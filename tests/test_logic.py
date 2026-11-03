from datetime import datetime

import pytest

from pixelpanel import config, pages, sources
from pixelpanel.core.render import Rendered

LOG_LINE = ("2026-11-03T12:23:16.356777+01:00 0x00003440 <INFO> native_modules::UserDataCrossCloudModule: "
            "Received Action: UserPresenceAction: {{cloud_context: https://teams.microsoft.com, availability: {}}}\n")
NOISE = "2026-11-03T12:23:16 <INFO> BroadcastGlobalState: { availability: PresenceUnknown, unread: 0 }\n"


def test_last_presence_ignores_unknown():
    assert sources.last_presence(LOG_LINE.format("Busy") + NOISE) == "Busy"
    assert sources.last_presence(NOISE) is None


def test_teams_watcher_follows_appends_and_rotation(tmp_path):
    old = tmp_path / "MSTeams_2026-11-03_11-00-00.01.log"
    old.write_text(LOG_LINE.format("Available"), encoding="utf-8")
    w = sources.TeamsWatcher(tmp_path)
    assert w.poll() == "Available"

    with open(old, "a", encoding="utf-8") as f:
        f.write(LOG_LINE.format("Busy"))
        f.write(LOG_LINE.format("DoNotDisturb")[:40])  # half written line is not read yet
    assert w.poll() == "Busy"

    import os
    import time
    new = tmp_path / "MSTeams_2026-11-03_13-00-00.02.log"
    new.write_text(NOISE, encoding="utf-8")
    t = time.time() + 5
    os.utime(new, (t, t))
    assert w.poll() == "Busy"  # empty new file keeps the last state
    with open(new, "a", encoding="utf-8") as f:
        f.write(LOG_LINE.format("Away"))
    assert w.poll() == "Away"


def test_parse_claude_usage():
    u = sources.parse_claude_usage({
        "five_hour": {"utilization": 50.0, "resets_at": "2026-11-03T18:20:00.46141+01:00"},
        "seven_day": {"utilization": 40.0, "resets_at": "2026-11-09T03:00:00+01:00"},
    }, "pro")
    assert (u.session, u.week, u.plan) == (50.0, 40.0, "pro")
    assert u.session_reset.hour == 18


def test_split_emoji():
    assert pages.split_emoji("Kaffee ☕ Pause") == ("Kaffee Pause", "☕")
    assert pages.split_emoji("❤️ Liebe") == ("Liebe", "❤️")
    assert pages.split_emoji("Hallo Welt") == ("Hallo Welt", None)


def test_emoji_icon_is_16px():
    icon = pages.emoji_icon("😀")
    assert icon is not None and icon.size == (16, 16)


def test_wmo_icon():
    assert sources.wmo_icon(0) == "sun"
    assert sources.wmo_icon(0, is_day=False) == "moon"
    assert sources.wmo_icon(63) == "rain"
    assert sources.wmo_icon(1234) == "cloud"


@pytest.mark.parametrize("page_type", list(config.PAGE_TYPES))
def test_every_page_type_renders(page_type):
    ctx = pages.Context(
        teams="Busy",
        claude=sources.ClaudeUsage(session=50, week=40),
        weather=sources.Weather(12.4, "partlycloudy", 14, 6),
    )
    page = config.new_page(page_type)
    out = pages.render_page(page, ctx, 64, 16, datetime(2026, 11, 3, 14, 5))
    if page_type == "native_clock":
        assert isinstance(out, pages.Native)
    else:
        assert isinstance(out, Rendered) and out.frames[0].size == (64, 16)


def test_signature_changes_with_data():
    page = config.new_page("teams")
    now = datetime(2026, 11, 3, 14, 5)
    assert pages.signature(page, pages.Context(teams="Busy"), now) != pages.signature(page, pages.Context(teams="Away"), now)
    clock = config.new_page("clock")
    assert pages.signature(clock, pages.Context(), now) != pages.signature(clock, pages.Context(), now.replace(minute=6))


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "c.json")
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    cfg = config.load()
    cfg["brightness"] = 33
    config.save(cfg)
    assert config.load()["brightness"] == 33
