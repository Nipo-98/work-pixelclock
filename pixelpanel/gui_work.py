"""Window sections of the work features; mixed into MainWindow."""

from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime

from PySide6.QtCore import Qt, QTime, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QTimeEdit, QVBoxLayout, QWidget,
)

from . import api, config, hotkeys, work

log = logging.getLogger(__name__)


def _hint(text: str) -> QLabel:
    label = QLabel(text, objectName="hint")
    label.setWordWrap(True)
    return label


def _status() -> QLabel:
    label = QLabel("–", objectName="muted")
    label.setWordWrap(True)
    return label


class WorkSections:
    """Needs self.cfg, self.engine, self._changed(), self._set_sub() and self._reload_pages()."""

    # --- section: work -----------------------------------------------------------------------------

    def _build_work_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        box = QGroupBox("Focus timer")
        form = QFormLayout(box)
        row = QHBoxLayout()
        self.focus_button = QPushButton("Start focus", objectName="primary")
        self.focus_button.clicked.connect(self.engine.focus_toggle)
        minutes = self._spin("focus", "minutes", 1, 240, " min focus")
        pause = self._spin("focus", "break", 0, 120, " min break")
        row.addWidget(self.focus_button)
        row.addWidget(minutes)
        row.addWidget(pause)
        row.addStretch(1)
        form.addRow(row)
        self.focus_status = _status()
        form.addRow("Now", self.focus_status)
        form.addRow("", self._check("focus", "takeover", "Show it instead of the rotation while it runs"))
        lay.addWidget(box)

        box = QGroupBox("Stopwatch")
        form = QFormLayout(box)
        row = QHBoxLayout()
        self.watch_button = QPushButton("Start")
        self.watch_button.clicked.connect(self.engine.stopwatch_toggle)
        reset = QPushButton("Reset")
        reset.clicked.connect(self.engine.stopwatch_reset)
        label = QLineEdit(self.cfg["stopwatch"].get("label", "Project"))
        label.setMaxLength(9)
        label.setPlaceholderText("Project")
        label.setFixedWidth(130)
        label.editingFinished.connect(lambda: self._set_sub("stopwatch", "label", label.text().strip()))
        self.watch_status = QLabel("0:00", objectName="showing")
        row.addWidget(self.watch_button)
        row.addWidget(reset)
        row.addWidget(label)
        row.addWidget(self.watch_status)
        row.addStretch(1)
        form.addRow(row)
        form.addRow(_hint("It shows up on the panel when you start or stop it and stays in the rotation while it runs "
                          "– tick the Stopwatch page to keep it there when stopped. Starts at zero when the app restarts."))
        lay.addWidget(box)

        box = QGroupBox("Door sign")
        outer = QVBoxLayout(box)
        row = QHBoxLayout()
        for sign in self.cfg.get("signs", []):
            b = QPushButton(sign)
            b.clicked.connect(lambda _=False, s=sign: self._show_sign(s))
            row.addWidget(b)
        self.sign_clear = QPushButton("Take it down")
        self.sign_clear.clicked.connect(self.engine.clear_message)
        row.addWidget(self.sign_clear)
        row.addStretch(1)
        outer.addLayout(row)
        self.sign_status = _status()
        outer.addWidget(self.sign_status)
        outer.addWidget(_hint("A sign stays until you take it down, press Next page or start the focus timer. "
                              "Notes from Clawd and On air show over it; afterwards it comes back."))
        lay.addWidget(box)

        box = QGroupBox("On air")
        form = QFormLayout(box)
        form.addRow("", self._check("on_air", "enabled", "Show ON AIR while an app uses the microphone or camera"))
        row = QHBoxLayout()
        row.addWidget(self._check("on_air", "mic", "Microphone"))
        row.addWidget(self._check("on_air", "camera", "Camera"))
        row.addStretch(1)
        form.addRow("Watch", row)
        self.air_status = _status()
        form.addRow("Now", self.air_status)
        form.addRow(_hint("Works with any app (Teams, Zoom, browser), read from Windows' privacy data. Windows only "
                          "tells that the microphone is in use – not whether you are muted."))
        lay.addWidget(box)

        box = QGroupBox("Reminders")
        form = QFormLayout(box)
        row = QHBoxLayout()
        row.addWidget(self._check("break", "enabled", "Remind me to take a break after"))
        row.addWidget(self._spin("break", "minutes", 15, 240, " min"))
        row.addStretch(1)
        form.addRow(row)
        row = QHBoxLayout()
        row.addWidget(self._check("limit", "enabled", "Warn when Claude usage reaches (%)"))
        numbers = QLineEdit(str(self.cfg["limit"].get("thresholds", "80, 90")))
        numbers.setFixedWidth(100)
        numbers.editingFinished.connect(lambda: self._set_sub("limit", "thresholds", numbers.text()))
        row.addWidget(numbers)
        row.addStretch(1)
        form.addRow(row)
        form.addRow(_hint("Being away from the PC for 5 minutes counts as a break."))
        lay.addWidget(box)
        lay.addStretch(1)

        self._stuck_shown = False
        self._timer_tick = QTimer(self, interval=1000, timeout=self._update_timers)
        self._timer_tick.start()
        return w

    def _check(self, section: str, key: str, text: str) -> QCheckBox:
        cb = QCheckBox(text)
        cb.setChecked(bool(self.cfg[section].get(key)))
        cb.toggled.connect(lambda v: self._set_sub(section, key, v))
        return cb

    def _spin(self, section: str, key: str, low: int, high: int, suffix: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setSuffix(suffix)
        spin.setValue(int(self.cfg[section].get(key, low)))
        spin.valueChanged.connect(lambda v: self._set_sub(section, key, v))
        return spin

    def _show_sign(self, text: str) -> None:
        page = {**config.new_page("text"), "text": text, "icon": "", "color": "#ffffff"}
        self.engine.show_message(page, None)

    def _update_timers(self) -> None:
        now = time.monotonic()
        focus = self.engine.focus
        showing = self.engine.status.showing
        if focus.phase:
            self.focus_button.setText("Stop")
            text = f"{focus.phase.capitalize()}: {work.clock_text(focus.remaining(now))} left"
            if self.cfg["focus"].get("takeover", True) and showing not in ("", "Focus timer"):
                text += f" – right now the panel shows: {showing}"
            self.focus_status.setText(text)
        else:
            self.focus_button.setText("Start focus")
            self.focus_status.setText("Not running")
        watch = self.engine.stopwatch
        self.watch_button.setText("Stop" if watch.running else "Start")
        self.watch_status.setText(work.clock_text(watch.elapsed(now)))
        message = self.engine._message
        self.sign_clear.setEnabled(message is not None)
        self.sign_status.setText(f"Showing: {message.page.get('text', '')}" if message else "No sign is up")
        self.hold_button.setVisible(message is not None)
        self.air_status.setText(self.engine.status.on_air or ("Off" if not self.cfg["on_air"].get("enabled") else "Checking …"))
        if self.engine.report_if_stuck() and not self._stuck_shown:
            self._stuck_shown = True
            self.error_label.setText("The panel control does not respond. Details are in the log; restart the app if it stays.")
            self.error_label.show()
        elif self._stuck_shown and time.monotonic() - self.engine.last_tick < 5:
            self._stuck_shown = False
            self.error_label.hide()

    # --- section: connections ----------------------------------------------------------------------

    def _build_github_box(self) -> QGroupBox:
        gh = self.cfg["github"]
        box = QGroupBox("GitHub")
        form = QFormLayout(box)
        form.addRow("", self._check("github", "enabled", "Fetch build status, issues and pull requests"))
        form.addRow("Repositories", self._text("github", "repos", "owner/repo, owner/other-repo"))
        form.addRow("Your user name", self._text("github", "user", "for pull requests waiting for your review"))
        token = QLineEdit()
        token.setEchoMode(QLineEdit.EchoMode.Password)
        token.setPlaceholderText("saved" if gh.get("token") else "only needed for private repositories")
        token.editingFinished.connect(lambda: self._save_token(token))
        form.addRow("Token", token)
        row = QHBoxLayout()
        row.addWidget(self._check("github", "alert_ci", "Alert when a build fails"))
        row.addWidget(self._check("github", "alert_issues", "Alert on new issues"))
        row.addStretch(1)
        form.addRow("", row)
        form.addRow("Check every", self._spin("github", "interval", 60, 3600, " s"))
        self.github_status = _status()
        form.addRow("Status", self.github_status)
        form.addRow(_hint("Add the GitHub page under Pages. The token is stored encrypted for your Windows user."))
        return box

    def _text(self, section: str, key: str, placeholder: str) -> QLineEdit:
        field = QLineEdit(str(self.cfg[section].get(key, "")))
        field.setPlaceholderText(placeholder)
        field.editingFinished.connect(lambda: self._set_sub(section, key, field.text().strip()))
        return field

    def _save_token(self, field: QLineEdit) -> None:
        if field.text():
            self._set_sub("github", "token", work.protect(field.text().strip()))
            field.clear()
            field.setPlaceholderText("saved")

    def _build_monitor_box(self) -> QGroupBox:
        mon = self.cfg["monitors"]
        box = QGroupBox("Service monitor")
        form = QFormLayout(box)
        form.addRow("", self._check("monitors", "enabled", "Check these addresses"))
        targets = QPlainTextEdit(mon.get("targets", ""))
        targets.setPlaceholderText("https://example.com/health\nAPI = https://api.example.com")
        targets.setFixedHeight(76)
        self._monitor_targets = targets
        save = QTimer(self, singleShot=True, interval=800,
                      timeout=lambda: self._set_sub("monitors", "targets", targets.toPlainText()))
        targets.textChanged.connect(save.start)
        form.addRow("Addresses", targets)
        form.addRow("Check every", self._spin("monitors", "interval", 15, 3600, " s"))
        form.addRow("", self._check("monitors", "alert", "Alert when one goes down"))
        self.monitor_status = _status()
        form.addRow("Status", self.monitor_status)
        return box

    def _build_team_box(self) -> QGroupBox:
        box = QGroupBox("Team message")
        form = QFormLayout(box)
        form.addRow("", self._check("team", "enabled", "Show a message everyone in the team gets"))
        form.addRow("Source", self._text("team", "source", r"https://… or \\server\share\team-message.txt"))
        form.addRow("Check every", self._spin("team", "interval", 30, 3600, " s"))
        self.team_status = _status()
        form.addRow("Status", self.team_status)
        form.addRow(_hint("Reads the first line of a text file or web address that your team shares. Whoever can change "
                          "that file writes on everyone's panel; nothing is sent back."))
        return box

    def _build_api_box(self) -> QGroupBox:
        cfg = self.cfg["api"]
        box = QGroupBox("Local interface")
        form = QFormLayout(box)
        enable = QCheckBox("Let scripts and tools on this PC write to the panel")
        enable.setChecked(bool(cfg.get("enabled")))
        enable.toggled.connect(self._set_api)
        form.addRow("", enable)
        self.api_example = QLabel("", objectName="muted")
        self.api_example.setWordWrap(True)
        self.api_example.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Example", self.api_example)
        copy = QPushButton("Copy example")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self._api_command()))
        form.addRow("", copy)
        self.api_status = _status()
        form.addRow("Status", self.api_status)
        form.addRow(_hint('Text, or JSON with text, color, icon, effect, seconds and progress: '
                          '{"text": "Build", "progress": 42}. Only reachable from this PC, with the secret in the address.'))
        self._show_api_example()
        return box

    def _api_url(self) -> str:
        return f"http://127.0.0.1:{api.PORT}/api/{self.cfg['api'].get('token', '')}/message"

    def _api_command(self) -> str:
        return f'curl -X POST {self._api_url()} -d "Backup done"'

    def _show_api_example(self) -> None:
        on = self.cfg["api"].get("enabled") and self.cfg["api"].get("token")
        self.api_example.setText(self._api_command() if on else "Switch it on to get your address.")

    def _set_api(self, on: bool) -> None:
        cfg = self.cfg["api"]
        cfg["token"] = cfg.get("token") or secrets.token_urlsafe(24)
        cfg["enabled"] = on
        self._show_api_example()
        self.api_status.setText("Listening" if on else "Off")
        self._changed()

    # --- section: settings -------------------------------------------------------------------------

    def _build_profile_box(self) -> QGroupBox:
        box = QGroupBox("Profiles")
        form = QFormLayout(box)
        self.profile_combo = QComboBox()
        apply_btn = QPushButton("Use", objectName="primary")
        apply_btn.clicked.connect(lambda: self._apply_profile(self.profile_combo.currentText()))
        delete = QPushButton("Delete")
        delete.clicked.connect(self._delete_profile)
        row = QHBoxLayout()
        row.addWidget(self.profile_combo, 1)
        row.addWidget(apply_btn)
        row.addWidget(delete)
        form.addRow("Profile", row)
        self.profile_name = QLineEdit()
        self.profile_name.setPlaceholderText("Work, Focus, Meeting, Evening …")
        self.profile_auto = QCheckBox("Use automatically from")
        self.profile_from, self.profile_to = QTimeEdit(QTime(8, 0)), QTimeEdit(QTime(17, 0))
        for edit in (self.profile_from, self.profile_to):
            edit.setDisplayFormat("HH:mm")
        save = QPushButton("Save current pages as profile")
        save.clicked.connect(self._save_profile)
        form.addRow("New name", self.profile_name)
        row = QHBoxLayout()
        row.addWidget(self.profile_auto)
        row.addWidget(self.profile_from)
        row.addWidget(QLabel("to"))
        row.addWidget(self.profile_to)
        row.addStretch(1)
        form.addRow("", row)
        form.addRow("", save)
        form.addRow(_hint("A profile remembers the ticked pages, brightness and transition."))
        self._reload_profiles()
        self._auto_profile_timer = QTimer(self, interval=30_000, timeout=self._check_auto_profile)
        self._auto_profile_timer.start()
        self._last_auto_profile = None
        return box

    def _reload_profiles(self) -> None:
        self.profile_combo.clear()
        self.profile_combo.addItems([p["name"] for p in self.cfg.get("profiles", [])])
        self.profile_combo.setCurrentText(self.cfg.get("active_profile", ""))
        if hasattr(self, "tray_profiles"):
            self._fill_profile_menu()

    def _save_profile(self) -> None:
        name = self.profile_name.text().strip()
        if not name:
            self.profile_name.setFocus()
            return
        auto = self.profile_auto.isChecked()
        work.save_profile(self.cfg, name, self.profile_from.time().toString("HH:mm") if auto else "",
                          self.profile_to.time().toString("HH:mm") if auto else "")
        self.cfg["active_profile"] = name
        self.profile_name.clear()
        self._reload_profiles()
        self._changed()

    def _delete_profile(self) -> None:
        name = self.profile_combo.currentText()
        self.cfg["profiles"] = [p for p in self.cfg.get("profiles", []) if p["name"] != name]
        if self.cfg.get("active_profile") == name:
            self.cfg["active_profile"] = ""
        self._reload_profiles()
        self._changed()

    def _apply_profile(self, name: str) -> None:
        if work.apply_profile(self.cfg, name):
            self.brightness.blockSignals(True)
            self.brightness.setValue(int(self.cfg.get("brightness", 60)))
            self.bright_label.setText(f"{self.brightness.value()} %")
            self.brightness.blockSignals(False)
            self._reload_pages(max(0, self.page_list.currentRow()))
            self.profile_combo.setCurrentText(name)
            self._changed()

    def _check_auto_profile(self) -> None:
        """Switch when a time range begins; choosing another profile by hand then lasts until the next one."""
        name = work.profile_for_time(self.cfg, datetime.now())
        if name != self._last_auto_profile:
            self._last_auto_profile = name
            if name:
                self._apply_profile(name)

    def _build_hotkey_box(self) -> QGroupBox:
        box = QGroupBox("Keyboard shortcuts")
        form = QFormLayout(box)
        enable = QCheckBox("Use these shortcuts everywhere in Windows")
        enable.setChecked(bool(self.cfg["hotkeys"].get("enabled")))
        enable.toggled.connect(self._set_hotkeys)
        form.addRow("", enable)
        for key, what in hotkeys.SHORTCUTS.values():
            form.addRow(f"Ctrl+Alt+{key}", QLabel(what, objectName="muted"))
        self.hotkey_status = QLabel("", objectName="error")
        self.hotkey_status.setWordWrap(True)
        form.addRow(self.hotkey_status)
        self._hotkeys = hotkeys.Hotkeys({"next": self.engine.next_page, "focus": self.engine.focus_toggle,
                                         "stopwatch": self.engine.stopwatch_toggle, "back": self.engine.clear_message})
        QApplication.instance().installNativeEventFilter(self._hotkeys)
        if self.cfg["hotkeys"].get("enabled"):
            self._register_hotkeys()
        return box

    def _register_hotkeys(self) -> None:
        busy = self._hotkeys.register()
        self.hotkey_status.setText(f"Used by another program: {', '.join(busy)}" if busy else "")

    def _set_hotkeys(self, on: bool) -> None:
        self._set_sub("hotkeys", "enabled", on)
        self._hotkeys.unregister()
        self.hotkey_status.setText("")
        if on:
            self._register_hotkeys()

    def _night_off_check(self) -> QCheckBox:
        return self._check("night", "off", "Switch the panel off completely at night")

    # --- tray --------------------------------------------------------------------------------------

    def _extend_tray(self, menu: QMenu) -> None:
        signs = menu.addMenu("Door sign")
        for sign in self.cfg.get("signs", []):
            signs.addAction(sign, lambda s=sign: self._show_sign(s))
        signs.addSeparator()
        signs.addAction("Take it down", self.engine.clear_message)
        menu.addAction("Focus timer on/off", self.engine.focus_toggle)
        menu.addAction("Stopwatch on/off", self.engine.stopwatch_toggle)
        self.tray_profiles = menu.addMenu("Profile")
        self._fill_profile_menu()

    def _fill_profile_menu(self) -> None:
        self.tray_profiles.clear()
        for profile in self.cfg.get("profiles", []):
            self.tray_profiles.addAction(profile["name"], lambda n=profile["name"]: self._apply_profile(n))
        self.tray_profiles.setEnabled(bool(self.cfg.get("profiles")))

    def _on_work_status(self, st) -> None:
        if st.github:
            self.github_status.setText(st.github)
        if st.monitors:
            self.monitor_status.setText(st.monitors)
        if st.team:
            self.team_status.setText(st.team)
        if st.api:
            self.api_status.setText(st.api)


