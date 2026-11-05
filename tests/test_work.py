from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pixelpanel import config, sources, work
from pixelpanel.m365 import Meeting


# --- break reminder --------------------------------------------------------------------------------

def test_break_after_90_minutes_of_activity():
    b = work.BreakTracker(90)
    assert b.update(0, 0) is False
    assert b.update(89 * 60, 5) is False
    assert b.update(90 * 60, 5) is True
    assert b.update(91 * 60, 5) is False  # next one only after another full period


def test_being_away_counts_as_a_break():
    b = work.BreakTracker(90, reset_after=300)
    b.update(0, 0)
    b.update(60 * 60, 400)  # away for more than 5 minutes
    assert b.since is None
    assert b.update(61 * 60, 0) is False
    assert b.update(150 * 60, 0) is False  # 89 min since coming back
    assert b.update(151 * 60, 0) is True


# --- limit warning ---------------------------------------------------------------------------------

def _usage(session, week=10):
    return sources.ClaudeUsage(session=session, week=week)


def test_limit_warns_once_per_threshold_and_rearms_after_reset():
    w = work.LimitWatch((80, 90))
    assert w.update(_usage(50)) == []
    assert w.update(_usage(81)) == [("5h", 81)]
    assert w.update(_usage(85)) == []
    assert w.update(_usage(91)) == [("5h", 91)]
    assert w.update(_usage(95)) == []
    assert w.update(_usage(3)) == []  # the window has reset
    assert w.update(_usage(82)) == [("5h", 82)]


def test_limit_handles_week_and_missing_values():
    w = work.LimitWatch((80,))
    assert w.update(sources.ClaudeUsage(session=None, week=85)) == [("Week", 85)]


# --- timers ----------------------------------------------------------------------------------------

def test_focus_runs_into_the_break_and_ends():
    f = work.FocusTimer()
    f.start(0, 25)
    assert f.phase == "focus" and f.remaining(60) == 24 * 60
    assert f.update(25 * 60 - 1, 5) is None
    assert f.update(25 * 60, 5) == "focus" and f.phase == "break"
    assert f.update(30 * 60, 5) == "break" and f.phase is None
    assert f.update(31 * 60, 5) is None


def test_focus_without_break_and_manual_stop():
    f = work.FocusTimer()
    f.start(0, 1)
    assert f.update(60, 0) == "focus" and f.phase is None
    f.start(0, 25)
    f.stop()
    assert f.phase is None and f.update(9999, 5) is None


def test_stopwatch():
    s = work.Stopwatch()
    s.toggle(10)
    assert s.running and s.elapsed(70) == 60
    s.toggle(70)
    assert not s.running and s.elapsed(500) == 60
    s.toggle(600)
    assert s.elapsed(630) == 90
    s.reset()
    assert s.elapsed(700) == 0 and not s.running


def test_clock_text():
    assert work.clock_text(65) == "1:05"
    assert work.clock_text(3725) == "1:02:05"
    assert work.clock_text(-5) == "0:00"


def test_countdown_text():
    now = datetime(2026, 11, 5, 16, 0)
    assert work.countdown_text("", "17:23", now) == "1:23 h"
    assert work.countdown_text("", "16:30", now) == "30 min"
    assert work.countdown_text("", "15:00", now) is None  # today's Feierabend has passed
    assert work.countdown_text("2026-11-17", "09:00", now) == "11 days"
    assert work.countdown_text("nonsense", "09:00", now) == "?"


# --- schedule and profiles -------------------------------------------------------------------------

def test_time_range_also_over_midnight():
    t = datetime(2026, 11, 5, 23, 0)
    assert work.in_time_range("22:00", "07:00", t)
    assert not work.in_time_range("08:00", "18:00", t)
    assert work.in_time_range("08:00", "18:00", t.replace(hour=8))
    assert not work.in_time_range("08:00", "18:00", t.replace(hour=18))


def test_schedule():
    thursday, saturday = datetime(2026, 11, 5, 9, 0), datetime(2026, 11, 7, 9, 0)
    assert work.in_schedule(None, thursday)
    assert work.in_schedule({"enabled": False, "from": "20:00", "to": "21:00"}, thursday)
    assert not work.in_schedule({"enabled": True, "from": "20:00", "to": "21:00"}, thursday)
    only_week = {"enabled": True, "from": "08:00", "to": "18:00", "weekdays_only": True}
    assert work.in_schedule(only_week, thursday) and not work.in_schedule(only_week, saturday)


def test_profiles_save_and_apply():
    cfg = config.default_config()
    ids = [p["id"] for p in cfg["pages"]]
    for p in cfg["pages"]:
        p["enabled"] = p["id"] == ids[0]
    cfg["brightness"], cfg["transition"] = 20, "fade"
    work.save_profile(cfg, "Focus")
    for p in cfg["pages"]:
        p["enabled"] = True
    cfg["brightness"] = 90
    assert work.apply_profile(cfg, "Focus")
    assert [p["id"] for p in cfg["pages"] if p["enabled"]] == [ids[0]]
    assert (cfg["brightness"], cfg["transition"], cfg["active_profile"]) == (20, "fade", "Focus")
    assert not work.apply_profile(cfg, "Missing")
    work.save_profile(cfg, "Focus")  # same name replaces
    assert len(cfg["profiles"]) == 1


