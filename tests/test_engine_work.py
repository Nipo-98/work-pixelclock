import json
import urllib.error
import urllib.request
from datetime import datetime

import pytest

from pixelpanel import api, config, pages, sources, work
from pixelpanel.core import font
from pixelpanel.engine import Engine

NOW = datetime(2026, 11, 5, 16, 0)


def engine(**sections):
    cfg = config.default_config()
    cfg["pages"] = [config.new_page("clock")]
    for key, value in sections.items():
        cfg[key].update(value)
    return Engine(cfg)


# --- every new page draws ------------------------------------------------------------------------

@pytest.mark.parametrize("page_type", ["day", "focus", "stopwatch", "countdown", "mail", "teams_chat", "github",
                                       "monitor", "system", "sessions", "team"])
@pytest.mark.parametrize("filled", [False, True])
def test_new_pages_render_with_and_without_data(page_type, filled):
    ctx = pages.Context()
    if filled:
        ctx = pages.Context(day=work.DayStats(3, 5, 60), focus=("focus", 600.0, 1500.0),
                            stopwatch=(4000.0, True, "Project"), mail=4, teams_unread=2,
                            github=work.GitHubState("failing", "a/repo", 2, 1), monitors=(work.Monitor("api", False),),
                            system=work.SystemStats(20, 60, 80, True), sessions=(3, 1), team_message="Lunch at 12")
    out = pages.render_page(config.new_page(page_type), ctx, 64, 16, NOW)
    assert out.frames[0].size == (64, 16)
    assert pages.signature(config.new_page(page_type), ctx, NOW)  # hashable and complete


def test_panel_texts_fit_next_to_an_icon():
    for text in ("3 left", "60% busy", "99% busy", "Full day", "12 active", "12 waiting", "FAILING", "passing", "running", "no runs",
                 "2 PRs", "to review", "12 open", "issues", "3/3 up", "2 DOWN", "Focus", "Break", "1:02:05", "Home in", "11 days"):
        assert font.text_width(text) <= 47, text
    assert font.text_width("ON AIR") * 2 <= 64


def test_timer_counts_on_the_panel_and_asks_to_be_resent():
    ctx = pages.Context(focus=("focus", 600.0, 1500.0))
    out = pages.render_page(config.new_page("focus"), ctx, 64, 16, NOW)
    assert len(out.frames) == 60 and out.durations[0] == 1000
    assert (out.valid_until - NOW).total_seconds() == 60
    assert out.frames[0].tobytes() != out.frames[1].tobytes()
    stopped = pages.render_page(config.new_page("stopwatch"), pages.Context(stopwatch=(90.0, False, "X")), 64, 16, NOW)
    assert not stopped.animated


def test_timer_near_the_end_stops_at_zero():
    out = pages.render_page(config.new_page("focus"), pages.Context(focus=("focus", 5.0, 1500.0)), 64, 16, NOW)
    assert len(out.frames) == 5


def test_countdown_celebrates_when_reached():
    page = {**config.new_page("countdown"), "time": "15:00"}
    assert pages.render_page(page, pages.Context(), 64, 16, NOW).animated  # rainbow
    assert not pages.render_page({**page, "time": "17:00"}, pages.Context(), 64, 16, NOW).animated


def test_system_page_does_not_resend_for_small_changes():
    p = config.new_page("system")
    a = pages.signature(p, pages.Context(system=work.SystemStats(21, 60)), NOW)
    b = pages.signature(p, pages.Context(system=work.SystemStats(23, 61)), NOW)
    c = pages.signature(p, pages.Context(system=work.SystemStats(40, 60)), NOW)
    assert a == b != c


def test_message_can_show_progress():
    page = {**config.new_page("text"), "text": "Build", "icon": "", "progress": 50.0}
    f = pages.render_page(page, pages.Context(), 64, 16, NOW).frames[0]
    assert f.getpixel((2, 15)) != (0, 0, 0)  # the bar has started ...
    assert f.getpixel((60, 15)) != f.getpixel((2, 15))  # ... but is not full


