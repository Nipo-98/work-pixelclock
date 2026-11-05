"""Desktop window and tray icon (PySide6)."""

from __future__ import annotations

import copy
import logging
import sys
from io import BytesIO

from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QSize, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QIcon, QImage, QMovie, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea,
    QSlider, QSpinBox, QStackedWidget, QSystemTrayIcon, QTimeEdit, QToolButton, QVBoxLayout, QWidget,
)

import secrets
from concurrent.futures import ThreadPoolExecutor

from . import __version__, autostart, calendars, claude_hooks, config, pages, sources, theme, transitions, updater
from .core import effects, icons
from .engine import Engine, Status

log = logging.getLogger(__name__)

EMOJIS = ["😀", "😎", "🥳", "❤️", "👍", "🔥", "☕", "🍕", "🍺", "🎉", "⭐", "✅", "❌", "⚠️", "💡", "🚀",
          "💻", "📞", "🎧", "🏠", "🌞", "🌧️", "❄️", "🐱", "🐶", "🤖", "👻", "💤", "🍀", "🎵"]
EFFECT_NAMES = {"none": "None", "rainbow": "Rainbow text", "matrix": "Matrix", "twinkle": "Twinkle",
                "fire": "Fire", "plasma": "Plasma", "snow": "Snow", "rain": "Rain", "pulse": "Pulse"}
ORIENTATION_NAMES = {"landscape": "Normal", "upside_down": "Upside down",
                     "portrait_left": "Portrait (left end up)", "portrait_right": "Portrait (right end up)"}
CLAUDE_SHOW = {"both": "Session (5 h) + week", "session": "Session (5 h) only, with bar",
               "week": "Week only, with bar"}
CALENDAR_SHOW = {"details": "Title and time", "time": "Time only (no titles)",
                 "free_busy": "Free till / Busy till"}
PREVIEW_WIDTH = 640


def app_icon() -> QIcon:
    """A tiny LED panel with a heart, drawn at a few sizes."""
    icon = QIcon()
    heart = icons.builtin_icon("heart")
    for size in (16, 32, 48, 64, 256):
        img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
        dark = Image.new("RGBA", (16, 12), (20, 20, 28, 255))
        img.paste(dark, (0, 2))
        img.paste(heart, (4, 4), heart)
        img = img.resize((size, size), Image.Resampling.NEAREST)
        icon.addPixmap(pil_to_pixmap(img))
    return icon


def glyph_icon(glyph: str, color: str = theme.TEXT) -> QIcon:
    """Icon from the Windows 11 symbol font (Segoe Fluent Icons); empty icon where it is missing."""
    family = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in QFontDatabase.families()), None)
    if family is None:
        return QIcon()
    icon = QIcon()
    for mode, c in ((QIcon.Mode.Normal, color), (QIcon.Mode.Selected, theme.ACCENT)):
        pm = QPixmap(40, 40)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        font = QFont(family)
        font.setPixelSize(30)
        p.setFont(font)
        p.setPen(QColor(c))
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
        p.end()
        icon.addPixmap(pm, mode)
    return icon


def scrolled(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidget(widget)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    return area


def pil_to_pixmap(img: Image.Image) -> QPixmap:
    buf = BytesIO()
    img.save(buf, format="PNG")
    pm = QPixmap()
    pm.loadFromData(buf.getvalue(), "PNG")
    return pm


class Bridge(QObject):
    status = Signal(object)
    preview = Signal(bytes, str, str)


class ColorButton(QPushButton):
    changed = Signal(str)

    def __init__(self, value: str = "#ffffff") -> None:
        super().__init__()
        self.setFixedWidth(80)
        self.clicked.connect(self._pick)
        self.set(value)

    def set(self, value: str) -> None:
        self.value = value or "#ffffff"
        self.setText(self.value)
        fg = "#000" if QColor(self.value).lightness() > 128 else "#fff"
        self.setStyleSheet(f"background:{self.value}; color:{fg}; border:1px solid {theme.LINE}; "
                           "border-radius:6px; padding:5px 8px;")

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self.value), self, "Choose a colour")
        if c.isValid():
            self.set(c.name())
            self.changed.emit(self.value)


class IconField(QWidget):
    """Emoji, built-in icon name, LaMetric id (lm:1234) or an image file."""

    changed = Signal(str)

    def __init__(self, value: str = "") -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.addItem("")
        self.combo.addItems(EMOJIS)
        self.combo.addItems(icons.BUILTIN_NAMES)
        self.combo.setToolTip("Emoji, built-in icon, LaMetric icon (e.g. lm:2867) or an image file")
        self.combo.setCurrentText(value or "")
        self.combo.currentTextChanged.connect(self.changed.emit)
        browse = QToolButton()
        browse.setText("…")
        browse.setToolTip("Use an image file as the icon")
        browse.clicked.connect(self._browse)
        lay.addWidget(self.combo, 1)
        lay.addWidget(browse)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose an icon", "", "Images (*.png *.gif *.jpg *.bmp)")
        if path:
            self.combo.setCurrentText(path)


def effect_combo(value: str) -> QComboBox:
    combo = QComboBox()
    for key in effects.EFFECTS:
        combo.addItem(EFFECT_NAMES.get(key, key), key)
    combo.setCurrentIndex(max(0, combo.findData(value)))
    return combo


