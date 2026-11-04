"""Runs in a background thread: keeps the panel connected and decides what it shows.

Priority: a message sent from "Sofort anzeigen" > Teams status light > page rotation.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
import time
import urllib.error
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import ble, calendars, m365, pages, sources, transitions
from .config import PAGE_TYPES
from .core import protocol as proto
from .core import render
from .core.render import Rendered

log = logging.getLogger(__name__)

TICK = 0.5
RECONNECT_EVERY = 15.0
TEAMS_EVERY = 2.0
WEATHER_EVERY = 15 * 60
CALENDAR_EVERY = 5 * 60


def _clock() -> float:
    """Monotonic seconds; a function of its own so tests can drive the engine's time."""
    return time.monotonic()

REMINDER_PAGE = {"id": "_reminder", "type": "calendar", "remind": True}
FALLBACK_PAGE = {"id": "_fallback_clock", "type": "clock", "color": "#ffffff", "effect": "none",
                 "seconds": False, "weekday_bar": True}


@dataclass
class Status:
    connection: str = "Nicht verbunden"  # human readable
    connected: bool = False
    showing: str = ""
    teams: str | None = None
    claude: sources.ClaudeUsage | None = None
    weather: sources.Weather | None = None
    meeting: m365.Meeting | None = None
    calendar: str = ""  # account or problem, for the settings tab
    error: str | None = None


@dataclass
class Message:
    """Shown instead of the rotation until `until` (None = until cleared)."""

    page: dict
    until: float | None
    rendered: Rendered | None = None


@dataclass
class _Shown:
    key: tuple | None = None
    valid_until: datetime | None = None