# --- who gets the panel --------------------------------------------------------------------------

def test_priorities():
    e = engine(focus={"takeover": True})
    e.ctx.teams = "Busy"
    assert e._choose(e.config, 1.0)[2] is True  # status light
    e.focus.start(1.0, 25)
    assert e._choose(e.config, 2.0)[0]["type"] == "focus"  # focus beats the status light
    e.show_message(config.new_page("text"), None)
    assert e._choose(e.config, 3.0)[1] == "Door sign / message"  # your sign beats focus and the status light
    e.ctx.on_air = True
    assert e._choose(e.config, 4.0)[0]["type"] == "on_air"  # on air beats a sign
    e.push_alert("CI failed", "repo", seconds=10)
    assert e._choose(e.config, 5.0)[0]["type"] == "alert"  # a short note beats on air
    assert e._choose(e.config, 16.0)[0]["type"] == "on_air"
    e.ctx.on_air = False
    assert e._choose(e.config, 17.0)[1] == "Door sign / message"  # the sign comes back


def test_focus_without_takeover_leaves_the_rotation_alone():
    e = engine(focus={"takeover": False})
    e.focus.start(1.0, 25)
    assert e._choose(e.config, 2.0)[0]["type"] == "clock"


def test_notes_wait_for_their_turn_and_then_end():
    e = engine()
    e.push_alert("one", "x", seconds=10)
    e.push_alert("two", "y", seconds=10)
    assert e._choose(e.config, 100.0)[0]["title"] == "one"
    assert e._choose(e.config, 105.0)[0]["title"] == "one"
    assert e._choose(e.config, 110.0)[0]["title"] == "two"
    assert e._choose(e.config, 121.0)[0]["type"] == "clock"


def test_schedule_takes_a_page_out_of_the_rotation():
    e = engine()
    e.config["pages"][0]["schedule"] = {"enabled": True, "from": "00:00", "to": "00:00"}
    assert e._rotation(e.config) == []
    e.config["pages"][0]["schedule"] = {"enabled": False}
    assert len(e._rotation(e.config)) == 1


def test_night_off_switches_the_panel_off(monkeypatch):
    e = engine(night={"enabled": True, "off": True, "start": "00:00", "end": "23:59"})
    assert e._power_on(e.config) is False
    e.config["night"]["off"] = False
    assert e._power_on(e.config) is True


# --- alerts from sources -------------------------------------------------------------------------

def _titles(e):
    return [p["title"] for p, _ in e._alerts]


def test_ci_failure_alerts_once():
    e = engine()
    ok, bad = work.GitHubState("passing", "a/r"), work.GitHubState("failing", "a/r")
    e._set_github(ok)
    e._set_github(bad)
    e._set_github(bad)
    assert _titles(e) == ["CI failed"]
    e._set_github(ok)
    e._set_github(bad)
    assert _titles(e) == ["CI failed", "CI failed"]


def test_only_issues_that_are_really_new_alert():
    e = engine()
    e._set_github(work.GitHubState(issue_keys=("a/r#1", "a/r#2")))  # first look: no alarm for old ones
    assert _titles(e) == []
    e._set_github(work.GitHubState(issue_keys=("a/r#1", "a/r#2", "a/r#3")))
    assert [p["text"] for p, _ in e._alerts] == ["r#3"]


def test_github_error_keeps_quiet_and_is_shown():
    e = engine()
    e._set_github(work.GitHubState(error="GitHub: rate limit"))
    assert _titles(e) == [] and "rate limit" in e.status.github


def test_monitor_alerts_when_going_down_only():
    e = engine()
    up, down = (work.Monitor("api", True),), (work.Monitor("api", False),)
    e._set_monitors(up)
    assert _titles(e) == []
    e._set_monitors(down)
    e._set_monitors(down)
    assert [p["text"] for p, _ in e._alerts] == ["api"]
    assert "api" in e.status.monitors


