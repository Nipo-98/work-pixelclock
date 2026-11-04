"""Next meeting from the Microsoft 365 calendar (Microsoft Graph, sign-in with MSAL).

Needs an app registration in the organisation's Entra ID (see README): public client, redirect
URI http://localhost, delegated permission Calendars.Read. The token cache is encrypted with
Windows DPAPI, so the user signs in once.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import APP_DIR

log = logging.getLogger(__name__)

SCOPES = ["Calendars.Read"]
GRAPH = "https://graph.microsoft.com/v1.0"
CACHE_FILE = APP_DIR / "m365_token.bin"
LOOKAHEAD = timedelta(hours=36)


@dataclass(frozen=True)
class Meeting:
    subject: str
    start: datetime  # aware
    end: datetime
    location: str

    def running(self, now: datetime) -> bool:
        return self.start <= now < self.end


def _parse(value: dict) -> datetime:
    """Graph returns 'dateTime' without offset in the requested zone (UTC by default)."""
    raw = value["dateTime"].split(".")[0]
    return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)


def next_meeting(events: list[dict], now: datetime) -> Meeting | None:
    """The running or next real meeting: not cancelled, declined, all-day or shown as free."""
    found = []
    for e in events:
        if e.get("isCancelled") or e.get("isAllDay") or e.get("showAs") == "free":
            continue
        if (e.get("responseStatus") or {}).get("response") == "declined":
            continue
        try:
            start, end = _parse(e["start"]), _parse(e["end"])
        except (KeyError, ValueError):
            continue
        if end <= now:
            continue
        subject = (e.get("subject") or "Meeting").strip()
        found.append(Meeting(subject, start, end, ((e.get("location") or {}).get("displayName") or "").strip()))
    return min(found, key=lambda m: m.start) if found else None


class Calendar:
    """MSAL sign-in plus Graph calendarView; all calls block (run them in a thread)."""

    def __init__(self, client_id: str, tenant_id: str) -> None:
        import msal
        from msal_extensions import PersistedTokenCache, build_encrypted_persistence

        APP_DIR.mkdir(parents=True, exist_ok=True)
        cache = PersistedTokenCache(build_encrypted_persistence(str(CACHE_FILE)))
        self.app = msal.PublicClientApplication(
            client_id, authority=f"https://login.microsoftonline.com/{tenant_id or 'organizations'}",
            token_cache=cache)

    def account(self) -> str | None:
        accounts = self.app.get_accounts()
        return accounts[0].get("username") if accounts else None

    def _token(self) -> str | None:
        accounts = self.app.get_accounts()
        if not accounts:
            return None
        result = self.app.acquire_token_silent(SCOPES, account=accounts[0])
        return (result or {}).get("access_token")

    def sign_in(self) -> str:
        """Opens the browser for the Microsoft sign-in; returns the account name."""
        result = self.app.acquire_token_interactive(SCOPES, prompt="select_account")
        if "access_token" not in result:
            raise RuntimeError(result.get("error_description") or result.get("error") or "Sign-in failed")
        return self.account() or "angemeldet"

    def sign_out(self) -> None:
        for account in self.app.get_accounts():
            self.app.remove_account(account)

    def fetch(self, now: datetime | None = None) -> Meeting | None:
        token = self._token()
        if token is None:
            raise PermissionError("Not signed in")
        now = now or datetime.now(timezone.utc)
        q = urllib.parse.urlencode({
            "startDateTime": (now - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDateTime": (now + LOOKAHEAD).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "$select": "subject,start,end,location,isAllDay,isCancelled,showAs,responseStatus",
            "$orderby": "start/dateTime",
            "$top": "50",
        })
        req = urllib.request.Request(f"{GRAPH}/me/calendarView?{q}", headers={
            "Authorization": f"Bearer {token}", "Prefer": 'outlook.timezone="UTC"'})
        with urllib.request.urlopen(req, timeout=20) as resp:
            events = json.load(resp).get("value", [])
        return next_meeting(events, now)
