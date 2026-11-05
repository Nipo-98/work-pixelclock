import json
import urllib.error
import urllib.request
from datetime import datetime

import pytest

from pixelpanel import claude_hooks as ch
from pixelpanel import config, pages
from pixelpanel.core import font

ALL = {"input", "idle", "error"}
URL = f"{ch.URL_BASE}/secret"


def ev(name, session="s1", cwd=r"C:\work\my-project", **extra):
    return {"hook_event_name": name, "session_id": session, "cwd": cwd, **extra}


def perm(session="s1", **kw):
    return ev("Notification", session, notification_type="permission_prompt", **kw)


def test_permission_prompt_raises_an_alert_and_tool_use_clears_it():
    t = ch.Tracker()
    assert t.apply(perm(), 0, ALL) is True
    s = t.summary()
    assert (s.kind, s.project, s.count) == ("input", "my-project", 1)
    assert t.apply(perm(), 1, ALL) is False  # the same alert again is not new
    t.apply(ev("PostToolUse"), 2, ALL)  # approved, Claude works on
    assert t.summary() is None


def test_idle_stays_until_the_user_answers_and_stop_does_not_clear_it():
    t = ch.Tracker()
    t.apply(ev("Notification", notification_type="idle_prompt"), 0, ALL)
    assert t.summary().kind == "idle"
    t.apply(ev("Stop"), 1, ALL)
    assert t.summary().kind == "idle"
    t.apply(ev("UserPromptSubmit"), 2, ALL)
    assert t.summary() is None


def test_api_error_stays_until_answered_or_session_end():
    t = ch.Tracker()
    t.apply(ev("StopFailure", error="rate_limit"), 0, ALL)
    t.apply(ev("PostToolUse"), 1, ALL)
    assert t.summary().kind == "error"
    t.apply(ev("SessionEnd"), 2, ALL)
    assert t.summary() is None


def test_other_notifications_and_disabled_kinds_are_ignored():
    t = ch.Tracker()
    assert t.apply(ev("Notification", notification_type="auth_success"), 0, ALL) is False
    assert t.apply(ev("Notification", notification_type="idle_prompt"), 0, {"input", "error"}) is False
    assert t.summary() is None


def test_several_sessions_count_and_the_most_urgent_wins():
    t = ch.Tracker()
    t.apply(ev("Notification", "a", r"C:\x\idle-one", notification_type="idle_prompt"), 0, ALL)
    t.apply(perm("b", cwd=r"C:\x\needs-one"), 1, ALL)
    assert (t.summary().kind, t.summary().project, t.summary().count) == ("input", "needs-one", 2)
    t.apply(ev("StopFailure", "c", r"C:\x\broken"), 2, ALL)
    assert (t.summary().kind, t.summary().project, t.summary().count) == ("error", "broken", 3)


def test_forgotten_alerts_expire():
    t = ch.Tracker()
    t.apply(perm(), 0, ALL)
    t.purge(ch.EXPIRE_AFTER - 1)
    assert t.summary() is not None
    t.purge(ch.EXPIRE_AFTER + 1)
    assert t.summary() is None


# --- settings.json ---------------------------------------------------------------------------------

def test_install_keeps_everything_else_and_uninstall_removes_only_ours(tmp_path):
    f = tmp_path / "settings.json"
    mine = {"type": "command", "command": "echo mine"}
    f.write_text(json.dumps({"theme": "dark", "hooks": {"Stop": [{"hooks": [mine]}]}}), encoding="utf-8")
    ch.install(f, URL)
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["theme"] == "dark"
    assert ch.is_installed(f)
    assert {"Notification", "StopFailure", "UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd"} <= set(data["hooks"])
    assert mine in [h for g in data["hooks"]["Stop"] for h in g["hooks"]]
    assert (tmp_path / "settings.json.pixelpanel-backup").exists()

    ch.install(f, URL)  # twice: no duplicates
    urls = [h.get("url") for g in json.loads(f.read_text(encoding="utf-8"))["hooks"]["Stop"] for h in g["hooks"]]
    assert urls.count(URL) == 1

    ch.uninstall(f)
    data = json.loads(f.read_text(encoding="utf-8"))
    assert not ch.is_installed(f)
    assert data == {"theme": "dark", "hooks": {"Stop": [{"hooks": [mine]}]}}


def test_uninstall_leaves_no_empty_hooks_section(tmp_path):
    f = tmp_path / "settings.json"
    ch.install(f, URL)  # the file did not exist yet
    ch.uninstall(f)
    assert json.loads(f.read_text(encoding="utf-8")) == {}


def test_broken_settings_file_is_not_touched(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ch.HookError):
        ch.install(f, URL)
    assert f.read_text(encoding="utf-8") == "{ not json"


