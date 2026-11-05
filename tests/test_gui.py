"""Build the real window without showing it and use the new controls."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from pixelpanel import config, gui, work
from pixelpanel.engine import Engine, Status


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(Engine, "_sync_api", lambda self: None)  # no real server in a test
    cfg = config.default_config()
    win = gui.MainWindow(cfg, Engine(cfg), gui.Bridge())
    yield win
    win._save_timer.stop()
    win._timer_tick.stop()
    win._update_timer.stop()
    win._auto_profile_timer.stop()
    win.tray.hide()
    win.deleteLater()


def test_all_sections_exist(window):
    names = [window.nav.item(i).text() for i in range(window.nav.count())]
    assert names == ["Show now", "Pages", "Work", "Connections", "Settings"]
    for i in range(window.nav.count()):
        window.nav.setCurrentRow(i)
        QApplication.processEvents()


@pytest.mark.parametrize("page_type", list(config.PAGE_TYPES))
def test_editor_opens_for_every_page_type(window, page_type):
    window._add_page(page_type)
    QApplication.processEvents()
    assert window.editor.page["type"] == page_type
    assert "schedule" in window.editor.page


def test_schedule_is_saved_with_the_page(window):
    window._add_page("clock")
    window.editor._set("schedule", {"enabled": True, "from": "09:00", "to": "12:00", "weekdays_only": True})
    assert window.cfg["pages"][-1]["schedule"]["from"] == "09:00"
    assert window.engine.config["pages"][-1]["schedule"]["enabled"] is True


def test_focus_button_starts_and_stops(window):
    window.focus_button.click()
    assert window.engine.focus.phase == "focus"
    window._update_timers()
    assert window.focus_button.text() == "Stop" and "left" in window.focus_status.text()
    window.focus_button.click()
    assert window.engine.focus.phase is None
    window._update_timers()
    assert window.focus_button.text() == "Start focus"


def test_stopwatch_buttons(window):
    window.watch_button.click()
    assert window.engine.stopwatch.running
    window._update_timers()
    assert window.watch_button.text() == "Stop"
    window.watch_button.click()
    assert not window.engine.stopwatch.running


def test_door_sign_buttons_show_a_message(window):
    buttons = [b for b in window.findChildren(QPushButton) if b.text() == "Do not disturb"]
    assert buttons
    buttons[0].click()
    assert window.engine._message.page["text"] == "Do not disturb"
    window.engine.clear_message()
    assert window.engine._message is None


def test_profiles_from_the_window(window):
    first = window.cfg["pages"][0]["id"]
    for page in window.cfg["pages"]:
        page["enabled"] = page["id"] == first
    window.profile_name.setText("Focus")
    window._save_profile()
    assert window.profile_combo.findText("Focus") >= 0
    for page in window.cfg["pages"]:
        page["enabled"] = True
    window._apply_profile("Focus")
    assert [p["id"] for p in window.engine.config["pages"] if p["enabled"]] == [first]
    assert window.page_list.item(0).checkState().name == "Checked"
    window._delete_profile()
    assert window.profile_combo.findText("Focus") < 0


def test_token_never_stays_in_plain_text(window):
    from PySide6.QtWidgets import QLineEdit
    field = QLineEdit("ghp_secret")
    window._save_token(field)
    assert "ghp_secret" not in str(window.cfg["github"]) and field.text() == ""
    assert work.unprotect(window.cfg["github"]["token"]) == "ghp_secret"


def test_api_switch_creates_a_secret_address(window):
    window._set_api(True)
    assert window.cfg["api"]["token"] and window.cfg["api"]["token"] in window.api_example.text()
    assert window._api_command().startswith("curl -X POST http://127.0.0.1:")
    window._set_api(False)
    assert window.cfg["api"]["token"] not in window.api_example.text()


def test_status_reaches_the_new_labels(window):
    window._on_status(Status(github="CI: passing", monitors="All 2 reachable", team="Now: Lunch", api="Listening"))
    assert window.github_status.text() == "CI: passing"
    assert window.monitor_status.text() == "All 2 reachable"
    assert window.team_status.text() == "Now: Lunch"
    assert window.api_status.text() == "Listening"


def test_tray_has_the_work_entries(window):
    titles = [a.text() for a in window.tray.contextMenu().actions()]
    for title in ("Door sign", "Focus timer on/off", "Stopwatch on/off", "Profile"):
        assert title in titles