def page_editor_rows(editor, page: dict) -> None:
    """Settings of the new page types in the page editor."""
    t = page["type"]
    form = editor.form
    if t == "countdown":
        label = QLineEdit(page.get("label", "Home in"))
        label.setMaxLength(9)
        label.textChanged.connect(lambda v: editor._set("label", v))
        form.addRow("Text", label)
        date = QLineEdit(page.get("date", ""))
        date.setPlaceholderText("YYYY-MM-DD, empty = every day")
        date.textChanged.connect(lambda v: editor._set("date", v.strip()))
        form.addRow("Date", date)
        time_edit = QTimeEdit(QTime.fromString(page.get("time", "17:00"), "HH:mm"))
        time_edit.setDisplayFormat("HH:mm")
        time_edit.timeChanged.connect(lambda v: editor._set("time", v.toString("HH:mm")))
        form.addRow("Time", time_edit)
    elif t == "github":
        show = QComboBox()
        for key, text in (("actions", "Build status"), ("prs", "Pull requests to review"), ("issues", "Open issues")):
            show.addItem(text, key)
        show.setCurrentIndex(max(0, show.findData(page.get("show", "actions"))))
        show.currentIndexChanged.connect(lambda _: editor._set("show", show.currentData()))
        form.addRow("Show", show)
    elif t == "system":
        show = QComboBox()
        show.addItem("CPU and memory", "cpu_ram")
        show.addItem("Battery", "battery")
        show.setCurrentIndex(max(0, show.findData(page.get("show", "cpu_ram"))))
        show.currentIndexChanged.connect(lambda _: editor._set("show", show.currentData()))
        form.addRow("Show", show)
    hints = {
        "day": "Meetings left today and how much of an 8 hour day is booked. Needs the calendar under Connections.",
        "focus": "Start the timer under Work.",
        "stopwatch": "Start it under Work, from the tray icon or with a shortcut.",
        "mail": "Unread mails in the inbox of the classic Outlook.",
        "teams_chat": "Unread count from the Teams log file – no sign-in needed.",
        "monitor": "Set the addresses under Connections.",
        "sessions": "Open Claude Code sessions. Needs Claude sessions under Connections.",
        "team": "Set the source under Connections.",
    }
    if t in hints:
        hint = _hint(hints[t])
        form.addRow(hint)

    schedule = page.setdefault("schedule", {"enabled": False, "from": "08:00", "to": "18:00", "weekdays_only": False})
    on = QCheckBox("Only show from")
    on.setChecked(bool(schedule.get("enabled")))
    start, end = QTimeEdit(QTime.fromString(schedule.get("from", "08:00"), "HH:mm")), QTimeEdit(
        QTime.fromString(schedule.get("to", "18:00"), "HH:mm"))
    for edit in (start, end):
        edit.setDisplayFormat("HH:mm")
    weekdays = QCheckBox("Mon–Fri")
    weekdays.setChecked(bool(schedule.get("weekdays_only")))

    def changed() -> None:
        editor._set("schedule", {"enabled": on.isChecked(), "from": start.time().toString("HH:mm"),
                                 "to": end.time().toString("HH:mm"), "weekdays_only": weekdays.isChecked()})

    on.toggled.connect(lambda _: changed())
    weekdays.toggled.connect(lambda _: changed())
    start.timeChanged.connect(lambda _: changed())
    end.timeChanged.connect(lambda _: changed())
    row = QHBoxLayout()
    row.addWidget(on)
    row.addWidget(start)
    row.addWidget(QLabel("to"))
    row.addWidget(end)
    row.addWidget(weekdays)
    row.addStretch(1)
    form.addRow("Schedule", row)


def confirm_note(parent, text: str) -> None:
    QMessageBox.information(parent, "Pixel Panel", text)
