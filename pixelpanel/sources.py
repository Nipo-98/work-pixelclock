"""Data shown on the panel: Teams presence (from the Teams log), Claude plan usage, weather."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

USER_AGENT = "PixelPanel/1.0"

# --- Teams ---------------------------------------------------------------------------------------

TEAMS_LOG_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / \
    "Packages/MSTeams_8wekyb3d8bbwe/LocalCache/Microsoft/MSTeams/Logs"
PRESENCE_RE = re.compile(r"UserPresenceAction: \{[^}]*availability: (\w+)")

# state -> (label, short label that fits next to an icon on 64 px, colour)
TEAMS_STATES: dict[str, tuple[str, str, tuple[int, int, int]]] = {
    "Available": ("Available", "Free", (0, 200, 60)),
    "Busy": ("Busy", "Busy", (230, 20, 20)),
    "InACall": ("In a call", "In call", (230, 20, 20)),
    "InAMeeting": ("In a meeting", "Meeting", (230, 20, 20)),
    "OnThePhone": ("On the phone", "Phone", (230, 20, 20)),
    "Presenting": ("Presenting", "Present.", (230, 20, 20)),
    "DoNotDisturb": ("Don't disturb", "DND", (200, 0, 0)),
    "BeRightBack": ("Back soon", "BRB", (255, 190, 0)),
    "Away": ("Away", "Away", (255, 190, 0)),
    "Offline": ("Offline", "Offline", (110, 110, 120)),
}
GREY = (110, 110, 120)


def teams_label(state: str | None, short: bool = False) -> tuple[str, tuple[int, int, int]]:
    if not state:
        return ("Teams ?", GREY)
    label, short_label, color = TEAMS_STATES.get(state, (state, state, GREY))
    return (short_label if short else label, color)


UNREAD_LINE_RE = re.compile(r"BroadcastGlobalState: New Global State Event[^\n]*")
UNREAD_RE = re.compile(r"unread notification count: (\d+)")


def last_unread(text: str) -> int | None:
    """Unread count of the last global state in a chunk of Teams log text (summed over all accounts)."""
    lines = UNREAD_LINE_RE.findall(text)
    return sum(int(n) for n in UNREAD_RE.findall(lines[-1])) if lines else None


def last_presence(text: str) -> str | None:
    """Last availability in a chunk of Teams log text."""
    found = PRESENCE_RE.findall(text)
    found = [s for s in found if s != "PresenceUnknown"]
    return found[-1] if found else None


class TeamsWatcher:
    """Follows the newest Teams log file and remembers the last presence written to it."""

    def __init__(self, log_dir: Path = TEAMS_LOG_DIR) -> None:
        self.log_dir = log_dir
        self.state: str | None = None
        self.unread: int | None = None
        self._file: Path | None = None
        self._pos = 0

    def _logs(self) -> list[Path]:
        try:
            files = [p for p in self.log_dir.glob("MSTeams_*.log")]
        except OSError:
            return []
        # newest first; NTFS times are coarse (~15 ms), so equal times are decided by the name,
        # which carries the creation time (MSTeams_YYYY-MM-DD_hh-mm-ss.NN.log)
        return sorted(files, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)

    def poll(self) -> str | None:
        logs = self._logs()
        if not logs:
            return self.state
        newest = logs[0]
        if self._file is None:
            # first run: newest file that mentions a presence (logs rotate, the newest may be empty)
            for path in logs[:5]:
                text = _read(path, 0)[0]
                if self.unread is None:
                    self.unread = last_unread(text)
                state = last_presence(text)
                if state:
                    self.state = state
                    break
            self._file, self._pos = newest, _read(newest, 0)[1]
            return self.state
        if newest != self._file:
            self._file, self._pos = newest, 0
        text, self._pos = _read(newest, self._pos)
        if state := last_presence(text):
            self.state = state
        unread = last_unread(text)
        if unread is not None:
            self.unread = unread
        return self.state


def _read(path: Path, pos: int) -> tuple[str, int]:
    """Complete lines from pos on, and the position after the last complete line."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            if f.tell() < pos:  # file was truncated
                pos = 0
            f.seek(pos)
            data = f.read()
    except OSError:
        return "", pos
    end = data.rfind(b"\n") + 1
    return data[:end].decode("utf-8", "replace"), pos + end


# --- Claude --------------------------------------------------------------------------------------

CLAUDE_CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"


@dataclass
class ClaudeUsage:
    session: float | None = None  # % of the 5 hour window
    session_reset: datetime | None = None
    week: float | None = None  # % of the weekly limit
    week_reset: datetime | None = None
    plan: str | None = None
    error: str | None = None


def _parse_time(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value).astimezone() if value else None
    except (TypeError, ValueError):
        return None