@dataclass
class Engine:
    config: dict
    on_status: Callable[[Status], None] = lambda s: None
    on_preview: Callable[[bytes, str, str], None] = lambda data, mime, label: None

    status: Status = field(default_factory=Status)
    ctx: pages.Context = field(default_factory=pages.Context)

    def __post_init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="panel", daemon=True)
        self.client = ble.PanelClient(on_disconnect=self._wake)
        self.teams = sources.TeamsWatcher()
        self._message: Message | None = None
        self._shown = _Shown()
        self._panel_img = None  # first frame of what the panel shows (view coordinates), for transitions
        self._panel_page: str | None = None
        self._preview_key: tuple | None = None
        self._rot_index = 0
        self._rot_started = 0.0
        self._rot_min = 0.0
        self._advance = False
        self._next_connect = 0.0
        self._next_teams = 0.0
        self._next_claude = 0.0
        self._next_weather = 0.0
        self._next_calendar = 0.0
        self._calendar: m365.Calendar | None = None
        self._calendar_ids: tuple[str, str] | None = None
        self._applied: dict = {}
        self._stop = False
        self._wake_event: asyncio.Event | None = None

    # --- public, called from the GUI thread ------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        if self._loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self.client.disconnect(), self._loop)
            try:
                fut.result(5)
            except Exception:  # noqa: BLE001
                pass
        self._wake()
        self._thread.join(5)

    def update_config(self, cfg: dict) -> None:
        self.config = copy.deepcopy(cfg)
        self._shown = _Shown()  # repaint with the new settings
        self._preview_key = None
        if cfg.get("weather", {}).get("lat") != self._applied.get("lat"):
            self._next_weather = 0
        cal = cfg.get("calendar", {})
        cal_key = (cal.get("enabled"), cal.get("provider"), cal.get("ics_url"), cal.get("client_id"), cal.get("tenant_id"))
        if cal_key != self._applied.get("calendar"):
            self._applied["calendar"] = cal_key
            self._next_calendar = 0  # source changed: fetch now
            if not cal.get("enabled"):
                self.ctx.meeting = None
        self._next_connect = 0
        self._wake()

    def show_message(self, page: dict, seconds: float | None) -> None:
        self._message = Message(copy.deepcopy(page), time.monotonic() + seconds if seconds else None)
        self._shown = _Shown()
        self._wake()

    def clear_message(self) -> None:
        self._message = None
        self._shown = _Shown()
        self._wake()

    def next_page(self) -> None:
        self._advance = True
        self._wake()

    def refresh_claude(self) -> None:
        self._next_claude = 0
        self._wake()

    def _get_calendar(self) -> m365.Calendar | None:
        cal = self.config.get("calendar", {})
        ids = (cal.get("client_id", "").strip(), cal.get("tenant_id", "").strip())
        if not ids[0]:
            return None
        if ids != self._calendar_ids:
            self._calendar, self._calendar_ids = m365.Calendar(*ids), ids
        return self._calendar

    def calendar_sign_in(self) -> Future:
        """Browser sign-in (blocks a worker thread, not the panel loop); result: account name."""
        async def _sign_in():
            cal = self._get_calendar()
            if cal is None:
                raise ValueError("Erst Client-ID eintragen")
            account = await asyncio.to_thread(cal.sign_in)
            self._next_calendar = 0
            self._wake()
            return account
        return self.submit(_sign_in())

    def calendar_sign_out(self) -> None:
        async def _out():
            if (cal := self._get_calendar()) is not None:
                await asyncio.to_thread(cal.sign_out)
            self.ctx.meeting = None
            self.status.calendar = "Abgemeldet"
            self._publish()
        self.submit(_out())

    def submit(self, coro) -> Future:
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def scan(self) -> Future:
        async def _scan():
            await self.client.disconnect()  # a connected panel does not advertise
            self._next_connect = time.monotonic() + 10
            return await ble.scan()
        return self.submit(_scan())

    def reconnect(self) -> None:
        """Connect to the configured panel now (switches panels when another one is connected)."""
        async def _re():
            if self.client.address != self.config.get("address") or not self.client.connected:
                await self.client.disconnect()
            self._next_connect = 0
        self.submit(_re())
        self._wake()

    # --- loop ------------------------------------------------------------------------------------

    def _wake(self) -> None:
        if self._wake_event is not None:
            self._loop.call_soon_threadsafe(self._wake_event.set)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self) -> None:
        self._wake_event = asyncio.Event()
        while not self._stop:
            try:
                await self._tick()
            except ConnectionError as err:  # panel went away mid-command; the next tick reconnects
                log.info("%s", err)
            except Exception as err:  # noqa: BLE001 - keep the loop alive
                log.exception("tick failed")
                self.status.error = str(err)
                self._publish()
            try:
                await asyncio.wait_for(self._wake_event.wait(), TICK)
            except TimeoutError:
                pass
            self._wake_event.clear()

    def _publish(self) -> None:
        self.status.teams = self.ctx.teams
        self.status.claude = self.ctx.claude
        self.status.weather = self.ctx.weather
        self.status.meeting = self.ctx.meeting
        self.on_status(copy.copy(self.status))

    async def _tick(self) -> None:
        now_m = _clock()
        cfg = self.config
        changed = await self._update_sources(cfg, now_m)
        await self._ensure_connection(cfg, now_m)
        if self.client.connected:
            await self._apply_settings(cfg)
        # fresh time: sources and connecting may have taken seconds of this tick
        target, label, light = self._choose(cfg, _clock())
        await self._show(target, label, light)
        if changed:
            self._publish()

    async def _update_sources(self, cfg: dict, now_m: float) -> bool:
        changed = False
        if cfg["teams"].get("enabled", True) and now_m >= self._next_teams:
            self._next_teams = now_m + TEAMS_EVERY
            state = await asyncio.to_thread(self.teams.poll)
            if state != self.ctx.teams:
                log.info("Teams: %s", state)
                self.ctx.teams, changed = state, True
        if cfg["claude"].get("enabled", True) and now_m >= self._next_claude:
            self._next_claude = now_m + max(30, int(cfg["claude"].get("interval", 120)))
            self.ctx.claude, changed = await asyncio.to_thread(sources.fetch_claude_usage), True
        cal_cfg = cfg.get("calendar", {})
        provider = cal_cfg.get("provider", "outlook")
        ready = {"graph": cal_cfg.get("client_id"), "ics": cal_cfg.get("ics_url")}.get(provider, True)
        if cal_cfg.get("enabled") and ready and now_m >= self._next_calendar:
            self._next_calendar = now_m + CALENDAR_EVERY
            changed = True
            try:
                if provider == "outlook":
                    self.ctx.meeting = await asyncio.to_thread(calendars.fetch_outlook)
                    self.status.calendar = "Liest den Kalender aus Outlook auf diesem PC"
                elif provider == "ics":
                    self.ctx.meeting = await asyncio.to_thread(calendars.fetch_ics, cal_cfg["ics_url"])
                    self.status.calendar = "Kalender-Link wird gelesen"
                else:
                    cal = self._get_calendar()
                    self.ctx.meeting = await asyncio.to_thread(cal.fetch)
                    self.status.calendar = f"Angemeldet als {await asyncio.to_thread(cal.account)}"
            except PermissionError:
                self.ctx.meeting, self.status.calendar = None, "Nicht angemeldet – „Anmelden“ klicken"
            except urllib.error.HTTPError as err:
                self.status.calendar = f"Kalender: HTTP {err.code}" + (" – Berechtigung fehlt?" if err.code == 403 else "")
                self._next_calendar = now_m + 120
            except Exception as err:  # noqa: BLE001 - network, MSAL configuration
                log.warning("calendar: %s", err)
                self.status.calendar = f"Kalender: {err}"
                self._next_calendar = now_m + 120
        wx = cfg.get("weather", {})
        if wx.get("lat") is not None and now_m >= self._next_weather:
            self._next_weather = now_m + WEATHER_EVERY
            self._applied["lat"] = wx.get("lat")
            try:
                self.ctx.weather = await asyncio.to_thread(sources.fetch_weather, wx["lat"], wx["lon"])
                changed = True
            except (OSError, ValueError, KeyError) as err:
                log.warning("weather: %s", err)
                self._next_weather = now_m + 120
        return changed

    async def _ensure_connection(self, cfg: dict, now_m: float) -> None:
        address = cfg.get("address")
        if self.client.connected != self.status.connected:
            self._connection_changed(cfg)
        if self.client.connected or not address or now_m < self._next_connect:
            return
        self._next_connect = now_m + RECONNECT_EVERY
        self.status.connection = f"Verbinde mit {cfg.get('name') or address} ..."
        self._publish()
        try:
            await self.client.connect(address)
            self.status.error = None
            self._connection_changed(cfg)  # now, not a tick later (that would forget the first picture)
        except Exception as err:  # noqa: BLE001 - bleak raises many kinds
            log.warning("connect failed: %s", err)
            self.status.connection = "Panel nicht erreichbar – neuer Versuch läuft"
            self.status.error = str(err) or err.__class__.__name__
            self._publish()

    def _connection_changed(self, cfg: dict) -> None:
        connected = self.client.connected
        self.status.connected = connected
        name = cfg.get("name") or cfg.get("address")
        self.status.connection = f"Verbunden mit {name}" if connected else "Getrennt"
        self._applied = {"lat": self._applied.get("lat"), "calendar": self._applied.get("calendar")}
        if connected:  # connect() set the clock already
            self._applied["time_synced"] = datetime.now().date()
        self._shown = _Shown()
        self._panel_img, self._panel_page = None, None  # unknown picture: no transition from it
        self._publish()

    def _brightness(self, cfg: dict) -> int:
        night = cfg.get("night", {})
        if night.get("enabled"):
            now = datetime.now().strftime("%H:%M")
            start, end = night.get("start", "22:00"), night.get("end", "07:00")
            inside = start <= now < end if start <= end else (now >= start or now < end)
            if inside:
                return int(night.get("brightness", 5))
        return int(cfg.get("brightness", 60))

    async def _apply_settings(self, cfg: dict) -> None:
        power = bool(cfg.get("power", True))
        if self._applied.get("power") != power:
            await self.client.command(proto.set_power(power))
            self._applied["power"] = power
        level = self._brightness(cfg)
        if self._applied.get("brightness") != level:
            await self.client.command(proto.set_brightness(level))
            self._applied["brightness"] = level
        day = datetime.now().date()
        if self._applied.get("time_synced") != day:  # the panel's clock drifts; resync daily
            await self.client.sync_time()
            self._applied["time_synced"] = day

    def _rotation(self, cfg: dict) -> list[dict]:
        return [p for p in cfg["pages"] if p.get("enabled")]

    def _choose(self, cfg: dict, now_m: float) -> tuple[dict | None, str, bool]:
        msg = self._message
        if msg is not None:
            if msg.until is not None and now_m >= msg.until:
                self._message = None
                self._shown = _Shown()
            else:
                return msg.page, "Sofort-Anzeige", False
        cal = cfg.get("calendar", {})
        meeting = self.ctx.meeting
        if cal.get("enabled") and meeting and int(cal.get("remind_minutes", 0)) > 0:
            left = (meeting.start - datetime.now(timezone.utc)).total_seconds()
            if 0 <= left <= int(cal["remind_minutes"]) * 60:
                return REMINDER_PAGE, "Termin-Erinnerung", False
        teams = cfg["teams"]
        if teams.get("enabled", True) and teams.get("status_light") and self.ctx.teams in teams.get("light_states", []):
            return {"id": "_teams_light", "type": "teams"}, "Teams-Statuslicht", True
        rotation = self._rotation(cfg)
        if not rotation:  # never leave the last picture (e.g. an old status light) standing
            return FALLBACK_PAGE, "Uhr (keine Seite aktiv)", False
        if self._rot_started == 0:
            self._rot_started = now_m
        elif self._advance or (len(rotation) > 1 and now_m - self._rot_started >= max(self._rot_min, 1)):
            self._rot_index += 1
            self._rot_started, self._rot_min = now_m, 0.0
        self._advance = False
        self._rot_index %= len(rotation)
        page = rotation[self._rot_index]
        if not self._rot_min:
            self._rot_min = float(page.get("duration", 10))
        return page, PAGE_TYPES.get(page["type"], page["type"]), False

    async def _transition(self, old, new, orientation: str) -> None:
        """Play the configured transition; returns when it has reached the new picture."""
        kind = self.config.get("transition", "none")
        anim = await asyncio.to_thread(transitions.build, kind, old, new)
        if anim is None:
            return
        data, is_gif = await asyncio.to_thread(render.encode, render.to_panel(anim, orientation))
        t0 = time.monotonic()
        await self.client.send_image(data, is_gif)
        log.info("Übergang %s: %s Bytes in %.1f s", kind, len(data), time.monotonic() - t0)
        await asyncio.sleep(transitions.play_ms(anim) / 1000)

    async def _show(self, page: dict | None, label: str, light: bool) -> None:
        if page is None:
            if self.status.showing != label:
                self.status.showing = label
                self._publish()
            return
        now = datetime.now()
        teams = self.config["teams"]
        frame, bars = None, 0
        if not light and teams.get("enabled", True) and teams.get("status_frame") and self.ctx.teams:
            frame = sources.teams_label(self.ctx.teams)[1]
            bars = max(1, min(3, int(teams.get("bar_width", 2))))
        key = pages.signature(page, self.ctx, now) + (light, frame, bars, self.config.get("orientation"))
        still_valid = self._shown.valid_until is None or now < self._shown.valid_until
        send = self.client.connected and self.config.get("power", True) and (key != self._shown.key or not still_valid)
        need_preview = key != self._preview_key
        if not send and not need_preview:
            return
        w, h = self.client.size
        orientation = self.config.get("orientation", "landscape")
        vw, vh = render.view_size(w, h, orientation)
        if light:
            out = render.render_screen(pages.teams_screen(self.ctx.teams, light=True), vw, vh)
        else:
            out = await asyncio.to_thread(pages.render_page, page, self.ctx, pages.inner_width(vw, bars), vh, now)
            if frame and isinstance(out, Rendered):  # (the built-in clock has no picture to frame)
                out = pages.with_status_bars(out, frame, bars)
        if isinstance(out, Rendered) and self._rot_min and out.scroll_ms and page is not None:
            # scrolling text stays at least one full pass
            self._rot_min = max(self._rot_min, out.scroll_ms / 1000)
        if need_preview:
            self._preview_key = key
            if isinstance(out, Rendered):
                data, mime = await asyncio.to_thread(pages.led_preview, out, 10)
                self.on_preview(data, mime, label)
            else:
                self.on_preview(b"", "native", out.label)
        if send:
            page_id = f"{page.get('id')}:{light}"
            try:
                if isinstance(out, pages.Native):
                    await self.client.command(out.data)
                else:
                    if page_id != self._panel_page and self._panel_img is not None:
                        await self._transition(self._panel_img, out.frames[0], orientation)
                    data, is_gif = await asyncio.to_thread(render.encode, render.to_panel(out, orientation))
                    await self.client.send_image(data, is_gif)
                    if page_id != self._panel_page and any(p is page for p in self._rotation(self.config)):
                        self._rot_started = _clock()  # the page's time starts once it is on the panel
                self._shown = _Shown(key, out.valid_until if isinstance(out, Rendered) else None)
                self._panel_img = out.frames[0] if isinstance(out, Rendered) else None
                self._panel_page = page_id
                if self.status.error:
                    self.status.error = None
                    self._publish()
            except Exception as err:  # noqa: BLE001
                log.warning("send failed: %s", err)
                self.status.error = f"Senden fehlgeschlagen: {err}"
        if self.status.showing != label:
            log.info("zeige: %s", label)
            self.status.showing = label
            self._publish()