class PageEditor(QWidget):
    """Form for one page; edits the dict in place and emits changed."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.page: dict | None = None
        self.form = QFormLayout(self)

    def edit(self, page: dict | None) -> None:
        self.page = page
        while self.form.rowCount():
            self.form.removeRow(0)
        if page is None:
            self.form.addRow(QLabel("Pick a page on the left or add one with + Page."))
            return
        t = page["type"]
        self.form.addRow(QLabel(f"<b>{config.PAGE_TYPES[t]}</b>"))
        dur = QSpinBox()
        dur.setRange(2, 3600)
        dur.setSuffix(" s")
        dur.setValue(int(page.get("duration", 10)))
        dur.valueChanged.connect(lambda v: self._set("duration", v))
        self.form.addRow("Duration", dur)

        if t == "text":
            text = QLineEdit(page.get("text", ""))
            text.setPlaceholderText("Text – an emoji in the text becomes the icon")
            text.textChanged.connect(lambda v: self._set("text", v))
            self.form.addRow("Text", text)
        if t in ("text", "date"):
            icon = IconField(page.get("icon", ""))
            icon.changed.connect(lambda v: self._set("icon", v))
            self.form.addRow("Icon / Emoji", icon)
        if t in ("clock", "date", "text", "weather"):
            color = ColorButton(page.get("color", "#ffffff"))
            color.changed.connect(lambda v: self._set("color", v))
            self.form.addRow("Farbe", color)
            eff = effect_combo(page.get("effect", "none"))
            eff.currentIndexChanged.connect(lambda _: self._set("effect", eff.currentData()))
            self.form.addRow("Effekt", eff)
        if t == "clock":
            for key, label in (("seconds", "Show seconds"), ("weekday_bar", "Weekday bar")):
                cb = QCheckBox(label)
                cb.setChecked(bool(page.get(key)))
                cb.toggled.connect(lambda v, k=key: self._set(k, v))
                self.form.addRow("", cb)
        if t == "native_clock":
            style = QSpinBox()
            style.setRange(0, 8)
            style.setValue(int(page.get("style", 1)))
            style.valueChanged.connect(lambda v: self._set("style", v))
            self.form.addRow("Design (0–8)", style)
            cb = QCheckBox("Show date")
            cb.setChecked(bool(page.get("show_date", True)))
            cb.toggled.connect(lambda v: self._set("show_date", v))
            self.form.addRow("", cb)
            self.form.addRow(QLabel("The clock runs on the panel itself (no preview)."))
        if t == "image":
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            path = QLineEdit(page.get("path", ""))
            path.textChanged.connect(lambda v: self._set("path", v))
            btn = QPushButton("Browse…")
            btn.clicked.connect(lambda: self._browse_image(path))
            lay.addWidget(path, 1)
            lay.addWidget(btn)
            self.form.addRow("Image / GIF", row)
            self.form.addRow(QLabel("Scaled down to 64×16; animated GIFs play on the panel."))
        if t == "weather":
            self.form.addRow(QLabel("Set the location under Settings."))
        if t == "teams":
            self.form.addRow(QLabel("Shows your current Teams status as a coloured dot.\n"
                                    "The status light (whole panel) is set under Connections."))
        if t == "calendar":
            self.form.addRow(QLabel("Shows time and title of your next meeting.\n"
                                    "Calendar source and reminder are set under Connections."))
        if t == "claude":
            show = QComboBox()
            for key, label in CLAUDE_SHOW.items():
                show.addItem(label, key)
            show.setCurrentIndex(max(0, show.findData(page.get("show", "both"))))
            show.currentIndexChanged.connect(lambda _: self._set("show", show.currentData()))
            self.form.addRow("Show", show)
            logo = QComboBox()
            for key, label in pages.CLAUDE_LOGOS.items():
                logo.addItem(QIcon(pil_to_pixmap(pages.CUSTOM_ICONS[key]().resize((32, 32 * pages.CUSTOM_ICONS[key]().height // 16), Image.Resampling.NEAREST))), label, key)
            logo.setCurrentIndex(max(0, logo.findData(page.get("logo", "claude"))))
            self.form.addRow("Icon", logo)
            animate = QCheckBox("Animate Clawd (waves, hops, sits down, blinks)")
            animate.setChecked(bool(page.get("animate", True)))
            animate.toggled.connect(lambda v: self._set("animate", v))
            animate.setVisible(logo.currentData() == "clawd")
            logo.currentIndexChanged.connect(
                lambda _: (self._set("logo", logo.currentData()), animate.setVisible(logo.currentData() == "clawd")))
            self.form.addRow("", animate)

    def _browse_image(self, field: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose an image", "", "Images (*.png *.gif *.jpg *.jpeg *.bmp *.webp)")
        if path:
            field.setText(path)

    def _set(self, key: str, value) -> None:
        if self.page is not None:
            self.page[key] = value
            self.changed.emit()


def page_title(page: dict) -> str:
    title = config.PAGE_TYPES.get(page["type"], page["type"])
    if page["type"] == "text" and page.get("text"):
        title += f": {page['text'][:24]}"
    if page["type"] == "image" and page.get("path"):
        title += f": {page['path'].replace(chr(92), '/').rsplit('/', 1)[-1]}"
    return f"{title}  ({page.get('duration', 10)} s)"


class MainWindow(QMainWindow):
    def __init__(self, cfg: dict, engine: Engine, bridge: Bridge) -> None:
        super().__init__()
        self.cfg = cfg
        self.engine = engine
        self.setWindowTitle("Pixel Panel")
        self.setWindowIcon(app_icon())
        self.resize(820, 720)
        self._movie: QMovie | None = None
        self._movie_buf: QBuffer | None = None
        self._quitting = False
        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=self._save_now)
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gui")
        self._updating = False
        self.restart_exe = None  # set after an update: start this exe once the app has quit
        self.restart_args: list[str] = []
        self._update_timer = QTimer(self, interval=updater.CHECK_EVERY_H * 3600 * 1000,
                                    timeout=lambda: self._check_updates())
        self._update_timer.start()
        QTimer.singleShot(15_000, lambda: self._check_updates())  # first check shortly after start

        central = QWidget(objectName="root")
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QWidget(objectName="sidebar")
        sidebar.setFixedWidth(200)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 18, 12, 14)
        brand = QLabel("Pixel Panel", objectName="brand")
        side.addWidget(brand)
        side.addSpacing(14)
        self.nav = QListWidget(objectName="nav")
        self.nav.setIconSize(QSize(18, 18))
        side.addWidget(self.nav, 1)
        self.conn_label = QLabel("Not connected", objectName="conn")
        self.conn_label.setWordWrap(True)
        side.addWidget(self.conn_label)
        outer.addWidget(sidebar)

        main = QWidget(objectName="main")
        body = QVBoxLayout(main)
        body.setContentsMargins(24, 20, 24, 16)
        body.setSpacing(14)
        body.addWidget(self._build_header())
        self.stack = QStackedWidget()
        sections = [
            ("Show now", "\ue768", scrolled(self._build_now_tab())),
            ("Pages", "\ue8fd", self._build_pages_tab()),
            ("Connections", "\ue71b", scrolled(self._build_live_tab())),
            ("Settings", "\ue713", scrolled(self._build_settings_tab())),
        ]
        for title, glyph, widget in sections:
            self.nav.addItem(QListWidgetItem(glyph_icon(glyph), title))
            self.stack.addWidget(widget)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        body.addWidget(self.stack, 1)
        outer.addWidget(main, 1)
        self.setCentralWidget(central)

        bridge.status.connect(self._on_status)
        bridge.preview.connect(self._on_preview)
        self._build_tray()

    # --- header: the panel itself ---------------------------------------------------------------

    def _build_header(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.preview = QLabel(objectName="preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(184)
        lay.addWidget(self.preview)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.showing = QLabel("", objectName="showing")
        nxt = QPushButton("Next page")
        nxt.setIcon(glyph_icon("\ue893"))
        nxt.clicked.connect(self.engine.next_page)
        sun = QLabel()
        sun.setPixmap(glyph_icon("\ue706").pixmap(18, 18))
        self.brightness = QSlider(Qt.Orientation.Horizontal)
        self.brightness.setRange(1, 100)
        self.brightness.setFixedWidth(180)
        self.brightness.setValue(int(self.cfg.get("brightness", 60)))
        self.brightness.setToolTip("Brightness")
        self.bright_label = QLabel(f"{self.brightness.value()} %", objectName="muted")
        self.bright_label.setFixedWidth(40)
        self.brightness.valueChanged.connect(lambda v: self.bright_label.setText(f"{v} %"))
        self.brightness.sliderReleased.connect(lambda: self._set_cfg("brightness", self.brightness.value()))
        self.brightness.valueChanged.connect(
            lambda v: None if self.brightness.isSliderDown() else self._set_cfg("brightness", v))
        self.power = QPushButton(objectName="power")
        self.power.setCheckable(True)
        self.power.setIcon(glyph_icon("\ue7e8"))
        self.power.setChecked(self.cfg.get("power", True))
        self.power.setText("On" if self.power.isChecked() else "Off")
        self.power.toggled.connect(lambda v: (self.power.setText("On" if v else "Off"), self._set_cfg("power", v)))
        row.addWidget(self.showing, 1)
        row.addWidget(nxt)
        row.addSpacing(12)
        row.addWidget(sun)
        row.addWidget(self.brightness)
        row.addWidget(self.bright_label)
        row.addWidget(self.power)
        lay.addLayout(row)
        self.error_label = QLabel("", objectName="error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        lay.addWidget(self.error_label)
        return w

    def _build_panel_settings(self) -> QGroupBox:
        box = QGroupBox("Panel")
        form = QFormLayout(box)
        row = QHBoxLayout()
        self.devices = QComboBox()
        self.devices.setMinimumWidth(260)
        if self.cfg.get("address"):
            self.devices.addItem(f"{self.cfg.get('name') or ''} ({self.cfg['address']})", self.cfg["address"])
        scan = QPushButton("Search")
        scan.clicked.connect(self._scan)
        connect = QPushButton("Connect", objectName="primary")
        connect.clicked.connect(self._connect_selected)
        row.addWidget(self.devices, 1)
        row.addWidget(scan)
        row.addWidget(connect)
        form.addRow("Device", row)
        self.scan_info = QLabel("", objectName="muted")
        form.addRow("", self.scan_info)
        orient = QComboBox()
        for key, label in ORIENTATION_NAMES.items():
            orient.addItem(label, key)
        orient.setCurrentIndex(max(0, orient.findData(self.cfg.get("orientation", "landscape"))))
        orient.currentIndexChanged.connect(lambda _: self._set_cfg("orientation", orient.currentData()))
        form.addRow("Orientation", orient)
        return box

    # --- tab: show now ---------------------------------------------------------------------------

    def _build_now_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        box = QGroupBox("Text / Emoji")
        form = QFormLayout(box)
        self.now_text = QLineEdit()
        self.now_text.setPlaceholderText("e.g. Back in 5 min ☕")
        self.now_text.returnPressed.connect(self._send_text)
        self.now_icon = IconField("")
        self.now_color = ColorButton("#ffbe00")
        self.now_effect = effect_combo("none")
        self.now_secs = QSpinBox()
        self.now_secs.setRange(0, 86400)
        self.now_secs.setValue(0)
        self.now_secs.setSuffix(" s")
        self.now_secs.setSpecialValueText("until Back to rotation")
        form.addRow("Text", self.now_text)
        form.addRow("Icon / Emoji", self.now_icon)
        form.addRow("Colour", self.now_color)
        form.addRow("Effect", self.now_effect)
        form.addRow("Duration", self.now_secs)
        buttons = QHBoxLayout()
        send = QPushButton("Show", objectName="primary")
        send.setDefault(True)
        send.clicked.connect(self._send_text)
        add = QPushButton("Add to rotation")
        add.clicked.connect(self._text_to_rotation)
        buttons.addWidget(send)
        buttons.addWidget(add)
        buttons.addStretch(1)
        form.addRow(buttons)
        lay.addWidget(box)

        box = QGroupBox("Image, GIF or built-in clock")
        row = QHBoxLayout(box)
        img = QPushButton("Show image / GIF…")
        img.clicked.connect(self._send_image)
        self.native_style = QSpinBox()
        self.native_style.setRange(0, 8)
        self.native_style.setValue(1)
        native = QPushButton("Show built-in clock")
        native.clicked.connect(lambda: self.engine.show_message(
            {**config.new_page("native_clock"), "style": self.native_style.value()}, None))
        row.addWidget(img)
        row.addSpacing(20)
        row.addWidget(QLabel("Clock design"))
        row.addWidget(self.native_style)
        row.addWidget(native)
        row.addStretch(1)
        lay.addWidget(box)

        back = QPushButton("Back to rotation")
        back.clicked.connect(self.engine.clear_message)
        lay.addWidget(back, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)
        return w

    def _now_page(self) -> dict:
        return {**config.new_page("text"), "text": self.now_text.text(), "icon": self.now_icon.combo.currentText(),
                "color": self.now_color.value, "effect": self.now_effect.currentData()}

    def _send_text(self) -> None:
        self.engine.show_message(self._now_page(), self.now_secs.value() or None)

    def _text_to_rotation(self) -> None:
        self.cfg["pages"].append(self._now_page())
        self._reload_pages(len(self.cfg["pages"]) - 1)
        self._changed()

    def _send_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose an image", "", "Images (*.png *.gif *.jpg *.jpeg *.bmp *.webp)")
        if path:
            self.engine.show_message({**config.new_page("image"), "path": path}, self.now_secs.value() or None)

    # --- tab: pages ------------------------------------------------------------------------------

    def _build_pages_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        left = QVBoxLayout()
        trans_row = QHBoxLayout()
        trans = QComboBox()
        for key, label in transitions.TRANSITIONS.items():
            trans.addItem(label, key)
        trans.setCurrentIndex(max(0, trans.findData(self.cfg.get("transition", "push_left"))))
        trans.currentIndexChanged.connect(lambda _: self._set_cfg("transition", trans.currentData()))
        trans.setToolTip("How the next page comes onto the panel")
        trans_row.addWidget(QLabel("Transition"))
        trans_row.addWidget(trans, 1)
        left.addLayout(trans_row)
        self.page_list = QListWidget()
        self.page_list.currentRowChanged.connect(self._select_page)
        self.page_list.itemChanged.connect(self._page_checked)
        left.addWidget(QLabel("Ticked pages rotate, top to bottom."))
        left.addWidget(self.page_list, 1)
        btns = QHBoxLayout()
        add = QToolButton()
        add.setText("+ Page")
        add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(add)
        for key, label in config.PAGE_TYPES.items():
            menu.addAction(label, lambda k=key: self._add_page(k))
        add.setMenu(menu)
        btns.addWidget(add)
        for text, slot in (("Remove", self._remove_page), ("▲", lambda: self._move_page(-1)),
                           ("▼", lambda: self._move_page(1))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            btns.addWidget(b)
        left.addLayout(btns)
        lay.addLayout(left, 2)
        self.editor = PageEditor()
        self.editor.changed.connect(self._page_edited)
        lay.addWidget(self.editor, 3)
        self._reload_pages(0)
        return w

    def _reload_pages(self, select: int | None = None) -> None:
        self.page_list.blockSignals(True)
        self.page_list.clear()
        for page in self.cfg["pages"]:
            item = QListWidgetItem(page_title(page))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if page.get("enabled") else Qt.CheckState.Unchecked)
            self.page_list.addItem(item)
        self.page_list.blockSignals(False)
        if select is not None and self.cfg["pages"]:
            self.page_list.setCurrentRow(max(0, min(select, len(self.cfg["pages"]) - 1)))
        else:
            self.editor.edit(None)

    def _select_page(self, row: int) -> None:
        self.editor.edit(self.cfg["pages"][row] if 0 <= row < len(self.cfg["pages"]) else None)

    def _page_checked(self, item: QListWidgetItem) -> None:
        row = self.page_list.row(item)
        self.cfg["pages"][row]["enabled"] = item.checkState() == Qt.CheckState.Checked
        self._changed()

    def _page_edited(self) -> None:
        row = self.page_list.currentRow()
        if 0 <= row < self.page_list.count():
            self.page_list.blockSignals(True)
            self.page_list.item(row).setText(page_title(self.cfg["pages"][row]))
            self.page_list.blockSignals(False)
        self._changed()

    def _add_page(self, page_type: str) -> None:
        self.cfg["pages"].append(config.new_page(page_type))
        self._reload_pages(len(self.cfg["pages"]) - 1)
        self._changed()

    def _remove_page(self) -> None:
        row = self.page_list.currentRow()
        if 0 <= row < len(self.cfg["pages"]):
            del self.cfg["pages"][row]
            self._reload_pages(row)
            self._changed()

    def _move_page(self, delta: int) -> None:
        row = self.page_list.currentRow()
        new = row + delta
        pages = self.cfg["pages"]
        if 0 <= row < len(pages) and 0 <= new < len(pages):
            pages[row], pages[new] = pages[new], pages[row]
            self._reload_pages(new)
            self._changed()

    # --- section: connections (Teams, Claude, calendar) ---------------------------------------------------------------------

    def _build_live_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        teams = self.cfg["teams"]
        box = QGroupBox("Microsoft Teams")
        form = QFormLayout(box)
        self.teams_now = QLabel("–")
        self.teams_now.setStyleSheet("font-weight:bold")
        form.addRow("Current status", self.teams_now)
        en = QCheckBox("Read Teams status")
        en.setChecked(teams.get("enabled", True))
        en.toggled.connect(lambda v: self._set_sub("teams", "enabled", v))
        form.addRow("", en)
        frame_cb = QCheckBox("Status bar – a stripe in the Teams colour on the left and right")
        frame_cb.setChecked(teams.get("status_frame", True))
        frame_cb.toggled.connect(lambda v: self._set_sub("teams", "status_frame", v))
        bar_width = QComboBox()
        for n in (1, 2, 3):
            bar_width.addItem(f"{n} px wide", n)
        bar_width.setCurrentIndex(max(0, bar_width.findData(int(teams.get("bar_width", 2)))))
        bar_width.currentIndexChanged.connect(lambda _: self._set_sub("teams", "bar_width", bar_width.currentData()))
        frame_cb.toggled.connect(bar_width.setEnabled)
        bar_width.setEnabled(frame_cb.isChecked())
        row = QHBoxLayout()
        row.addWidget(frame_cb)
        row.addWidget(bar_width)
        row.addStretch(1)
        form.addRow("", row)
        self.light_cb = QCheckBox("Status light – the whole panel lights up and the rotation pauses for:")
        self.light_cb.setToolTip("Only tick states that should really stop the rotation "
                                 "(e.g. Busy). Only Show now takes priority over it.")
        self.light_cb.setChecked(teams.get("status_light", True))
        self.light_cb.toggled.connect(lambda v: self._set_sub("teams", "status_light", v))
        form.addRow("", self.light_cb)
        states = QWidget()
        grid = QHBoxLayout(states)
        grid.setContentsMargins(20, 0, 0, 0)
        col = QVBoxLayout()
        for i, (key, (label, _short, _color)) in enumerate(sources.TEAMS_STATES.items()):
            cb = QCheckBox(label)
            cb.setChecked(key in teams.get("light_states", []))
            cb.toggled.connect(lambda v, k=key: self._toggle_light_state(k, v))
            col.addWidget(cb)
            if i % 4 == 3:
                grid.addLayout(col)
                col = QVBoxLayout()
        col.addStretch(1)
        grid.addLayout(col)
        grid.addStretch(1)
        form.addRow(states)
        hint = QLabel("The status is read from the Teams log file – no sign-in, no admin rights. "
                      "It changes as soon as Teams changes it.", objectName="hint")
        hint.setWordWrap(True)
        form.addRow(hint)
        lay.addWidget(box)

        claude = self.cfg["claude"]
        box = QGroupBox("Claude usage")
        form = QFormLayout(box)
        self.claude_now = QLabel("–")
        self.claude_now.setStyleSheet("font-weight:bold")
        self.claude_now.setWordWrap(True)
        form.addRow("Now", self.claude_now)
        en = QCheckBox("Fetch usage")
        en.setChecked(claude.get("enabled", True))
        en.toggled.connect(lambda v: self._set_sub("claude", "enabled", v))
        form.addRow("", en)
        interval = QSpinBox()
        interval.setRange(30, 3600)
        interval.setSuffix(" s")
        interval.setValue(int(claude.get("interval", 120)))
        interval.valueChanged.connect(lambda v: self._set_sub("claude", "interval", v))
        refresh = QPushButton("Refresh now")
        refresh.clicked.connect(self.engine.refresh_claude)
        row = QHBoxLayout()
        row.addWidget(interval)
        row.addWidget(refresh)
        row.addStretch(1)
        form.addRow("Check every", row)
        hint = QLabel("Uses the Claude Code sign-in on this PC. Session is the 5-hour limit, week the weekly limit "
                      "of your plan – the same numbers as claude.ai › Settings › Usage.",
                      objectName="hint")
        hint.setWordWrap(True)
        form.addRow(hint)
        lay.addWidget(box)
        lay.addWidget(self._build_attention_box())
        lay.addWidget(self._build_calendar_box())
        lay.addStretch(1)
        return w

    # --- tab: calendar --------------------------------------------------------------------------

    def _build_attention_box(self) -> QGroupBox:
        cfg = self.cfg["claude"]["attention"]
        box = QGroupBox("Claude sessions")
        form = QFormLayout(box)
        self.attention_cb = QCheckBox("Alert on the panel when a Claude Code session needs attention")
        self.attention_cb.setChecked(bool(cfg.get("enabled")))
        self.attention_cb.toggled.connect(self._set_attention)
        form.addRow("", self.attention_cb)
        kinds = QHBoxLayout()
        for key, label, tip in (("input", "Needs a decision", "Waits for a permission or an answer"),
                                ("idle", "Finished", "Done and waiting for you for about a minute"),
                                ("error", "Error", "The turn ended with an API error")):
            cb = QCheckBox(label)
            cb.setToolTip(tip)
            cb.setChecked(bool(cfg.get(key, True)))
            cb.toggled.connect(lambda v, k=key: self._set_attention_option(k, v))
            kinds.addWidget(cb)
        kinds.addStretch(1)
        form.addRow("Alert for", kinds)
        row = QHBoxLayout()
        seconds = QSpinBox()
        seconds.setRange(3, 600)
        seconds.setSuffix(" s")
        seconds.setValue(int(cfg.get("seconds", 15)))
        seconds.valueChanged.connect(lambda v: self._set_attention_option("seconds", v))
        test = QPushButton("Send test alert")
        test.clicked.connect(self._test_attention)
        row.addWidget(seconds)
        row.addWidget(test)
        row.addStretch(1)
        form.addRow("Show for", row)
        self.attention_status = QLabel("Off", objectName="muted")
        self.attention_status.setWordWrap(True)
        form.addRow("Status", self.attention_status)
        hint = QLabel("Clawd pulls the alert onto the panel; it stays in the rotation until the session goes on. "
                      "Only the project folder name is used – no prompts or answers. Works for Claude Code on "
                      "this PC (terminal and VS Code).", objectName="hint")
        hint.setWordWrap(True)
        form.addRow(hint)
        return box

    def _set_attention_option(self, key: str, value) -> None:
        self.cfg["claude"]["attention"][key] = value
        self._changed()

    def _test_attention(self) -> None:
        if not self.cfg["claude"]["attention"].get("enabled"):
            self.attention_status.setText("Switch the alerts on first.")
            return
        self.engine.attention_test()

    def _set_attention(self, on: bool) -> None:
        cfg = self.cfg["claude"]["attention"]
        try:
            if on:
                answer = QMessageBox.question(
                    self, "Claude session alerts",
                    "Pixel Panel will add hooks to Claude Code's settings:\n"
                    f"{claude_hooks.SETTINGS_FILE}\n\n"
                    "Claude Code then reports to this PC only (127.0.0.1) when a session needs you. Your other "
                    "settings stay as they are and a backup is made. Switching this off removes the hooks again.")
                if answer != QMessageBox.StandardButton.Yes:
                    self._revert_attention(False)
                    return
                cfg["token"] = cfg.get("token") or secrets.token_urlsafe(24)
                claude_hooks.install(claude_hooks.SETTINGS_FILE, f"{claude_hooks.URL_BASE}/{cfg['token']}")
            else:
                claude_hooks.uninstall(claude_hooks.SETTINGS_FILE)
        except claude_hooks.HookError as err:
            log.warning("Claude hooks: %s", err)
            QMessageBox.warning(self, "Claude session alerts", str(err))
            self._revert_attention(not on)
            return
        cfg["enabled"] = on
        self.attention_status.setText("Listening – no session needs you right now" if on else "Off")
        self._changed()

    def _revert_attention(self, value: bool) -> None:
        self.attention_cb.blockSignals(True)
        self.attention_cb.setChecked(value)
        self.attention_cb.blockSignals(False)

    def _build_calendar_box(self) -> QGroupBox:
        cal = self.cfg["calendar"]
        box = QGroupBox("Calendar")
        form = QFormLayout(box)
        en = QCheckBox("Fetch next meeting")
        en.setChecked(cal.get("enabled", False))
        en.toggled.connect(lambda v: self._set_sub("calendar", "enabled", v))
        form.addRow("", en)
        provider = QComboBox()
        for key, label in calendars.PROVIDERS.items():
            provider.addItem(label, key)
        provider.setCurrentIndex(max(0, provider.findData(cal.get("provider", "outlook"))))
        form.addRow("Source", provider)

        # one block of settings per source; only the chosen one is visible
        outlook = QLabel("Reads the calendar from classic Outlook on this PC – no sign-in. "
                         "Outlook has to be set up; it starts hidden in the background when needed.",
                         objectName="hint")
        outlook.setWordWrap(True)
        ics = QWidget()
        ics_form = QFormLayout(ics)
        ics_form.setContentsMargins(0, 0, 0, 0)
        ics_url = QLineEdit(cal.get("ics_url", ""))
        ics_url.setPlaceholderText("https://outlook.office365.com/owa/calendar/…/calendar.ics")
        ics_url.editingFinished.connect(lambda: self._set_sub("calendar", "ics_url", ics_url.text().strip()))
        ics_form.addRow("Link", ics_url)
        ics_hint = QLabel("Outlook on the web › Settings › Calendar › Shared calendars › Publish a calendar "
                          "› copy the ICS link. Anyone with the link can see the meetings.",
                          objectName="hint")
        ics_hint.setWordWrap(True)
        ics_form.addRow(ics_hint)
        graph = QWidget()
        graph_form = QFormLayout(graph)
        graph_form.setContentsMargins(0, 0, 0, 0)
        for key, label in (("client_id", "Application ID"), ("tenant_id", "Directory ID")):
            field = QLineEdit(cal.get(key, ""))
            field.setPlaceholderText("xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
            field.editingFinished.connect(lambda f=field, k=key: self._set_sub("calendar", k, f.text().strip()))
            graph_form.addRow(label, field)
        row = QHBoxLayout()
        sign_in = QPushButton("Sign in", objectName="primary")
        sign_in.setToolTip("Opens the Microsoft sign-in in the browser (once)")
        sign_in.clicked.connect(self._calendar_sign_in)
        sign_out = QPushButton("Sign out")
        sign_out.clicked.connect(self.engine.calendar_sign_out)
        row.addWidget(sign_in)
        row.addWidget(sign_out)
        row.addStretch(1)
        graph_form.addRow("", row)
        graph_hint = QLabel("Needs an app registration in your organisation's Microsoft directory (see the README).",
                            objectName="hint")
        graph_hint.setWordWrap(True)
        graph_form.addRow(graph_hint)
        blocks = {"outlook": outlook, "ics": ics, "graph": graph}
        for block in blocks.values():
            form.addRow(block)

        def show_block() -> None:
            for key, block in blocks.items():
                block.setVisible(key == provider.currentData())

        provider.currentIndexChanged.connect(lambda _: (show_block(), self._set_sub("calendar", "provider",
                                                                                    provider.currentData())))
        show_block()
        self.cal_status = QLabel("–", objectName="muted")
        self.cal_status.setWordWrap(True)
        form.addRow("Status", self.cal_status)
        self.cal_next = QLabel("–")
        self.cal_next.setStyleSheet("font-weight:bold")
        self.cal_next.setWordWrap(True)
        form.addRow("Next meeting", self.cal_next)
        remind = QSpinBox()
        remind.setRange(0, 60)
        remind.setSuffix(" min before")
        remind.setSpecialValueText("off")
        remind.setValue(int(cal.get("remind_minutes", 5)))
        remind.valueChanged.connect(lambda v: self._set_sub("calendar", "remind_minutes", v))
        form.addRow("Reminder", remind)
        show = QComboBox()
        for key, label in CALENDAR_SHOW.items():
            show.addItem(label, key)
        show.setCurrentIndex(max(0, show.findData(cal.get("show", "details"))))
        show.currentIndexChanged.connect(lambda _: self._set_sub("calendar", "show", show.currentData()))
        form.addRow("Show on the panel", show)
        cal_icon = QCheckBox("Show the calendar icon (off: more room, bigger time)")
        cal_icon.setChecked(bool(cal.get("icon", True)))
        cal_icon.toggled.connect(lambda v: self._set_sub("calendar", "icon", v))
        form.addRow("", cal_icon)
        hint = QLabel("Shortly before a meeting the reminder shows a countdown (in 4 min); it takes priority over "
                      "the status light and the rotation. Add the Next meeting page under Pages.",
                      objectName="hint")
        hint.setWordWrap(True)
        form.addRow(hint)
        return box

    def _calendar_sign_in(self) -> None:
        self.cal_status.setText("Sign-in running in the browser …")
        fut = self.engine.calendar_sign_in()

        def done() -> None:
            if not fut.done():
                QTimer.singleShot(300, done)
                return
            try:
                self.cal_status.setText(f"Signed in as {fut.result()}")
            except Exception as err:  # noqa: BLE001 - shown to the user
                self.cal_status.setText(f"Sign-in failed: {err}")

        QTimer.singleShot(300, done)

    def _toggle_light_state(self, key: str, on: bool) -> None:
        states = set(self.cfg["teams"].get("light_states", []))
        states.add(key) if on else states.discard(key)
        self._set_sub("teams", "light_states", sorted(states))

    # --- tab: settings ---------------------------------------------------------------------------

    def _build_settings_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._build_panel_settings())
        night = self.cfg["night"]
        box = QGroupBox("Night mode")
        form = QFormLayout(box)
        en = QCheckBox("Dimmer at night")
        en.setChecked(night.get("enabled", False))
        en.toggled.connect(lambda v: self._set_sub("night", "enabled", v))
        form.addRow("", en)
        for key, label in (("start", "From"), ("end", "To")):
            te = QTimeEdit(QTime.fromString(night.get(key, "22:00"), "HH:mm"))
            te.setDisplayFormat("HH:mm")
            te.timeChanged.connect(lambda t, k=key: self._set_sub("night", k, t.toString("HH:mm")))
            form.addRow(label, te)
        nb = QSpinBox()
        nb.setRange(0, 100)
        nb.setSuffix(" %")
        nb.setValue(int(night.get("brightness", 5)))
        nb.valueChanged.connect(lambda v: self._set_sub("night", "brightness", v))
        form.addRow("Night brightness", nb)
        lay.addWidget(box)

        box = QGroupBox("Weather")
        form = QFormLayout(box)
        row = QHBoxLayout()
        self.city = QLineEdit(self.cfg["weather"].get("city", ""))
        self.city.setPlaceholderText("e.g. Cologne")
        self.city.returnPressed.connect(self._find_city)
        find = QPushButton("Set location")
        find.clicked.connect(self._find_city)
        row.addWidget(self.city, 1)
        row.addWidget(find)
        form.addRow("Location", row)
        self.weather_now = QLabel("–")
        form.addRow("Now", self.weather_now)
        lay.addWidget(box)

        box = QGroupBox("App")
        form = QFormLayout(box)
        auto = QCheckBox("Start with Windows")
        auto.setChecked(autostart.is_enabled())
        auto.toggled.connect(self._set_autostart)
        form.addRow("", auto)
        mini = QCheckBox("Start minimised (tray icon only)")
        mini.setChecked(self.cfg.get("start_minimized", False))
        mini.toggled.connect(lambda v: self._set_cfg("start_minimized", v))
        form.addRow("", mini)
        auto_update = QCheckBox("Install updates automatically")
        auto_update.setToolTip("Checks GitHub for a new release at start and every few hours, "
                               "downloads it, verifies it and restarts the app.")
        auto_update.setChecked(self.cfg.get("auto_update", True))
        auto_update.toggled.connect(lambda v: self._set_cfg("auto_update", v))
        form.addRow("", auto_update)
        row = QHBoxLayout()
        check = QPushButton("Check for New Version")
        check.clicked.connect(lambda: self._check_updates(manual=True))
        self.update_status = QLabel(f"Version {__version__}", objectName="muted")
        self.update_status.setWordWrap(True)
        row.addWidget(check)
        row.addWidget(self.update_status, 1)
        form.addRow("Version", row)
        path = QLabel(f"Settings file: {config.CONFIG_FILE}")
        path.setObjectName("hint")
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(path)
        lay.addWidget(box)
        lay.addStretch(1)
        return w

    def _find_city(self) -> None:
        name = self.city.text().strip()
        if not name:
            self.cfg["weather"] = {"city": "", "lat": None, "lon": None}
            self._changed()
            return
        try:
            hit = sources.geocode(name)
        except OSError as err:
            QMessageBox.warning(self, "Weather", f"Location search not reachable:\n{err}")
            return
        if not hit:
            QMessageBox.information(self, "Weather", f"\"{name}\" not found.")
            return
        label, lat, lon = hit
        self.city.setText(label)
        self.cfg["weather"] = {"city": label, "lat": lat, "lon": lon}
        self._changed()

    def _set_autostart(self, on: bool) -> None:
        try:
            autostart.set_enabled(on)
        except OSError as err:
            QMessageBox.warning(self, "Autostart", str(err))

    # --- config plumbing -------------------------------------------------------------------------

    def _set_cfg(self, key: str, value) -> None:
        self.cfg[key] = value
        self._changed()

    def _set_sub(self, section: str, key: str, value) -> None:
        self.cfg[section][key] = value
        self._changed()

    def _changed(self) -> None:
        self.engine.update_config(self.cfg)
        self._save_timer.start()

    def _save_now(self) -> None:
        try:
            config.save(self.cfg)
        except OSError as err:
            log.warning("save failed: %s", err)

    # --- connection ------------------------------------------------------------------------------

    def _scan(self) -> None:
        self.scan_info.setText("Searching for panels …")
        fut = self.engine.scan()

        def done() -> None:
            if not fut.done():
                QTimer.singleShot(200, done)
                return
            try:
                panels = fut.result()
            except Exception as err:  # noqa: BLE001
                self.scan_info.setText(f"Search failed: {err}")
                return
            self.devices.clear()
            for address, name, rssi in panels:
                self.devices.addItem(f"{name} ({address}, {rssi} dBm)", (address, name))
            self.scan_info.setText(f"{len(panels)} panel(s) found" if panels else
                                   "No panel found. Is it switched on and the phone app closed?")
            if len(panels) == 1:
                self._connect_selected()

        QTimer.singleShot(200, done)

    def _connect_selected(self) -> None:
        data = self.devices.currentData()
        if not data:
            self._scan()
            return
        address, name = data if isinstance(data, tuple) else (data, self.cfg.get("name"))
        self.cfg["address"], self.cfg["name"] = address, name
        self._changed()
        self.engine.reconnect()

    # --- engine callbacks ------------------------------------------------------------------------

    def _on_status(self, st: Status) -> None:
        self.conn_label.setText(f"●  {st.connection}")
        self.conn_label.setProperty("ok", st.connected)
        self.conn_label.style().polish(self.conn_label)
        self.showing.setText(f"On the panel: {st.showing}" if st.showing else "")
        self.error_label.setVisible(bool(st.error))
        self.error_label.setText(st.error or "")
        label, color = sources.teams_label(st.teams)
        self.teams_now.setText(label if st.teams else "no status found in the Teams log yet")
        self.teams_now.setStyleSheet(f"font-weight:bold; color:rgb{color}")
        c = st.claude
        if c is None:
            self.claude_now.setText("–")
        elif c.error:
            self.claude_now.setText(c.error)
        else:
            def fmt(pct, reset):
                days = pages.WEEKDAYS
                when = f", resets {days[reset.weekday()]} {reset:%H:%M}" if reset else ""
                return f"{pct:.0f} %{when}" if pct is not None else "?"
            self.claude_now.setText(f"Session: {fmt(c.session, c.session_reset)}\n"
                                    f"Week: {fmt(c.week, c.week_reset)}" + (f"\nPlan: {c.plan}" if c.plan else ""))
        if st.calendar:
            self.cal_status.setText(st.calendar)
        if st.attention:
            self.attention_status.setText(st.attention)
        m = st.meeting
        if m is None:
            self.cal_next.setText("–")
        else:
            start, end = m.start.astimezone(), m.end.astimezone()
            days = pages.WEEKDAYS
            self.cal_next.setText(f"{days[start.weekday()]} {start:%H:%M}–{end:%H:%M}  {m.subject}"
                                  + (f"  ({m.location})" if m.location else ""))
        wx = st.weather
        if wx:
            self.weather_now.setText(f"{wx.temperature:.0f} °C ({wx.icon}), today {wx.low:.0f}–{wx.high:.0f} °C")
        tip = f"Pixel Panel – {st.connection}"
        if st.teams:
            tip += f"\nTeams: {label}"
        self.tray.setToolTip(tip)

    def _on_preview(self, data: bytes, mime: str, label: str) -> None:
        if self._movie is not None:
            self._movie.stop()
            self._movie = None
        if mime == "native":
            self.preview.setPixmap(QPixmap())
            self.preview.setText(f"<span style='color:#aaa'>{label} – runs on the panel (no preview)</span>")
            return
        img = QImage.fromData(data)
        if img.isNull():
            return
        scale = max(1, PREVIEW_WIDTH // max(1, img.width())) if img.width() < PREVIEW_WIDTH else 1
        size = QSize(img.width() * scale, img.height() * scale)
        if size.width() > PREVIEW_WIDTH or size.height() > 300:
            size.scale(PREVIEW_WIDTH, 300, Qt.AspectRatioMode.KeepAspectRatio)
        if mime == "image/gif":
            self._movie_buf = QBuffer(self)
            self._movie_buf.setData(QByteArray(data))
            self._movie_buf.open(QIODevice.OpenModeFlag.ReadOnly)
            self._movie = QMovie(self._movie_buf, b"gif", self)
            self._movie.setScaledSize(size)
            self.preview.setMovie(self._movie)
            self._movie.start()
        else:
            self.preview.setPixmap(QPixmap.fromImage(img).scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                                                                 Qt.TransformationMode.FastTransformation))

    # --- tray ------------------------------------------------------------------------------------

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        menu.addAction("Open", self.show_window)
        menu.addAction("Next page", self.engine.next_page)
        menu.addAction("Back to rotation", self.engine.clear_message)
        menu.addSeparator()
        self.tray_power = QAction("Panel on", menu, checkable=True)
        self.tray_power.setChecked(self.cfg.get("power", True))
        self.tray_power.toggled.connect(self.power.setChecked)
        self.power.toggled.connect(self.tray_power.setChecked)
        menu.addAction(self.tray_power)
        tray_light = QAction("Teams status light", menu, checkable=True)
        tray_light.setChecked(self.cfg["teams"].get("status_light", True))
        tray_light.toggled.connect(self.light_cb.setChecked)
        self.light_cb.toggled.connect(tray_light.setChecked)
        menu.addAction(tray_light)
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_window() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.setToolTip("Pixel Panel")
        self.tray.show()
        self._tray_hint_shown = False

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt name
        if self._quitting:
            event.accept()
            return
        event.ignore()
        self.hide()
        if not self._tray_hint_shown:
            self.tray.showMessage("Pixel Panel keeps running",
                                  "The panel is still controlled. Quit from the tray icon.",
                                  QSystemTrayIcon.MessageIcon.Information, 4000)
            self._tray_hint_shown = True

    # --- updates --------------------------------------------------------------------------------

    def _run_in_background(self, fn, on_done, *args) -> None:
        """Run fn(*args) in a worker thread and call on_done(result, error) in the GUI thread."""
        fut = self._pool.submit(fn, *args)

        def poll() -> None:
            if not fut.done():
                QTimer.singleShot(200, poll)
                return
            err = fut.exception()
            on_done(None if err else fut.result(), err)

        QTimer.singleShot(200, poll)

    def _check_updates(self, manual: bool = False) -> None:
        if self._updating:
            return
        if manual:
            self.update_status.setText("Checking GitHub …")

        def done(release, err) -> None:
            if err is not None:
                log.info("update check failed: %s", err)
                if manual:
                    self.update_status.setText(f"Could not check for updates: {err}")
                return
            if release is None:
                if manual:
                    self.update_status.setText(f"Version {__version__} – this is the latest version.")
                return
            self._offer_update(release, manual)

        self._run_in_background(updater.check, done)

    def _offer_update(self, release, manual: bool) -> None:
        new = release.tag.lstrip("v")
        self.update_status.setText(f"Version {new} is available (you have {__version__}).")
        if updater.running_exe() is None:
            self.update_status.setText(self.update_status.text() + " Updates install only in the exe – "
                                       "this copy runs from the source code.")
            return
        if manual:
            answer = QMessageBox.question(self, "Update available",
                                          f"Pixel Panel {new} is available (you have {__version__}).\n\n"
                                          "Download and install it now? The app restarts afterwards.")
            if answer == QMessageBox.StandardButton.Yes:
                self._install_update(release)
        elif self.cfg.get("auto_update", True):
            self._install_update(release)
        else:
            self.tray.showMessage("Pixel Panel update", f"Version {new} is available – "
                                  "Settings › Check for New Version.", QSystemTrayIcon.MessageIcon.Information, 6000)

    def _install_update(self, release) -> None:
        exe = updater.running_exe()
        if exe is None or self._updating:
            return
        self._updating = True
        new = release.tag.lstrip("v")
        progress = {"done": 0, "total": release.size}

        def show_progress() -> None:
            if self._updating and progress["total"]:
                pct = 100 * progress["done"] // progress["total"]
                self.update_status.setText(f"Downloading version {new} … {pct} %")
                QTimer.singleShot(300, show_progress)

        def work() -> None:
            path = updater.download(release, exe.parent, lambda d, t: progress.update(done=d))
            updater.swap(path, exe)

        def done(_result, err) -> None:
            self._updating = False
            if err is not None:
                log.warning("update failed: %s", err)
                self.update_status.setText(f"Update failed: {err}")
                return
            log.info("updated to %s, restarting", release.tag)
            self.tray.showMessage("Pixel Panel updated", f"Restarting with version {new} …",
                                  QSystemTrayIcon.MessageIcon.Information, 3000)
            self.restart_args = [] if self.isVisible() else ["--minimized"]
            self.restart_exe = exe
            QTimer.singleShot(800, self.quit)

        show_progress()
        self._run_in_background(work, done)

    def quit(self) -> None:
        self._quitting = True
        self._save_now()
        self.tray.hide()
        self.engine.stop()
        QApplication.quit()


def run(start_hidden: bool = False) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Pixel Panel")
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")  # the native Windows style ignores parts of the stylesheet
    app.setStyleSheet(theme.stylesheet(config.APP_DIR))
    app.setWindowIcon(app_icon())

    from PySide6.QtCore import QLockFile
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(config.APP_DIR / "pixelpanel.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "Pixel Panel", "Pixel Panel is already running (tray icon).")
        return 0

    updater.cleanup()  # remove the previous version (PixelPanel.exe.old) after an update
    cfg = config.load()
    bridge = Bridge()
    engine = Engine(copy.deepcopy(cfg), on_status=bridge.status.emit, on_preview=bridge.preview.emit)
    window = MainWindow(cfg, engine, bridge)
    engine.start()
    if not (start_hidden or cfg.get("start_minimized")):
        window.show()
    if not cfg.get("address"):
        window.show()
        QTimer.singleShot(300, window._scan)  # first start: find the panel
    code = app.exec()
    lock.unlock()  # before the restart, or the new version would find the app "already running"
    if window.restart_exe is not None:
        updater.restart(window.restart_exe, window.restart_args)
    return code