def parse_claude_usage(data: dict, plan: str | None = None) -> ClaudeUsage:
    five, week = data.get("five_hour") or {}, data.get("seven_day") or {}
    return ClaudeUsage(
        session=five.get("utilization"), session_reset=_parse_time(five.get("resets_at")),
        week=week.get("utilization"), week_reset=_parse_time(week.get("resets_at")), plan=plan,
    )


CLAUDE_PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
PROFILE_EVERY = 3600.0  # the plan rarely changes: ask for it once an hour
_profile_cache: dict = {"at": -PROFILE_EVERY, "data": None}

_ORG_TYPES = {"claude_pro": "Pro", "claude_team": "Team", "claude_enterprise": "Enterprise", "claude_free": "Free"}


def plan_name(profile: dict | None, signed_in_as: str | None) -> str | None:
    """Current plan from the live profile; the credentials file only knows the plan at sign-in time.

    Max comes in two sizes, told apart by the rate limit tier (…_max_5x / …_max_20x)."""
    org = (profile or {}).get("organization") or {}
    org_type, tier = org.get("organization_type") or "", org.get("rate_limit_tier") or ""
    if org_type == "claude_max" or "max" in tier:
        for size in ("20x", "5x"):
            if f"max_{size}" in tier:
                return f"Max ({size})"
        return "Max"
    if org_type in _ORG_TYPES:
        return _ORG_TYPES[org_type]
    return signed_in_as.capitalize() if signed_in_as else None


def _get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def _profile(token: str) -> dict | None:
    if time.monotonic() - _profile_cache["at"] >= PROFILE_EVERY:
        try:
            _profile_cache["data"] = _get(CLAUDE_PROFILE_URL, token)
        except (OSError, ValueError) as err:
            log.info("Claude profile not available: %s", err)
        _profile_cache["at"] = time.monotonic()
    return _profile_cache["data"]


def fetch_claude_usage(credentials: Path = CLAUDE_CREDENTIALS) -> ClaudeUsage:
    """Plan usage of the Claude account Claude Code is logged in with on this PC.

    Only reads the token; refreshing it is left to Claude Code (refresh tokens rotate)."""
    try:
        oauth = json.loads(credentials.read_text(encoding="utf-8"))["claudeAiOauth"]
    except (OSError, ValueError, KeyError):
        return ClaudeUsage(error="Not signed in to Claude Code")
    token = oauth.get("accessToken", "")
    try:
        usage = _get(CLAUDE_USAGE_URL, token)
        return parse_claude_usage(usage, plan_name(_profile(token), oauth.get("subscriptionType")))
    except urllib.error.HTTPError as err:
        if err.code == 401:
            return ClaudeUsage(error="Sign-in expired – open Claude Code once")
        return ClaudeUsage(error=f"HTTP {err.code}")
    except (OSError, ValueError) as err:
        return ClaudeUsage(error=f"No connection ({err.__class__.__name__})")


# --- Weather (Open-Meteo, no key) ----------------------------------------------------------------

# WMO weather code -> built-in icon name
WMO_ICONS = [
    ((0,), "sun"), ((1, 2), "partlycloudy"), ((3,), "cloud"), ((45, 48), "fog"),
    ((51, 53, 55, 56, 57, 61, 63, 80, 81), "rain"), ((65, 66, 67, 82), "rain"),
    ((71, 73, 75, 77, 85, 86), "snow"), ((95, 96, 99), "thunder"),
]


def wmo_icon(code: int, is_day: bool = True) -> str:
    for codes, name in WMO_ICONS:
        if code in codes:
            return "moon" if name == "sun" and not is_day else name
    return "cloud"


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def geocode(city: str) -> tuple[str, float, float] | None:
    q = urllib.parse.urlencode({"name": city, "count": 1, "language": "de"})
    results = _get_json(f"https://geocoding-api.open-meteo.com/v1/search?{q}").get("results") or []
    if not results:
        return None
    r = results[0]
    return (f"{r['name']}, {r.get('country_code', '')}".strip(", "), r["latitude"], r["longitude"])


@dataclass
class Weather:
    temperature: float
    icon: str
    high: float | None = None
    low: float | None = None


def fetch_weather(lat: float, lon: float) -> Weather:
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "current": "temperature_2m,weather_code,is_day",
        "daily": "temperature_2m_max,temperature_2m_min", "timezone": "auto", "forecast_days": 1,
    })
    data = _get_json(f"https://api.open-meteo.com/v1/forecast?{q}")
    cur, daily = data["current"], data.get("daily", {})
    return Weather(
        temperature=cur["temperature_2m"], icon=wmo_icon(cur["weather_code"], bool(cur.get("is_day", 1))),
        high=(daily.get("temperature_2m_max") or [None])[0], low=(daily.get("temperature_2m_min") or [None])[0],
    )