def test_team_message_alerts_when_it_changes():
    e = engine()
    e._set_team("Lunch")
    e._set_team("Lunch")
    e._set_team("Meeting moved")
    assert [p["text"] for p, _ in e._alerts] == ["Lunch", "Meeting moved"]


def test_limit_alert_only_when_switched_on():
    e = engine()
    e.ctx.claude = sources.ClaudeUsage(session=85, week=10)
    e._check_limits(e.config)
    assert _titles(e) == []
    e.config["limit"].update(enabled=True, thresholds="80, 90")
    e._check_limits(e.config)
    assert _titles(e) == ["5h 85%"]


def test_settings_change_does_not_ask_github_again_unless_github_changed():
    e = engine()
    e._next_poll["github"] = 999.0
    cfg = json.loads(json.dumps(e.config))
    cfg["brightness"] = 11
    e.update_config(cfg)
    assert e._next_poll.get("github") == 999.0
    cfg = json.loads(json.dumps(cfg))
    cfg["github"]["repos"] = "a/b"
    e.update_config(cfg)
    assert "github" not in e._next_poll


# --- Teams unread --------------------------------------------------------------------------------

def test_teams_unread_is_summed_from_the_last_state():
    line = ("x BroadcastGlobalState: New Global State Event: UserDataGlobalState total number of users: 2 "
            "{ availability: Busy, unread notification count: %d } { availability: Busy, unread notification count: %d }\n")
    assert sources.last_unread(line % (1, 1) + line % (2, 3)) == 5
    assert sources.last_unread("nothing here\n") is None


# --- local API -----------------------------------------------------------------------------------

EFFECTS = ("none", "fire")


def test_message_parsing():
    page, seconds = api.parse_message(b"Backup done", EFFECTS)
    assert page["text"] == "Backup done" and seconds == api.DEFAULT_SECONDS
    page, seconds = api.parse_message(json.dumps(
        {"text": "Build", "color": "00ff00", "progress": 150, "seconds": 0, "effect": "fire", "icon": "🚀"}).encode(), EFFECTS)
    assert (page["color"], page["progress"], page["effect"], page["icon"], seconds) == ("#00ff00", 100.0, "fire", "🚀", None)
    assert api.parse_message(b'{"text": "x", "effect": "evil", "color": "red"}', EFFECTS)[0]["effect"] == "none"


def test_files_on_this_pc_cannot_be_used_as_icons():
    page, _ = api.parse_message(json.dumps({"text": "x", "icon": "C:\\secret\\a.png"}).encode(), EFFECTS)
    assert page["icon"] == ""


@pytest.mark.parametrize("body", [b"", b"   ", b'{"text": ""}', b'{"text": "x", "seconds": -1}', b'{"text": "x", "progress": "a"}', b"{broken"])
def test_bad_messages_are_refused(body):
    with pytest.raises(ValueError):
        api.parse_message(body, EFFECTS)


def _call(url, body=None):
    req = urllib.request.Request(url, data=body)
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as err:
        return err.code, None


def test_api_server_end_to_end():
    got, cleared = [], []
    server = api.LocalApi("tok", lambda p, s: got.append((p, s)), lambda: cleared.append(1), lambda: None,
                          lambda: {"showing": "Clock"}, EFFECTS, port=0)
    server.start()
    base = f"http://127.0.0.1:{server.port}/api"
    try:
        assert _call(f"{base}/tok/message", b"Hello")[0] == 200
        assert got[0][0]["text"] == "Hello"
        assert _call(f"{base}/wrong/message", b"Hello")[0] == 404
        assert _call(f"{base}/tok/message", b"")[0] == 400
        assert _call(f"{base}/tok/status") == (200, {"showing": "Clock"})
        assert _call(f"{base}/wrong/status")[0] == 404
        assert _call(f"{base}/tok/clear", b"")[0] == 200 and cleared == [1]
        assert len(got) == 1
    finally:
        server.stop()
