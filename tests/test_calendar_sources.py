from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pixelpanel import calendars

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)  # Tuesday, 10:00 in Berlin

ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:test
BEGIN:VTIMEZONE
TZID:W. Europe Standard Time
BEGIN:STANDARD
DTSTART:16010101T030000
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=10
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:16010101T020000
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=3
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
UID:daily
SUMMARY:Daily
DTSTART;TZID=W. Europe Standard Time:20261020T103000
DTEND;TZID=W. Europe Standard Time:20261020T104500
RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
END:VEVENT
BEGIN:VEVENT
UID:cancelled
SUMMARY:Abgesagt
STATUS:CANCELLED
DTSTART:20261103T091000Z
DTEND:20261103T092000Z
END:VEVENT
BEGIN:VEVENT
UID:free
SUMMARY:Fokus
TRANSP:TRANSPARENT
X-MICROSOFT-CDO-BUSYSTATUS:FREE
DTSTART:20261103T091500Z
DTEND:20261103T093000Z
END:VEVENT
BEGIN:VEVENT
UID:allday
SUMMARY:Urlaub
DTSTART;VALUE=DATE:20261103
DTEND;VALUE=DATE:20261104
END:VEVENT
END:VCALENDAR
"""


def test_ics_expands_series_and_skips_cancelled_free_allday():
    m = calendars.next_from_ics(ICS, NOW)
    assert m is not None and m.subject == "Daily"
    assert m.start == datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)  # 10:30 CET


def test_ics_empty():
    assert calendars.next_from_ics("BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n", NOW) is None


def test_webcal_links_are_fetched_over_https():
    assert calendars.ics_http_url("webcal://outlook.office365.com/x/calendar.ics") == \
        "https://outlook.office365.com/x/calendar.ics"


def test_outlook_filter_date_uses_the_windows_short_format():
    dt = datetime(2026, 11, 3, 7, 5)
    assert calendars.outlook_date(dt, "dd.MM.yyyy", "HH:mm") == "03.11.2026 07:05"
    assert calendars.outlook_date(dt, "M/d/yyyy", "h:mm tt") == "11/3/2026 7:05 AM"


def _item(subject, start_min, dur=30, **kw):
    start = (NOW + timedelta(minutes=start_min)).astimezone()
    return SimpleNamespace(Subject=subject, Start=start, End=start + timedelta(minutes=dur),
                           AllDayEvent=kw.get("allday", False), BusyStatus=kw.get("busy", 2),
                           MeetingStatus=kw.get("status", 1), ResponseStatus=kw.get("response", 3),
                           Location="")


def test_outlook_times_are_local_wall_time_despite_their_utc_label():
    """pywin32 labels Outlook's local times as UTC; 10:00 in the calendar must stay 10:00 local."""
    labelled_utc = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)
    t = calendars.com_time(labelled_utc)
    assert (t.hour, t.minute) == (10, 0) and t.utcoffset() == datetime(2026, 11, 4, 10).astimezone().utcoffset()


def test_outlook_items_are_filtered_like_graph():
    items = [_item("Frei", 5, busy=0), _item("Abgesagt", 6, status=5), _item("Abgelehnt", 7, response=4),
             _item("Ganztag", 0, 1440, allday=True), _item("Vorbei", -90), _item("Daily", 30), _item("Später", 90)]
    m = calendars.next_from_outlook_items(items, NOW)
    assert m.subject == "Daily"
