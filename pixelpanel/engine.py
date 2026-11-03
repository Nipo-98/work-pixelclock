"""Runs in a background thread: keeps the panel connected and decides what it shows.

Priority: a message sent from "Sofort anzeigen" > Teams status light > page rotation.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime

from . import ble, pages, sources
from .config import PAGE_TYPES
from .core import protocol as proto
from .core import render
from .core.render import Rendered

log = logging.getLogger(__name__)

TICK = 0.5
RECONNECT_EVERY = 15.0
TEAMS_EVERY = 2.0
WEATHER_EVERY = 15 * 60
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
        self._preview_key: tuple | None = None
        self._rot_index = 0
        self._rot_started = 0.0
        self._rot_min = 0.0
        self._advance = False
        self._next_connect = 0.0
        self._next_teams = 0.0
        self._next_claude = 0.0
        self._next_weather = 0.0
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
        self.on_status(copy.copy(self.status))

    async def _tick(self) -> None:
        now_m = time.monotonic()
        cfg = self.config
        changed = await self._update_sources(cfg, now_m)
        await self._ensure_connection(cfg, now_m)
        if self.client.connected:
            await self._apply_settings(cfg)
        target, label, light = self._choose(cfg, now_m)
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
        connected = self.client.connected
        if connected != self.status.connected:
            self.status.connected = connected
            self.status.connection = f"Verbunden mit {cfg.get('name') or address}" if connected else "Getrennt"
            self._applied = {"lat": self._applied.get("lat")}
            if connected:  # connecting set the clock already
                self._applied["time_synced"] = datetime.now().date()
            self._shown = _Shown()
            self._publish()
        if connected or not address or now_m < self._next_connect:
            return
        self._next_connect = now_m + RECONNECT_EVERY
        self.status.connection = f"Verbinde mit {cfg.get('name') or address} ..."
        self._publish()
        try:
            await self.client.connect(address)
            self._applied["time_synced"] = datetime.now().date()  # connect() set the clock
            self.status.error = None
        except Exception as err:  # noqa: BLE001 - bleak raises many kinds
            log.warning("connect failed: %s", err)
            self.status.connection = "Panel nicht erreichbar – neuer Versuch läuft"
            self.status.error = str(err) or err.__class__.__name__
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
            out = await asyncio.to_thread(pages.render_page, page, self.ctx, vw - 2 * bars, vh, now)
            if frame and isinstance(out, Rendered):  # (the built-in clock has no picture to frame)
                out = pages.with_status_bars(out, frame, bars)
        if isinstance(out, Rendered) and self._rot_min and out.scroll_ms and page is not None:
            # scrolling text stays at least one full pass
            self._rot_min = max(self._rot_min, out.scroll_ms / 1000)
        if need_preview:
            self._preview_key = key
            if isinstance(out, Rendered):
                data, mime = await asyncio.to_thread(render.preview, out)
                self.on_preview(data, mime, label)
            else:
                self.on_preview(b"", "native", out.label)
        if send:
            try:
                if isinstance(out, pages.Native):
                    await self.client.command(out.data)
                else:
                    data, is_gif = await asyncio.to_thread(render.encode, render.to_panel(out, orientation))
                    await self.client.send_image(data, is_gif)
                self._shown = _Shown(key, out.valid_until if isinstance(out, Rendered) else None)
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
