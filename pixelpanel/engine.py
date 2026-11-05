"""Runs in a background thread: keeps the panel connected and decides what it shows.

Priority: a message sent from "Show now" > Teams status light > page rotation.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import sys
import threading
import time
import traceback
import urllib.error
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import api, ble, calendars, claude_hooks, m365, pages, sources, transitions, work
from .core import effects
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
FOCUS_PAGE = {"id": "_focus", "type": "focus", "duration": 8, "enabled": True, "label": "Focus timer"}
STOPWATCH_PAGE = {"id": "_stopwatch", "type": "stopwatch", "duration": 8, "enabled": True, "label": "Stopwatch"}
MESSAGE_LABEL = "Door sign / message"
PEEK_SECONDS = 5.0  # the stopwatch shows up this long when it is started or stopped
ON_AIR_PAGE = {"id": "_on_air", "type": "on_air"}
NO_BARS = {"attention", "alert", "on_air"}  # pages that want the whole panel
ATTENTION_TAKEOVER = {"id": "_attention", "type": "attention", "pull": True}
ATTENTION_PAGE = {"id": "_attention_page", "type": "attention", "duration": 8, "enabled": True,
                  "label": "Claude needs attention"}


def _clock() -> float:
    """Monotonic seconds; a function of its own so tests can drive the engine's time."""
    return time.monotonic()

REMINDER_PAGE = {"id": "_reminder", "type": "calendar", "remind": True}
FALLBACK_PAGE = {"id": "_fallback_clock", "type": "clock", "color": "#ffffff", "effect": "none",
                 "seconds": False, "weekday_bar": True}


