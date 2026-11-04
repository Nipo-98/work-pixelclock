from datetime import datetime, timedelta, timezone

from pixelpanel import config, pages
from pixelpanel.m365 import Meeting, next_meeting

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)


def ev(subject, start_min, dur_min=30, **extra):
    start = NOW + timedelta(minutes=start_min)
    end = start + timedelta(minutes=dur_min)
    return {
        "subject": subject,
        "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"},
        "end": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"},
        "isAllDay": False, "isCancelled": False, "showAs": "busy",
        "responseStatus": {"response": "accepted"}, "location": {"displayName": ""},
        **extra,
    }


def test_skips_cancelled_declined_allday_free_and_past():
    events = [
        ev("Vorbei", -60),
        ev("Abgesagt", 10, isCancelled=True),
        ev("Abgelehnt", 15, responseStatus={"response": "declined"}),
        ev("Urlaub", 0, 24 * 60, isAllDay=True),
        ev("Fokuszeit", 20, showAs="free"),
        ev("Daily", 30),
        ev("Später", 90),
    ]
    m = next_meeting(events, NOW)
    assert m is not None and m.subject == "Daily"
    assert m.start == NOW + timedelta(minutes=30)


def test_running_meeting_counts_until_it_ends():
    m = next_meeting([ev("Läuft", -10, 30), ev("Danach", 60)], NOW)
    assert m.subject == "Läuft" and m.running(NOW)


def test_no_meeting():
    assert next_meeting([ev("Vorbei", -60)], NOW) is None
    assert next_meeting([], NOW) is None


def _mt(start_min, subject="Daily"):
    start = NOW + timedelta(minutes=start_min)
    return Meeting(subject, start, start + timedelta(minutes=30), "")


def test_reminder_takes_over_shortly_before_a_meeting(monkeypatch):
    from pixelpanel import engine as eng_mod
    cfg = config.default_config()
    cfg["calendar"].update(enabled=True, remind_minutes=5)
    e = eng_mod.Engine(cfg)
    e.ctx.teams = "Busy"  # reminder even beats the status light
    now = datetime.now(timezone.utc)
    e.ctx.meeting = Meeting("Daily", now + timedelta(minutes=4), now + timedelta(minutes=34), "")
    page, label, light = e._choose(e.config, 100)
    assert page["type"] == "calendar" and page.get("remind") and not light
    e.ctx.meeting = Meeting("Daily", now + timedelta(minutes=20), now + timedelta(minutes=50), "")
    page, label, light = e._choose(e.config, 101)
    assert page["type"] != "calendar" or not page.get("remind")


def test_calendar_page_shows_time_and_countdown():
    page = config.new_page("calendar")
    local_now = NOW.astimezone()
    far = pages.calendar_screen(_mt(120), local_now, remind=False)
    assert far.lines[0].text == (NOW + timedelta(minutes=120)).astimezone().strftime("%H:%M")
    assert far.lines[1].text == "Daily"
    soon = pages.calendar_screen(_mt(4), local_now, remind=True)
    assert soon.lines[0].text == "in 4 min"
    running = pages.calendar_screen(_mt(-5), local_now, remind=False)
    assert running.lines[0].text.startswith("till ")
    none = pages.calendar_screen(None, local_now, remind=False)
    assert [line.text for line in none.lines] == ["No", "meetings"]
    assert pages.render_page(page, pages.Context(meeting=_mt(30)), 64, 16, local_now).frames
