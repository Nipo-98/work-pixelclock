"""Calendar sources without an app registration: the classic Outlook on this PC, or an ICS link.

All sources end in the same Meeting (see m365.py for the Microsoft 365 / Graph source).
"""

from __future__ import annotations

import logging
import re
import time
import urllib.request
import winreg
from datetime import datetime, timedelta, timezone

from .m365 import LOOKAHEAD, Meeting, pick_next, start_of_day

log = logging.getLogger(__name__)

PROVIDERS: dict[str, str] = {
    "outlook": "Outlook on this PC (no sign-in)",
    "ics": "Calendar link (ICS)",
    "graph": "Microsoft 365 (sign-in, needs an app registration)",
}

# Outlook enums
OL_FOLDER_CALENDAR = 9
OL_FOLDER_INBOX = 6
OL_FREE = 0
OL_MEETING_CANCELED = (5, 7)
OL_RESPONSE_DECLINED = 4
OUTLOOK_START_WAIT = 20  # seconds


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.astimezone()


# --- ICS -----------------------------------------------------------------------------------------

def ics_http_url(url: str) -> str:
    url = url.strip()
    return "https://" + url[len("webcal://"):] if url.lower().startswith("webcal://") else url


def meetings_from_ics(text: str, now: datetime) -> list[Meeting]:
    import icalendar
    import recurring_ical_events

    cal = icalendar.Calendar.from_ical(text)
    found = []
    for ev in recurring_ical_events.of(cal).between(start_of_day(now), now + LOOKAHEAD):
        start, end = ev.decoded("DTSTART"), ev.decoded("DTEND") if "DTEND" in ev else None
        if not isinstance(start, datetime):  # all-day: a date without time
            continue
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        busy = str(ev.get("X-MICROSOFT-CDO-BUSYSTATUS", "")).upper()
        if busy == "FREE" or (not busy and str(ev.get("TRANSP", "")).upper() == "TRANSPARENT"):
            continue
        start = _aware(start)
        end = _aware(end) if isinstance(end, datetime) else start + timedelta(minutes=30)
        found.append(Meeting(str(ev.get("SUMMARY", "Meeting")).strip(), start, end, str(ev.get("LOCATION", "")).strip()))
    return found


def next_from_ics(text: str, now: datetime) -> Meeting | None:
    return pick_next(meetings_from_ics(text, now), now)


def fetch_ics(url: str, now: datetime | None = None) -> list[Meeting]:
    req = urllib.request.Request(ics_http_url(url), headers={"User-Agent": "PixelPanel/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        text = resp.read().decode("utf-8", "replace")
    return meetings_from_ics(text, now or datetime.now(timezone.utc))


# --- classic Outlook (COM) -----------------------------------------------------------------------

def _windows_formats() -> tuple[str, str]:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International") as key:
            return winreg.QueryValueEx(key, "sShortDate")[0], winreg.QueryValueEx(key, "sShortTime")[0]
    except OSError:
        return "M/d/yyyy", "h:mm tt"


_TOKENS = re.compile(r"yyyy|yy|MM|M|dd|d|HH|H|hh|h|mm|m|tt|'[^']*'")


def outlook_date(dt: datetime, date_fmt: str, time_fmt: str) -> str:
    """A date in the PC's short date/time format – the only format Outlook's Restrict() accepts."""
    hour12 = dt.hour % 12 or 12

    def token(m: re.Match) -> str:
        t = m.group(0)
        return {
            "yyyy": f"{dt.year:04d}", "yy": f"{dt.year % 100:02d}", "MM": f"{dt.month:02d}", "M": str(dt.month),
            "dd": f"{dt.day:02d}", "d": str(dt.day), "HH": f"{dt.hour:02d}", "H": str(dt.hour),
            "hh": f"{hour12:02d}", "h": str(hour12), "mm": f"{dt.minute:02d}", "m": str(dt.minute),
            "tt": "AM" if dt.hour < 12 else "PM",
        }.get(t, t.strip("'"))

    return f"{_TOKENS.sub(token, date_fmt)} {_TOKENS.sub(token, time_fmt)}"


def com_time(dt: datetime) -> datetime:
    """Outlook hands out local wall time, but pywin32 labels it as UTC: drop the label, use local."""
    return dt.replace(tzinfo=None).astimezone()


def meetings_from_outlook_items(items) -> list[Meeting]:
    found = []
    for it in items:
        try:
            if it.AllDayEvent or it.BusyStatus == OL_FREE or it.MeetingStatus in OL_MEETING_CANCELED:
                continue
            if it.ResponseStatus == OL_RESPONSE_DECLINED:
                continue
            start, end = com_time(it.Start), com_time(it.End)
        except AttributeError:
            continue
        found.append(Meeting(str(it.Subject or "Meeting").strip(), start, end, str(it.Location or "").strip()))
    return found


def next_from_outlook_items(items, now: datetime) -> Meeting | None:
    return pick_next(meetings_from_outlook_items(items), now)


def _outlook_folder(number: int):
    """A default folder of the classic Outlook profile (starts Outlook hidden if needed)."""
    import pythoncom
    import win32com.client

    for _ in range(OUTLOOK_START_WAIT):  # a freshly started Outlook needs a moment until it answers
        try:
            return win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI").GetDefaultFolder(number)
        except (AttributeError, pythoncom.com_error):
            time.sleep(1)
    raise RuntimeError("Outlook is not responding")


def outlook_unread() -> int:
    """Unread mails in the inbox of the classic Outlook profile."""
    import pythoncom

    pythoncom.CoInitialize()
    try:
        return int(_outlook_folder(OL_FOLDER_INBOX).UnReadItemCount)
    finally:
        pythoncom.CoUninitialize()


def fetch_outlook(now: datetime | None = None) -> list[Meeting]:
    """Today's and the coming meetings from the classic Outlook profile."""
    import pythoncom

    now = now or datetime.now(timezone.utc)
    pythoncom.CoInitialize()
    try:
        items = _outlook_folder(OL_FOLDER_CALENDAR).Items
        items.IncludeRecurrences = True
        items.Sort("[Start]")
        local_now = now.astimezone().replace(tzinfo=None)
        fmt = _windows_formats()
        flt = (f"[End] >= '{outlook_date(start_of_day(now).replace(tzinfo=None), *fmt)}' AND "
               f"[Start] <= '{outlook_date(local_now + LOOKAHEAD, *fmt)}'")
        restricted = items.Restrict(flt)
        found = []
        item = restricted.GetFirst()
        while item is not None and len(found) < 50:  # with recurrences Count is unreliable: iterate
            found.append(item)
            item = restricted.GetNext()
        return meetings_from_outlook_items(found)
    finally:
        pythoncom.CoUninitialize()
