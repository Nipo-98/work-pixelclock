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


# --- display modes ---------------------------------------------------------------------------------

def _lines(screen):
    return [line.text for line in screen.lines]


def _hm(dt):
    return dt.astimezone().strftime("%H:%M")


def test_time_only_never_shows_the_title():
    now = NOW.astimezone()
    assert _lines(pages.calendar_screen(_mt(120), now, False, show="time")) == [_hm(NOW + timedelta(minutes=120))]
    assert _lines(pages.calendar_screen(_mt(-5), now, False, show="time")) == [f"till {_hm(NOW + timedelta(minutes=25))}"]
    remind = _lines(pages.calendar_screen(_mt(4), now, True, show="time"))
    assert remind == ["in 4 min", _hm(NOW + timedelta(minutes=4))]
    assert not any("Daily" in text for text in remind)


def test_free_and_busy_till():
    now = NOW.astimezone()
    assert _lines(pages.calendar_screen(_mt(120), now, False, show="free_busy")) == [
        "Free till", _hm(NOW + timedelta(minutes=120))]
    assert _lines(pages.calendar_screen(_mt(-5), now, False, show="free_busy")) == [
        "Busy till", _hm(NOW + timedelta(minutes=25))]
    assert _lines(pages.calendar_screen(None, now, False, show="free_busy")) == ["Free"]


def test_free_till_a_meeting_on_another_day_names_the_day():
    now = NOW.astimezone()
    lines = _lines(pages.calendar_screen(_mt(24 * 60), now, False, show="free_busy"))
    assert lines[0] == "Free till" and lines[1].split()[0] in pages.WEEKDAYS


def test_busy_till_covers_back_to_back_and_overlapping_meetings():
    events = [ev("Erstes", -10, 30), ev("Direkt danach", 20, 40), ev("Ueberlappt", 50, 30), ev("Spaeter", 120, 30)]
    m = next_meeting(events, NOW)
    assert m.subject == "Erstes"
    assert m.busy_end == NOW + timedelta(minutes=80)  # 3 meetings in a row, a gap before the 4th
    assert m.end == NOW + timedelta(minutes=20)       # the meeting itself keeps its own end
    lines = _lines(pages.calendar_screen(m, NOW.astimezone(), False, show="free_busy"))
    assert lines == ["Busy till", _hm(NOW + timedelta(minutes=80))]


def test_a_gap_ends_the_busy_block():
    m = next_meeting([ev("A", -10, 20), ev("B", 15, 30)], NOW)
    assert m.busy_end == NOW + timedelta(minutes=10)


def test_calendar_texts_fit_next_to_the_icon():
    from pixelpanel.core import font
    for text in ("Free till", "Busy till", "Free", "Wed 09:00", "till 10:20", "in 45 min"):
        assert font.text_width(text) <= 47, text


def test_icon_can_be_switched_off():
    assert pages.calendar_screen(_mt(120), NOW.astimezone(), False, icon=False).icon is None
    assert pages.calendar_screen(_mt(120), NOW.astimezone(), False).icon is not None


def test_reminder_and_page_follow_the_setting_in_the_engine():
    from pixelpanel.engine import Engine
    cfg = config.default_config()
    cfg["calendar"].update(show="free_busy", icon=False)
    e = Engine(cfg)
    assert (e.ctx.calendar_show, e.ctx.calendar_icon) == ("free_busy", False)
    cfg2 = config.default_config()
    cfg2["calendar"]["show"] = "time"
    e.update_config(cfg2)
    assert (e.ctx.calendar_show, e.ctx.calendar_icon) == ("time", True)
    e.ctx.meeting = _mt(120)
    out = pages.render_page(config.new_page("calendar"), e.ctx, 64, 16, NOW.astimezone())
    assert out.frames[0].size == (64, 16)