# --- local server ----------------------------------------------------------------------------------

def _post(url, body=b"{}"):
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.status, r.read()


def test_server_accepts_only_the_right_token_on_localhost():
    got = []
    server = ch.HookServer("tok123", got.append, port=0)
    server.start()
    try:
        base = f"http://127.0.0.1:{server.port}/pixelpanel"
        status, body = _post(f"{base}/tok123", json.dumps(perm()).encode())
        assert status == 200 and body == b""  # empty 2xx = success for Claude Code
        assert got and got[0]["session_id"] == "s1"
        with pytest.raises(urllib.error.HTTPError):
            _post(f"{base}/wrong", json.dumps(perm()).encode())
        assert len(got) == 1
        assert server.host == "127.0.0.1"
    finally:
        server.stop()


def test_only_needed_fields_reach_the_app():
    got = []
    server = ch.HookServer("t", got.append, port=0)
    server.start()
    try:
        secret = {**perm(), "last_assistant_message": "secret text", "tool_input": {"a": 1}, "prompt": "secret"}
        _post(f"http://127.0.0.1:{server.port}/pixelpanel/t", json.dumps(secret).encode())
        assert set(got[0]) <= {"hook_event_name", "session_id", "cwd", "notification_type"}
    finally:
        server.stop()


# --- panel -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["input", "idle", "error"])
def test_alert_texts_fit_next_to_clawd(kind):
    for count in (1, 2, 12):
        text = pages.attention_title(ch.Summary(kind, "x", count))
        assert font.text_width(text) <= 47, text


def test_alert_page_has_a_waving_clawd_and_keeps_a_gap_from_the_text():
    s = ch.Summary("input", "proj", 1)
    out = pages.render_attention(s, 64, 16)
    assert out.animated and out.frames[0].size == (64, 16)
    assert len({f.crop((0, 0, 16, 16)).tobytes() for f in out.frames}) > 1  # Clawd moves
    assert len({f.crop((17, 0, 64, 16)).tobytes() for f in out.frames}) == 1  # the text stands still


def test_long_project_names_scroll_while_clawd_keeps_waving():
    out = pages.render_attention(ch.Summary("input", "a-very-long-project-name-indeed", 1), 64, 16)
    assert len({f.crop((17, 0, 64, 16)).tobytes() for f in out.frames}) > 1
    assert len({f.crop((0, 0, 16, 16)).tobytes() for f in out.frames}) > 1


def test_clawd_pulls_the_alert_in_from_the_right():
    from PIL import Image
    old = Image.new("RGB", (64, 16), (0, 0, 200))
    final = pages.render_attention(ch.Summary("input", "proj", 1), 64, 16).frames[0]
    pull = pages.attention_pull(old, final)
    first, middle = pull.frames[0], pull.frames[len(pull.frames) // 2]
    assert first.getpixel((10, 8)) == (0, 0, 200)  # still the old page on the left
    assert middle.getpixel((2, 8)) == (0, 0, 200) and middle.getpixel((63, 0)) != (0, 0, 200)
    assert pull.frames[-1].tobytes() == final.tobytes()  # ends exactly on the page
    assert pull.durations[-1] >= 3000


def test_engine_takeover_then_rotation_while_pending():
    from pixelpanel.engine import Engine
    cfg = config.default_config()
    cfg["claude"]["attention"]["enabled"] = True
    cfg["claude"]["attention"]["seconds"] = 10
    cfg["pages"] = [config.new_page("clock")]
    e = Engine(cfg)
    e.ctx.teams = "Busy"
    e.attention_event(perm(), now=100.0)
    page, _, light = e._choose(e.config, 105.0)
    assert page["type"] == "attention" and page.get("pull") and not light  # beats the status light
    e.ctx.teams = "Available"
    page, _, _ = e._choose(e.config, 111.0)  # takeover over, still pending: part of the rotation
    assert any(p["type"] == "attention" for p in e._rotation(e.config))
    e.attention_event(ev("PostToolUse"), now=112.0)
    assert e.ctx.attention is None
    assert not any(p["type"] == "attention" for p in e._rotation(e.config))


def test_engine_ignores_events_when_switched_off():
    from pixelpanel.engine import Engine
    e = Engine(config.default_config())  # off by default
    e.attention_event(perm(), now=1.0)
    assert e.ctx.attention is None


def test_alert_signature_changes_with_the_summary():
    p = {"id": "_a", "type": "attention"}
    now = datetime(2026, 11, 5, 9, 0)
    a = pages.signature(p, pages.Context(attention=ch.Summary("input", "x", 1)), now)
    b = pages.signature(p, pages.Context(attention=ch.Summary("input", "x", 2)), now)
    assert a != b