def test_profile_by_time():
    cfg = config.default_config()
    work.save_profile(cfg, "Work", "08:00", "17:00")
    work.save_profile(cfg, "Manual")
    work.save_profile(cfg, "Evening", "17:00", "22:00")
    day = datetime(2026, 11, 5, 9, 0)
    assert work.profile_for_time(cfg, day) == "Work"
    assert work.profile_for_time(cfg, day.replace(hour=18)) == "Evening"
    assert work.profile_for_time(cfg, day.replace(hour=23)) is None


# --- day overview ----------------------------------------------------------------------------------

def test_day_stats_counts_left_and_merges_overlaps():
    now = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)

    def m(h1, m1, h2, m2, day=0):
        base = now + timedelta(days=day)
        return Meeting("x", base.replace(hour=h1, minute=m1), base.replace(hour=h2, minute=m2), "")

    meetings = [m(9, 0, 10, 0), m(9, 30, 10, 30), m(13, 0, 14, 0), m(15, 0, 15, 30), m(9, 0, 17, 0, day=1)]
    s = work.day_stats(meetings, now)
    assert (s.left, s.total) == (2, 4)  # tomorrow does not count
    assert s.share == round(100 * 180 / 480)  # 9:00-10:30 + 13-14 + 15-15:30


def test_day_stats_empty():
    assert work.day_stats([], datetime.now(timezone.utc)) == work.DayStats(0, 0, 0)


# --- GitHub ----------------------------------------------------------------------------------------

def _run(workflow, status="completed", conclusion="success"):
    return {"workflow_id": workflow, "status": status, "conclusion": conclusion}


def test_ci_summary_uses_the_latest_run_of_each_workflow():
    # newest first: workflow 1 is green again, an old failure does not count
    assert work.summarize_runs({"a/b": [_run(1), _run(1, conclusion="failure")]}) == ("passing", "a/b")
    assert work.summarize_runs({"a/b": [_run(1, conclusion="failure"), _run(1)]}) == ("failing", "a/b")
    assert work.summarize_runs({"a/b": [_run(1, status="in_progress", conclusion=None)]}) == ("running", "a/b")
    assert work.summarize_runs({"a/b": [_run(1)], "c/d": [_run(2, conclusion="failure")]}) == ("failing", "c/d")
    assert work.summarize_runs({"a/b": []}) == ("none", "")
    assert work.summarize_runs({"a/b": [_run(1, conclusion="cancelled")]}) == ("none", "")


def test_fetch_github_counts_issues_without_pull_requests(monkeypatch):
    def fake(path, token):
        if "/actions/runs" in path:
            return {"workflow_runs": [_run(1)]}
        if "/issues" in path and "search" not in path:
            return [{"number": 7}, {"number": 9}, {"number": 8, "pull_request": {}}]
        return {"total_count": 3}

    monkeypatch.setattr(work, "_gh", fake)
    s = work.fetch_github(["a/b"], "me", "")
    assert (s.ci, s.prs, s.issues, s.newest_issue, s.newest_issue_repo) == ("passing", 3, 2, 9, "a/b")
    assert work.fetch_github(["a/b"], "", "").prs == 0


def test_fetch_github_explains_errors(monkeypatch):
    def fake(path, token):
        raise work.urllib.error.HTTPError(path, 404, "x", None, None)

    monkeypatch.setattr(work, "_gh", fake)
    assert "private" in work.fetch_github(["a/b"], "", "").error


# --- monitors and team message ---------------------------------------------------------------------

def test_parse_targets():
    text = "# comment\nhttps://a.example/health\nAPI = https://b.example/x?y=1\nc.example\n\n"
    assert work.parse_targets(text) == [("a.example", "https://a.example/health"), ("API", "https://b.example/x?y=1"),
                                        ("c.example", "https://c.example")]


def test_check_url_against_a_real_local_server():
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response({"/ok": 200, "/auth": 401, "/bad": 500}[self.path])
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert work.check_url(f"{base}/ok") and work.check_url(f"{base}/auth")
        assert not work.check_url(f"{base}/bad")
    finally:
        srv.shutdown()
        srv.server_close()
    assert not work.check_url(base, timeout=1)  # nobody listens anymore


def test_team_message_from_a_file(tmp_path):
    f = tmp_path / "msg.txt"
    f.write_text("\n  Lunch at 12!  \nsecond line\n", encoding="utf-8")
    assert work.read_team_message(str(f)) == "Lunch at 12!"
    f.write_text("x" * 500, encoding="utf-8")
    assert len(work.read_team_message(str(f))) == 200
    with pytest.raises(OSError):
        work.read_team_message(str(Path(tmp_path) / "missing.txt"))


# --- token storage ---------------------------------------------------------------------------------

def test_token_is_not_stored_as_plain_text():
    blob = work.protect("ghp_secret")
    assert "ghp_secret" not in blob and work.unprotect(blob) == "ghp_secret"
    assert work.protect("") == "" and work.unprotect("") == ""
    assert work.unprotect("bm90LWVuY3J5cHRlZA==") == ""  # broken value: no crash


# --- Windows sources -------------------------------------------------------------------------------

def test_real_sources_do_not_crash():
    assert isinstance(work.on_air(), bool)
    assert work.idle_seconds() >= 0
    stats = work.system_stats()
    assert 0 <= stats.cpu <= 100 and 0 <= stats.ram <= 100