@dataclass
class Status:
    connection: str = "Not connected"  # human readable
    connected: bool = False
    showing: str = ""
    teams: str | None = None
    claude: sources.ClaudeUsage | None = None
    weather: sources.Weather | None = None
    meeting: m365.Meeting | None = None
    calendar: str = ""  # account or problem, for the settings tab
    attention: str = ""  # state of the Claude session alerts, for the settings tab
    on_air: str = ""
    github: str = ""
    monitors: str = ""
    team: str = ""
    api: str = ""
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
        self._sync_calendar_view()
        self.tracker = claude_hooks.Tracker()
        self._attention_until = 0.0
        self._attention_error = ""
        self.focus = work.FocusTimer()
        self.stopwatch = work.Stopwatch()
        self._break = work.BreakTracker()
        self._limit = work.LimitWatch()
        self._peek_until = 0.0
        self._peek_seconds = PEEK_SECONDS
        self.last_tick = time.monotonic()
        self._reported_stuck = False
        self._alerts: list[tuple[dict, float]] = []
        self._alert: dict | None = None
        self._alert_until = 0.0
        self._alert_count = 0
        self._next_poll: dict[str, float] = {}
        self._polling: set[str] = set()
        self._api: api.LocalApi | None = None
        self._api_error = ""
        self._seen_issues: set[str] | None = None
        self._hook_server: claude_hooks.HookServer | None = None
        self._applied: dict = {}
        self._stop = False
        self._wake_event: asyncio.Event | None = None

    # --- public, called from the GUI thread ------------------------------------------------------

    def start(self) -> None:
        self._thread.start()
        self._sync_hook_server()
        self._sync_api()

    def stop(self) -> None:
        self._stop = True
        if self._hook_server is not None:
            self._hook_server.stop()
            self._hook_server = None
        if self._api is not None:
            self._api.stop()
            self._api = None
        if self._loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self.client.disconnect(), self._loop)
            try:
                fut.result(5)
            except Exception:  # noqa: BLE001
                pass
        self._wake()
        self._thread.join(5)

    def update_config(self, cfg: dict) -> None:
        for name, section in (("github", "github"), ("monitors", "monitors"), ("team", "team"), ("on_air", "on_air")):
            if cfg.get(section) != self.config.get(section):
                self._next_poll.pop(name, None)  # only a source whose settings changed is asked again
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
        self._sync_calendar_view()
        self._sync_hook_server()
        self._sync_api()
        self._next_connect = 0
        self._wake()

    # --- Claude session alerts --------------------------------------------------------------------

    # --- work features ----------------------------------------------------------------------------

    def push_alert(self, title: str, text: str, rgb=pages.AMBER, seconds: float = 15) -> None:
        """Clawd brings a note onto the panel; several notes wait for their turn."""
        self._alert_count += 1
        self._alerts.append(({"id": f"_alert{self._alert_count}", "type": "alert", "pull": True, "title": title,
                              "text": text, "rgb": rgb}, seconds))
        self._wake()

    def focus_toggle(self) -> None:
        if self.focus.phase:
            self.focus.stop()
        else:
            self.focus.start(_clock(), int(self.config["focus"].get("minutes", 25)))
            self._message = None  # the last action wins: starting the timer takes a door sign down
        self._shown = _Shown()
        self._wake()

    def stopwatch_toggle(self) -> None:
        self.stopwatch.toggle(_clock())
        self._peek_until = _clock() + self._peek_seconds
        self._shown = _Shown()
        self._wake()

    def report_if_stuck(self, seconds: float = 30) -> bool:
        """The engine's loop should tick all the time; if not, write down where it hangs."""
        stuck = time.monotonic() - self.last_tick > seconds
        if stuck and not self._reported_stuck:
            self._reported_stuck = True
            frame = sys._current_frames().get(self._thread.ident)
            where = "".join(traceback.format_stack(frame)[-6:]) if frame else "?"
            tasks = []
            if self._loop.is_running():
                for task in asyncio.all_tasks(self._loop):
                    tasks.extend(f"{f.f_code.co_name}:{f.f_lineno}" for f in task.get_stack(limit=8))
            log.error("panel control stuck for %.0f s at:\n%s\ntasks: %s", time.monotonic() - self.last_tick, where, tasks)
        elif not stuck:
            self._reported_stuck = False
        return stuck

    def stopwatch_reset(self) -> None:
        self.stopwatch.reset()
        self._shown = _Shown()
        self._wake()

    def _sync_api(self) -> None:
        cfg = self.config.get("api") or {}
        wanted = bool(cfg.get("enabled") and cfg.get("token"))
        if wanted and self._api is None:
            try:
                self._api = api.LocalApi(cfg["token"], self.show_message, self.clear_message, self.next_page,
                                         self._api_status, effects.EFFECTS)
                self._api.start()
                self._api_error = ""
            except OSError as err:
                log.warning("local API: %s", err)
                self._api_error = f"Port {api.PORT} is in use"
        elif not wanted and self._api is not None:
            self._api.stop()
            self._api = None

    def _api_status(self) -> dict:
        return {"connected": self.status.connected, "showing": self.status.showing, "teams": self.ctx.teams,
                "on_air": self.ctx.on_air, "focus": (self.focus.phase or "")}

    def _poll(self, name: str, every: float, now_m: float, fn, apply, *args) -> None:
        """Run a slow source in a worker thread, so it never holds up the panel."""
        if name in self._polling or now_m < self._next_poll.get(name, 0):
            return
        self._next_poll[name] = now_m + every
        self._polling.add(name)

        async def run() -> None:
            try:
                apply(await asyncio.to_thread(fn, *args))
                self._publish()
            except Exception as err:  # noqa: BLE001 - a source must never stop the panel
                log.warning("%s: %s", name, err)
                apply_error = getattr(self, f"_{name}_failed", None)
                if apply_error:
                    apply_error(err)
                    self._publish()
            finally:
                self._polling.discard(name)
                self._wake()

        asyncio.ensure_future(run())

    def _work_sources(self, cfg: dict, now_m: float) -> None:
        wanted = {p["type"] for p in cfg["pages"] if p.get("enabled")}
        air = cfg.get("on_air") or {}
        if air.get("enabled"):
            self._poll("on_air", 2, now_m, lambda: work.on_air_users(air.get("mic", True), air.get("camera", True)),
                       self._set_on_air)
        elif self.ctx.on_air or self.status.on_air:
            self.ctx.on_air, self.status.on_air = False, ""
        if "system" in wanted:
            self._poll("system", 5, now_m, work.system_stats, self._set("system"))
        if "mail" in wanted:
            self._poll("mail", 120, now_m, calendars.outlook_unread, self._set("mail"))
        gh = cfg.get("github") or {}
        repos = [r.strip() for r in (gh.get("repos") or "").replace(",", "\n").splitlines() if r.strip()]
        if gh.get("enabled") and (repos or gh.get("user")):
            self._poll("github", max(60, int(gh.get("interval", 300))), now_m, work.fetch_github, self._set_github,
                       repos, (gh.get("user") or "").strip(), work.unprotect(gh.get("token", "")))
        mon = cfg.get("monitors") or {}
        targets = work.parse_targets(mon.get("targets", ""))
        if mon.get("enabled") and targets:
            self._poll("monitors", max(15, int(mon.get("interval", 60))), now_m, work.check_targets, self._set_monitors, targets)
        team = cfg.get("team") or {}
        if team.get("enabled") and team.get("source"):
            self._poll("team", max(30, int(team.get("interval", 300))), now_m, work.read_team_message, self._set_team, team["source"])
        pause = cfg.get("break") or {}
        if pause.get("enabled") and now_m >= self._next_poll.get("break", 0):
            self._next_poll["break"] = now_m + 30
            self._break.minutes = int(pause.get("minutes", 90))
            if self._break.update(now_m, work.idle_seconds()):
                self.push_alert("Break!", "Time to stand up", pages.GREEN)
        ended = self.focus.update(now_m, int(cfg["focus"].get("break", 5)))
        if ended == "focus":
            self.push_alert("Well done", "Focus time is over", pages.GREEN)
        elif ended == "break":
            self.push_alert("Break over", "Ready for more?", pages.AMBER)
        if ended:
            self._shown = _Shown()
        unread = self.teams.unread
        if unread != self.ctx.teams_unread:
            self.ctx.teams_unread = unread
        self.ctx.sessions = self.tracker.sessions()

    def _set(self, name: str):
        def apply(value) -> None:
            setattr(self.ctx, name, value)
        return apply

    def _set_on_air(self, users: list[str]) -> None:
        self.status.on_air = f"In use by: {', '.join(users)}" if users else "Not in use"
        if bool(users) != self.ctx.on_air:
            log.info("on air: %s", users)
            self.ctx.on_air = bool(users)

    def _set_github(self, state: work.GitHubState) -> None:
        old, self.ctx.github = self.ctx.github, state
        self.status.github = state.error or (f"CI: {state.ci} {state.ci_repo}".strip()
                                             + f" – {state.prs} PRs to review, {state.issues} open issues")
        if state.error:
            return
        gh = self.config.get("github") or {}
        if gh.get("alert_ci", True) and state.ci == "failing" and (old is None or old.ci != "failing"):
            self.push_alert("CI failed", state.ci_repo.rsplit("/", 1)[-1], pages.RED)
        keys = set(state.issue_keys)
        if self._seen_issues is not None and gh.get("alert_issues", True):
            for key in sorted(keys - self._seen_issues):
                self.push_alert("New issue", key.rsplit("/", 1)[-1], pages.AMBER)
        self._seen_issues = keys

    def _github_failed(self, err: Exception) -> None:
        self.status.github = f"GitHub: {err}"

    def _set_monitors(self, monitors: tuple[work.Monitor, ...]) -> None:
        old = {m.name: m.up for m in self.ctx.monitors or ()}
        self.ctx.monitors = monitors
        down = [m.name for m in monitors if not m.up]
        self.status.monitors = f"Down: {', '.join(down)}" if down else f"All {len(monitors)} reachable"
        if (self.config.get("monitors") or {}).get("alert", True):
            for m in monitors:
                if not m.up and old.get(m.name, True):
                    self.push_alert("DOWN", m.name, pages.RED)

    def _set_team(self, text: str) -> None:
        old, self.ctx.team_message = self.ctx.team_message, text
        self.status.team = f"Now: {text}" if text else "The source is empty"
        if text and text != old:
            self.push_alert("Team", text, pages.AMBER, 20)

    def _team_failed(self, err: Exception) -> None:
        self.status.team = f"Cannot read the source: {err}"

    def _sync_calendar_view(self) -> None:
        cal = self.config.get("calendar", {})
        self.ctx.calendar_show = cal.get("show", "details")
        self.ctx.calendar_icon = bool(cal.get("icon", True))

    def _attention_cfg(self) -> dict:
        return self.config["claude"].get("attention") or {}

    def _sync_hook_server(self) -> None:
        """The local server runs exactly while the alerts are switched on."""
        cfg = self._attention_cfg()
        wanted = bool(cfg.get("enabled") and cfg.get("token"))
        if wanted and self._hook_server is None:
            try:
                self._hook_server = claude_hooks.HookServer(cfg["token"], self._on_hook_event)
                self._hook_server.start()
                self._attention_error = ""
            except OSError as err:
                log.warning("Claude hook server: %s", err)
                self._attention_error = f"Port {claude_hooks.PORT} is in use – alerts cannot be received"
        elif not wanted and self._hook_server is not None:
            self._hook_server.stop()
            self._hook_server = None
        if not wanted and self.tracker.pending:
            self.tracker.pending.clear()
            self.ctx.attention = None

    def _on_hook_event(self, event: dict) -> None:
        """Called on the server's thread; the engine state belongs to the loop's thread."""
        self._loop.call_soon_threadsafe(self.attention_event, event)

    def attention_event(self, event: dict, now: float | None = None) -> None:
        cfg = self._attention_cfg()
        if not cfg.get("enabled"):
            return
        now = _clock() if now is None else now
        kinds = {k for k in ("input", "idle", "error") if cfg.get(k, True)}
        new = self.tracker.apply(event, now, kinds)
        summary = self.tracker.summary()
        if summary != self.ctx.attention:
            log.info("Claude sessions: %s", summary)
            self.ctx.attention = summary
            self._publish()
        if new:
            self._attention_until = now + max(3, int(cfg.get("seconds", 15)))
        self._wake()

    def attention_test(self) -> None:
        """A pretend session asks for attention and goes away again (button in the settings)."""
        event = {"hook_event_name": "Notification", "notification_type": "permission_prompt",
                 "session_id": "_test", "cwd": "Test"}
        seconds = max(3, int(self._attention_cfg().get("seconds", 15)))
        self._loop.call_soon_threadsafe(self.attention_event, event)
        self._loop.call_soon_threadsafe(self._loop.call_later, seconds + 3, self.attention_event,
                                        {"hook_event_name": "SessionEnd", "session_id": "_test"})

    def show_message(self, page: dict, seconds: float | None) -> None:
        self._message = Message(copy.deepcopy(page), time.monotonic() + seconds if seconds else None)
        self._shown = _Shown()
        self._wake()

    def clear_message(self) -> None:
        self._message = None
        self._shown = _Shown()
        self._wake()

    def next_page(self) -> None:
        self._message = None  # the last action wins: going on takes a door sign down
        self._shown = _Shown()
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
                raise ValueError("Enter the application (client) ID first")
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
            self.status.calendar = "Signed out"
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
        self.status.attention = self._attention_text()
        self.status.api = self._api_error or ("Listening" if self._api is not None else "")
        self.on_status(copy.copy(self.status))

    def _attention_text(self) -> str:
        if not self._attention_cfg().get("enabled"):
            return ""
        if self._attention_error:
            return self._attention_error
        s = self.ctx.attention
        if s is None:
            return "Listening – no session needs you right now"
        return f"{s.count} session(s) waiting, latest: {s.project} ({s.kind})"

    async def _tick(self) -> None:
        self.last_tick = time.monotonic()
        now_m = _clock()
        if self.tracker.purge(now_m):
            self.ctx.attention = self.tracker.summary()
            self._publish()
        cfg = self.config
        changed = await self._update_sources(cfg, now_m)
        self._work_sources(cfg, now_m)
        if self.focus.phase:
            self.ctx.focus = (self.focus.phase, self.focus.remaining(_clock()), self.focus.length)
        else:
            self.ctx.focus = None
        self.ctx.stopwatch = (self.stopwatch.elapsed(_clock()), self.stopwatch.running,
                              (cfg.get("stopwatch") or {}).get("label", "Project"))
        await self._ensure_connection(cfg, now_m)
        if self.client.connected:
            await self._apply_settings(cfg)
        # fresh time: sources and connecting may have taken seconds of this tick
        target, label, light = self._choose(cfg, _clock())
        await self._show(target, label, light)
        if changed:
            self._publish()

    def _set_meetings(self, meetings: list[m365.Meeting]) -> None:
        now = datetime.now(timezone.utc)
        self.ctx.meeting = m365.pick_next(meetings, now)
        self.ctx.day = work.day_stats(meetings, now)

    def _check_limits(self, cfg: dict) -> None:
        limit = cfg.get("limit") or {}
        usage = self.ctx.claude
        if not limit.get("enabled") or usage is None or usage.error:
            return
        numbers = [int(n) for n in str(limit.get("thresholds", "")).replace(",", " ").split() if n.isdigit()]
        self._limit.thresholds = sorted(numbers) or [80, 90]
        for window, pct in self._limit.update(usage):
            reset = usage.session_reset if window == "5h" else usage.week_reset
            when = f"resets {pages.WEEKDAYS[reset.weekday()]} {reset:%H:%M}" if reset else ""
            self.push_alert(f"{window} {pct}%", when or "Claude limit", pages.RED if pct >= 90 else pages.AMBER)

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
            self._check_limits(cfg)
        cal_cfg = cfg.get("calendar", {})
        provider = cal_cfg.get("provider", "outlook")
        ready = {"graph": cal_cfg.get("client_id"), "ics": cal_cfg.get("ics_url")}.get(provider, True)
        if cal_cfg.get("enabled") and ready and now_m >= self._next_calendar:
            self._next_calendar = now_m + CALENDAR_EVERY
            changed = True
            try:
                if provider == "outlook":
                    self._set_meetings(await asyncio.to_thread(calendars.fetch_outlook))
                    self.status.calendar = "Reading the calendar from Outlook on this PC"
                elif provider == "ics":
                    self._set_meetings(await asyncio.to_thread(calendars.fetch_ics, cal_cfg["ics_url"]))
                    self.status.calendar = "Reading the calendar link"
                else:
                    cal = self._get_calendar()
                    self._set_meetings(await asyncio.to_thread(cal.fetch))
                    self.status.calendar = f"Signed in as {await asyncio.to_thread(cal.account)}"
            except PermissionError:
                self.ctx.meeting, self.status.calendar = None, "Not signed in – click Sign in"
            except urllib.error.HTTPError as err:
                self.status.calendar = f"Calendar: HTTP {err.code}" + (" – permission missing?" if err.code == 403 else "")
                self._next_calendar = now_m + 120
            except Exception as err:  # noqa: BLE001 - network, MSAL configuration
                log.warning("calendar: %s", err)
                self.status.calendar = f"Calendar: {err}"
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
        self.status.connection = f"Connecting to {cfg.get('name') or address} ..."
        self._publish()
        try:
            await self.client.connect(address)
            self.status.error = None
            self._connection_changed(cfg)  # now, not a tick later (that would forget the first picture)
        except Exception as err:  # noqa: BLE001 - bleak raises many kinds
            log.warning("connect failed: %s", err)
            self.status.connection = "Panel not reachable – retrying"
            self.status.error = str(err) or err.__class__.__name__
            self._publish()

    def _connection_changed(self, cfg: dict) -> None:
        connected = self.client.connected
        self.status.connected = connected
        name = cfg.get("name") or cfg.get("address")
        self.status.connection = f"Connected to {name}" if connected else "Disconnected"
        self._applied = {"lat": self._applied.get("lat"), "calendar": self._applied.get("calendar")}
        if connected:  # connect() set the clock already
            self._applied["time_synced"] = datetime.now().date()
        self._shown = _Shown()
        self._panel_img, self._panel_page = None, None  # unknown picture: no transition from it
        self._publish()

    def _power_on(self, cfg: dict) -> bool:
        """The panel's switch, and off during the night when that is asked for."""
        night = cfg.get("night") or {}
        if night.get("enabled") and night.get("off") and work.in_time_range(
                night.get("start", "22:00"), night.get("end", "07:00"), datetime.now()):
            return False
        return bool(cfg.get("power", True))

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
        power = self._power_on(cfg)
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
        now = datetime.now()
        pages_on = [p for p in cfg["pages"] if p.get("enabled") and work.in_schedule(p.get("schedule"), now)]
        types = {p["type"] for p in pages_on}
        if self.stopwatch.running and "stopwatch" not in types:  # a running timer never hides
            pages_on = [*pages_on, STOPWATCH_PAGE]
        if self.focus.phase and "focus" not in types:
            pages_on = [*pages_on, FOCUS_PAGE]
        return [*pages_on, ATTENTION_PAGE] if self.ctx.attention is not None else pages_on

    def _choose(self, cfg: dict, now_m: float) -> tuple[dict | None, str, bool]:
        # Who gets the panel: short notes, on air, stopwatch feedback, your own sign or message, meeting
        # reminder, focus timer, Teams status light, rotation.
        if self.ctx.attention is not None and now_m < self._attention_until:
            return ATTENTION_TAKEOVER, "Claude needs attention", False
        if self._alert is not None and now_m >= self._alert_until:
            self._alert = None
        if self._alert is None and self._alerts:
            self._alert, seconds = self._alerts.pop(0)
            self._alert_until = now_m + seconds
        if self._alert is not None:
            return self._alert, f"Note: {self._alert['title']}", False
        if self.ctx.on_air:
            return ON_AIR_PAGE, "On air", False
        if now_m < self._peek_until:
            return STOPWATCH_PAGE, "Stopwatch", False
        msg = self._message
        if msg is not None:
            if msg.until is not None and now_m >= msg.until:
                self._message = None
                self._shown = _Shown()
            else:
                return msg.page, MESSAGE_LABEL, False
        cal = cfg.get("calendar", {})
        meeting = self.ctx.meeting
        if cal.get("enabled") and meeting and int(cal.get("remind_minutes", 0)) > 0:
            left = (meeting.start - datetime.now(timezone.utc)).total_seconds()
            if 0 <= left <= int(cal["remind_minutes"]) * 60:
                return REMINDER_PAGE, "Meeting reminder", False
        if self.focus.phase and cfg["focus"].get("takeover", True):
            return FOCUS_PAGE, "Focus timer", False
        teams = cfg["teams"]
        if teams.get("enabled", True) and teams.get("status_light") and self.ctx.teams in teams.get("light_states", []):
            return {"id": "_teams_light", "type": "teams"}, "Teams status light", True
        rotation = self._rotation(cfg)
        if not rotation:  # never leave the last picture (e.g. an old status light) standing
            return FALLBACK_PAGE, "Clock (no page enabled)", False
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
        return page, page.get("label") or PAGE_TYPES.get(page["type"], page["type"]), False

    async def _transition(self, old, new, orientation: str) -> None:
        """Play the configured transition; returns when it has reached the new picture."""
        kind = self.config.get("transition", "none")
        anim = await asyncio.to_thread(transitions.build, kind, old, new)
        if anim is not None:
            await self._play(anim, kind, orientation)

    async def _play(self, anim: Rendered, kind: str, orientation: str) -> None:
        """Send a one-shot animation; returns when it has reached its held last frame."""
        data, is_gif = await asyncio.to_thread(render.encode, render.to_panel(anim, orientation))
        t0 = time.monotonic()
        await self.client.send_image(data, is_gif)
        log.info("transition %s: %s bytes in %.1f s", kind, len(data), time.monotonic() - t0)
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
        if not light and page["type"] not in NO_BARS and teams.get("enabled", True) and teams.get("status_frame") \
                and self.ctx.teams:
            frame = sources.teams_label(self.ctx.teams)[1]
            bars = max(1, min(3, int(teams.get("bar_width", 2))))
        key = pages.signature(page, self.ctx, now) + (light, frame, bars, self.config.get("orientation"))
        still_valid = self._shown.valid_until is None or now < self._shown.valid_until
        send = self.client.connected and self._power_on(self.config) and (key != self._shown.key or not still_valid)
        need_preview = key != self._preview_key or (send and not still_valid)
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
                        if page.get("pull"):  # Clawd pulls the note in
                            pull = await asyncio.to_thread(pages.attention_pull, self._panel_img, out.frames[0])
                            await self._play(pull, "clawd pull", orientation)
                        else:
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
                self.status.error = f"Sending failed: {err or err.__class__.__name__}"
                self._publish()
                if isinstance(err, TimeoutError):  # the panel does not answer: connect again
                    await self.client.disconnect()
        if self.status.showing != label:
            log.info("showing: %s", label)
            self.status.showing = label
            self._publish()
