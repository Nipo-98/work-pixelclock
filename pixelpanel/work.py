"""Everyday work features: on air, timers, break reminder, limits, GitHub, monitors, profiles.

The logic classes take the time as an argument, so they can be tested without waiting.
"""

from __future__ import annotations

import base64
import ctypes
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import winreg
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .m365 import Meeting

log = logging.getLogger(__name__)

USER_AGENT = "PixelPanel"
WORKDAY_MINUTES = 8 * 60


# --- on air ----------------------------------------------------------------------------------------

_CONSENT = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"


def _app_keys(root: str):
    """Yield the registry keys of all apps below a ConsentStore capability."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root) as key:
        for i in range(winreg.QueryInfoKey(key)[0]):
            name = winreg.EnumKey(key, i)
            if name == "NonPackaged":
                yield from _app_keys(f"{root}\\{name}")
            else:
                yield f"{root}\\{name}"


def capability_in_use(capability: str) -> bool:
    """Windows notes per app when it last used the mic/camera; stop time 0 means: right now."""
    try:
        for path in _app_keys(f"{_CONSENT}\\{capability}"):
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as app:
                try:
                    if winreg.QueryValueEx(app, "LastUsedTimeStop")[0] == 0 \
                            and winreg.QueryValueEx(app, "LastUsedTimeStart")[0] != 0:
                        return True
                except OSError:
                    continue
    except OSError:
        pass
    return False


def on_air(mic: bool = True, camera: bool = True) -> bool:
    return (mic and capability_in_use("microphone")) or (camera and capability_in_use("webcam"))


# --- PC --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SystemStats:
    cpu: int
    ram: int
    battery: int | None = None
    charging: bool = False


def system_stats() -> SystemStats:
    import psutil

    battery = psutil.sensors_battery()
    return SystemStats(round(psutil.cpu_percent(interval=0.5)), round(psutil.virtual_memory().percent),
                       round(battery.percent) if battery else None, bool(battery and battery.power_plugged))


class _LastInput(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input on this PC."""
    info = _LastInput(ctypes.sizeof(_LastInput), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


class BreakTracker:
    """Asks for a break after `minutes` at the PC; being away for `reset_after` counts as a break."""

    def __init__(self, minutes: int = 90, reset_after: float = 300) -> None:
        self.minutes, self.reset_after = minutes, reset_after
        self.since: float | None = None

    def update(self, now: float, idle: float) -> bool:
        if idle >= self.reset_after:
            self.since = None
            return False
        if self.since is None:
            self.since = now
        if now - self.since >= self.minutes * 60:
            self.since = now  # next reminder after another full period
            return True
        return False


class LimitWatch:
    """One warning per threshold and window; it re-arms when the usage has dropped (window reset)."""

    def __init__(self, thresholds: tuple[int, ...] = (80, 90)) -> None:
        self.thresholds = sorted(thresholds)
        self.warned: dict[str, int] = {}

    def update(self, usage) -> list[tuple[str, int]]:
        alerts = []
        for name, pct in (("5h", usage.session), ("Week", usage.week)):
            if pct is None:
                continue
            if pct < self.warned.get(name, 0):
                self.warned[name] = 0
            hit = max((t for t in self.thresholds if pct >= t), default=0)
            if hit > self.warned.get(name, 0):
                self.warned[name] = hit
                alerts.append((name, round(pct)))
        return alerts


# --- timers ----------------------------------------------------------------------------------------

class FocusTimer:
    """Focus phase, then a break, then done. update() names a phase that has just ended."""

    def __init__(self) -> None:
        self.phase: str | None = None  # focus | break
        self.ends = 0.0
        self.length = 0.0

    def start(self, now: float, focus_minutes: int) -> None:
        self.phase, self.length, self.ends = "focus", focus_minutes * 60.0, now + focus_minutes * 60.0

    def stop(self) -> None:
        self.phase = None

    def remaining(self, now: float) -> float:
        return max(0.0, self.ends - now)

    def update(self, now: float, break_minutes: int) -> str | None:
        if self.phase is None or now < self.ends:
            return None
        ended, self.phase = self.phase, None
        if ended == "focus" and break_minutes > 0:
            self.phase, self.length, self.ends = "break", break_minutes * 60.0, now + break_minutes * 60.0
        return ended


class Stopwatch:
    def __init__(self) -> None:
        self.running_since: float | None = None
        self.total = 0.0

    @property
    def running(self) -> bool:
        return self.running_since is not None

    def toggle(self, now: float) -> None:
        if self.running_since is None:
            self.running_since = now
        else:
            self.total += now - self.running_since
            self.running_since = None

    def reset(self) -> None:
        self.running_since, self.total = None, 0.0

    def elapsed(self, now: float) -> float:
        return self.total + (now - self.running_since if self.running_since is not None else 0.0)


def clock_text(seconds: float) -> str:
    """M:SS, or H:MM:SS from one hour on."""
    s = max(0, int(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def countdown_text(date: str, time_text: str, now: datetime) -> str | None:
    """Time left until a date (or today, when empty) at a time; None when it has passed.

    Far away: days. Today or tomorrow: H:MM, or minutes in the last hour."""
    try:
        hour, minute = (int(x) for x in (time_text or "00:00").split(":"))
        day = datetime.strptime(date, "%Y-%m-%d").date() if date else now.date()
    except ValueError:
        return "?"
    left = datetime(day.year, day.month, day.day, hour, minute) - now.replace(tzinfo=None)
    if left <= timedelta(0):
        return None
    minutes = -(-int(left.total_seconds()) // 60)
    if minutes >= 48 * 60:
        return f"{minutes // (24 * 60)} days"
    if minutes >= 60:
        return f"{minutes // 60}:{minutes % 60:02d} h"
    return f"{minutes} min"


# --- schedules and profiles ------------------------------------------------------------------------

def in_time_range(start: str, end: str, now: datetime) -> bool:
    """Is now in [start, end)? A range over midnight (22:00 to 07:00) works too."""
    t = now.strftime("%H:%M")
    return start <= t < end if start <= end else (t >= start or t < end)


def in_schedule(schedule: dict | None, now: datetime) -> bool:
    """A page without an enabled schedule is always on."""
    if not schedule or not schedule.get("enabled"):
        return True
    if schedule.get("weekdays_only") and now.weekday() >= 5:
        return False
    return in_time_range(schedule.get("from", "00:00"), schedule.get("to", "23:59"), now)


def save_profile(cfg: dict, name: str, auto_from: str = "", auto_to: str = "") -> None:
    """Remember the ticked pages, brightness and transition under a name."""
    profile = {"name": name, "page_ids": [p["id"] for p in cfg["pages"] if p.get("enabled")],
               "brightness": cfg.get("brightness", 60), "transition": cfg.get("transition", "push_left"),
               "auto_from": auto_from, "auto_to": auto_to}
    cfg["profiles"] = [p for p in cfg.get("profiles", []) if p["name"] != name] + [profile]


def apply_profile(cfg: dict, name: str) -> bool:
    profile = next((p for p in cfg.get("profiles", []) if p["name"] == name), None)
    if profile is None:
        return False
    for page in cfg["pages"]:
        page["enabled"] = page["id"] in profile["page_ids"]
    cfg["brightness"] = profile.get("brightness", cfg.get("brightness", 60))
    cfg["transition"] = profile.get("transition", cfg.get("transition", "push_left"))
    cfg["active_profile"] = name
    return True


def profile_for_time(cfg: dict, now: datetime) -> str | None:
    """The first profile whose automatic time range contains now."""
    for profile in cfg.get("profiles", []):
        if profile.get("auto_from") and profile.get("auto_to") \
                and in_time_range(profile["auto_from"], profile["auto_to"], now):
            return profile["name"]
    return None


# --- calendar day ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class DayStats:
    left: int  # meetings today that have not ended yet
    total: int
    share: int  # % of an 8 hour day in meetings


def day_stats(meetings: list[Meeting], now: datetime) -> DayStats:
    today = now.astimezone().date()
    day = sorted((m for m in meetings if m.start.astimezone().date() == today), key=lambda m: m.start)
    busy, block_end = 0.0, None
    for m in day:  # overlapping meetings count once
        start = m.start if block_end is None or m.start > block_end else block_end
        if m.end > start:
            busy += (m.end - start).total_seconds() / 60
            block_end = m.end
    return DayStats(sum(1 for m in day if m.end > now), len(day), round(100 * busy / WORKDAY_MINUTES))


# --- secrets ---------------------------------------------------------------------------------------

def protect(text: str) -> str:
    """Encrypt for this Windows user (DPAPI), so a token is not in the settings file as plain text."""
    import win32crypt

    if not text:
        return ""
    return base64.b64encode(win32crypt.CryptProtectData(text.encode(), None, None, None, None, 0)).decode()


def unprotect(blob: str) -> str:
    import pywintypes
    import win32crypt

    if not blob:
        return ""
    try:
        return win32crypt.CryptUnprotectData(base64.b64decode(blob), None, None, None, 0)[1].decode()
    except (ValueError, pywintypes.error) as err:
        log.warning("stored token not readable: %s", err)
        return ""


# --- GitHub ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class GitHubState:
    ci: str = "none"  # none | passing | running | failing
    ci_repo: str = ""
    prs: int = 0
    issues: int = 0
    newest_issue: int = 0
    newest_issue_repo: str = ""
    issue_keys: tuple[str, ...] = ()
    error: str = ""


def _gh(path: str, token: str) -> dict | list:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"https://api.github.com{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def summarize_runs(runs_by_repo: dict[str, list[dict]]) -> tuple[str, str]:
    """Latest run per repo and workflow; a failure beats a running build beats passing."""
    rank = {"none": 0, "passing": 1, "running": 2, "failing": 3}
    best, best_repo = "none", ""
    for repo, runs in runs_by_repo.items():
        seen = set()
        for run in runs:
            workflow = run.get("workflow_id")
            if workflow in seen:
                continue
            seen.add(workflow)
            if run.get("status") != "completed":
                state = "running"
            elif run.get("conclusion") in ("success", "skipped", "neutral"):
                state = "passing"
            elif run.get("conclusion") == "cancelled":
                continue
            else:
                state = "failing"
            if rank[state] > rank[best]:
                best, best_repo = state, repo
    return best, best_repo


def fetch_github(repos: list[str], user: str, token: str) -> GitHubState:
    runs, keys, newest, newest_repo, prs = {}, [], 0, "", 0
    try:
        for repo in repos:
            runs[repo] = _gh(f"/repos/{repo}/actions/runs?per_page=20", token).get("workflow_runs", [])
            for item in _gh(f"/repos/{repo}/issues?state=open&per_page=100", token):
                if "pull_request" in item:
                    continue
                keys.append(f"{repo}#{item['number']}")
                if item["number"] > newest:
                    newest, newest_repo = item["number"], repo
        if user:
            q = urllib.parse.quote(f"is:open is:pr review-requested:{user} archived:false")
            prs = int(_gh(f"/search/issues?q={q}&per_page=1", token).get("total_count", 0))
    except urllib.error.HTTPError as err:
        hint = {401: "token not accepted", 403: "rate limit – add a token", 404: "repository not found – private ones need a token"}
        return GitHubState(error=f"GitHub: {hint.get(err.code, f'HTTP {err.code}')}")
    except (OSError, ValueError, KeyError, AttributeError) as err:
        return GitHubState(error=f"GitHub: no connection ({err.__class__.__name__})")
    ci, ci_repo = summarize_runs(runs)
    return GitHubState(ci, ci_repo, prs, len(keys), newest, newest_repo, tuple(keys))


# --- service monitors ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Monitor:
    name: str
    up: bool


def parse_targets(text: str) -> list[tuple[str, str]]:
    """One target per line: a URL, or 'name = URL'."""
    targets = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, url = line.partition(" = ") if " = " in line else ("", "", line)
        url = url.strip()
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        targets.append((name.strip() or (urllib.parse.urlsplit(url).hostname or url), url))
    return targets


def check_url(url: str, timeout: float = 6) -> bool:
    """Up = the server answers without a server error (a 401 or 404 still means it is alive)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError as err:
        return err.code < 500
    except (OSError, ValueError):
        return False


def check_targets(targets: list[tuple[str, str]]) -> tuple[Monitor, ...]:
    return tuple(Monitor(name, check_url(url)) for name, url in targets)


# --- team message ----------------------------------------------------------------------------------

def read_team_message(source: str) -> str:
    """First non-empty line of a URL or a file (e.g. on a network share), at most 200 characters."""
    source = source.strip()
    if source.lower().startswith(("http://", "https://")):
        req = urllib.request.Request(source, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read(64 * 1024).decode("utf-8", "replace")
    else:
        text = Path(source).read_text(encoding="utf-8", errors="replace")
    return next((line.strip() for line in text.splitlines() if line.strip()), "")[:200]
