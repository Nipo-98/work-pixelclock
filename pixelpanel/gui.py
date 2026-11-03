"""Desktop window and tray icon (PySide6)."""

from __future__ import annotations

import copy
import logging
import sys
from io import BytesIO

from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QMovie, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton,
    QSlider, QSpinBox, QSystemTrayIcon, QTabWidget, QTimeEdit, QToolButton, QVBoxLayout, QWidget,
)
from PySide6.QtCore import QTime

from . import autostart, config, sources
from .core import effects, icons
from .engine import Engine, Status

log = logging.getLogger(__name__)

EMOJIS = ["😀", "😎", "🥳", "❤️", "👍", "🔥", "☕", "🍕", "🍺", "🎉", "⭐", "✅", "❌", "⚠️", "💡", "🚀",
          "💻", "📞", "🎧", "🏠", "🌞", "🌧️", "❄️", "🐱", "🐶", "🤖", "👻", "💤", "🍀", "🎵"]
EFFECTS_DE = {"none": "Keiner", "rainbow": "Regenbogen-Text", "matrix": "Matrix", "twinkle": "Funkeln",
              "fire": "Feuer", "plasma": "Plasma", "snow": "Schnee", "rain": "Regen", "pulse": "Pulsieren"}
ORIENTATIONS_DE = {"landscape": "Normal", "upside_down": "Auf dem Kopf",
                   "portrait_left": "Hochkant (links oben)", "portrait_right": "Hochkant (rechts oben)"}
CLAUDE_SHOW = {"both": "Sitzung (5 h) + Woche", "session": "Nur Sitzung (5 h) mit Balken",
               "week": "Nur Woche mit Balken"}
PREVIEW_WIDTH = 512


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
        self.setStyleSheet(f"background:{self.value}; color:{fg}; border:1px solid #555; padding:3px;")

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self.value), self, "Farbe wählen")
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
        self.combo.setToolTip("Emoji, eingebautes Icon, LaMetric-Icon (z. B. lm:2867) oder Bilddatei")
        self.combo.setCurrentText(value or "")
        self.combo.currentTextChanged.connect(self.changed.emit)
        browse = QToolButton()
        browse.setText("…")
        browse.setToolTip("Bilddatei als Icon wählen")
        browse.clicked.connect(self._browse)
        lay.addWidget(self.combo, 1)
        lay.addWidget(browse)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Icon wählen", "", "Bilder (*.png *.gif *.jpg *.bmp)")
        if path:
            self.combo.setCurrentText(path)


def effect_combo(value: str) -> QComboBox:
    combo = QComboBox()
    for key in effects.EFFECTS:
        combo.addItem(EFFECTS_DE.get(key, key), key)
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
            self.form.addRow(QLabel("Links eine Seite wählen oder mit „+“ hinzufügen."))
            return
        t = page["type"]
        self.form.addRow(QLabel(f"<b>{config.PAGE_TYPES[t]}</b>"))
        dur = QSpinBox()
        dur.setRange(2, 3600)
        dur.setSuffix(" s")
        dur.setValue(int(page.get("duration", 10)))
        dur.valueChanged.connect(lambda v: self._set("duration", v))
        self.form.addRow("Anzeigedauer", dur)

        if t == "text":
            text = QLineEdit(page.get("text", ""))
            text.setPlaceholderText("Text – Emojis im Text werden zum Icon")
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
            for key, label in (("seconds", "Sekunden anzeigen"), ("weekday_bar", "Wochentag-Leiste")):
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
            cb = QCheckBox("Datum anzeigen")
            cb.setChecked(bool(page.get("show_date", True)))
            cb.toggled.connect(lambda v: self._set("show_date", v))
            self.form.addRow("", cb)
            self.form.addRow(QLabel("Die Uhr läuft im Panel selbst (keine Vorschau)."))
        if t == "image":
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            path = QLineEdit(page.get("path", ""))
            path.textChanged.connect(lambda v: self._set("path", v))
            btn = QPushButton("Durchsuchen…")
            btn.clicked.connect(lambda: self._browse_image(path))
            lay.addWidget(path, 1)
            lay.addWidget(btn)
            self.form.addRow("Bild / GIF", row)
            self.form.addRow(QLabel("Wird auf 64×16 verkleinert; animierte GIFs laufen auf dem Panel."))
        if t == "weather":
            self.form.addRow(QLabel("Den Ort stellst du unter „Einstellungen“ ein."))
        if t == "teams":
            self.form.addRow(QLabel("Zeigt deinen aktuellen Teams-Status als farbigen Punkt.\n"
                                    "Das Statuslicht (ganzes Panel) stellst du unter „Teams & Claude“ ein."))
        if t == "claude":
            show = QComboBox()
            for key, label in CLAUDE_SHOW.items():
                show.addItem(label, key)
            show.setCurrentIndex(max(0, show.findData(page.get("show", "both"))))
            show.currentIndexChanged.connect(lambda _: self._set("show", show.currentData()))
            self.form.addRow("Anzeige", show)

    def _browse_image(self, field: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Bild wählen", "", "Bilder (*.png *.gif *.jpg *.jpeg *.bmp *.webp)")
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

        central = QWidget()
        root = QVBoxLayout(central)
        root.addWidget(self._build_panel_box())
        root.addWidget(self._build_preview())
        tabs = QTabWidget()
        tabs.addTab(self._build_now_tab(), "Sofort anzeigen")
        tabs.addTab(self._build_pages_tab(), "Seiten (Rotation)")
        tabs.addTab(self._build_live_tab(), "Teams && Claude")
        tabs.addTab(self._build_settings_tab(), "Einstellungen")
        root.addWidget(tabs, 1)
        self.setCentralWidget(central)

        bridge.status.connect(self._on_status)
        bridge.preview.connect(self._on_preview)
        self._build_tray()

    # --- top: connection, power, brightness ------------------------------------------------------

    def _build_panel_box(self) -> QGroupBox:
        box = QGroupBox("Panel")
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        self.conn_label = QLabel("Nicht verbunden")
        self.conn_label.setStyleSheet("font-weight:bold")
        self.devices = QComboBox()
        self.devices.setMinimumWidth(230)
        if self.cfg.get("address"):
            self.devices.addItem(f"{self.cfg.get('name') or ''} ({self.cfg['address']})", self.cfg["address"])
        scan = QPushButton("Suchen")
        scan.clicked.connect(self._scan)
        connect = QPushButton("Verbinden")
        connect.clicked.connect(self._connect_selected)
        row.addWidget(self.conn_label, 1)
        row.addWidget(self.devices)
        row.addWidget(scan)
        row.addWidget(connect)
        lay.addLayout(row)

        row = QHBoxLayout()
        self.power = QCheckBox("Panel an")
        self.power.setChecked(self.cfg.get("power", True))
        self.power.toggled.connect(lambda v: self._set_cfg("power", v))
        self.brightness = QSlider(Qt.Orientation.Horizontal)
        self.brightness.setRange(1, 100)
        self.brightness.setValue(int(self.cfg.get("brightness", 60)))
        self.bright_label = QLabel(f"{self.brightness.value()} %")
        self.brightness.valueChanged.connect(lambda v: self.bright_label.setText(f"{v} %"))
        self.brightness.sliderReleased.connect(lambda: self._set_cfg("brightness", self.brightness.value()))
        self.brightness.valueChanged.connect(
            lambda v: None if self.brightness.isSliderDown() else self._set_cfg("brightness", v))
        orient = QComboBox()
        for key, label in ORIENTATIONS_DE.items():
            orient.addItem(label, key)
        orient.setCurrentIndex(max(0, orient.findData(self.cfg.get("orientation", "landscape"))))
        orient.currentIndexChanged.connect(lambda _: self._set_cfg("orientation", orient.currentData()))
        row.addWidget(self.power)
        row.addSpacing(16)
        row.addWidget(QLabel("Helligkeit"))
        row.addWidget(self.brightness, 1)
        row.addWidget(self.bright_label)
        row.addSpacing(16)
        row.addWidget(QLabel("Ausrichtung"))
        row.addWidget(orient)
        lay.addLayout(row)
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color:#c33")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        lay.addWidget(self.error_label)
        return box

    def _build_preview(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(140)
        self.preview.setStyleSheet("background:#0b0b0e; border-radius:6px;")
        row = QHBoxLayout()
        self.showing = QLabel("")
        nxt = QPushButton("Nächste Seite ▶")
        nxt.clicked.connect(self.engine.next_page)
        row.addWidget(QLabel("Auf dem Panel:"))
        row.addWidget(self.showing, 1)
        row.addWidget(nxt)
        lay.addWidget(self.preview)
        lay.addLayout(row)
        return w

    # --- tab: show now ---------------------------------------------------------------------------

    def _build_now_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        box = QGroupBox("Text / Emoji")
        form = QFormLayout(box)
        self.now_text = QLineEdit()
        self.now_text.setPlaceholderText("z. B. Bin in 5 Min zurück ☕")
        self.now_text.returnPressed.connect(self._send_text)
        self.now_icon = IconField("")
        self.now_color = ColorButton("#ffbe00")
        self.now_effect = effect_combo("none")
        self.now_secs = QSpinBox()
        self.now_secs.setRange(0, 86400)
        self.now_secs.setValue(0)
        self.now_secs.setSuffix(" s")
        self.now_secs.setSpecialValueText("bis „Zurück zur Rotation“")
        form.addRow("Text", self.now_text)
        form.addRow("Icon / Emoji", self.now_icon)
        form.addRow("Farbe", self.now_color)
        form.addRow("Effekt", self.now_effect)
        form.addRow("Dauer", self.now_secs)
        buttons = QHBoxLayout()
        send = QPushButton("Anzeigen")
        send.setDefault(True)
        send.clicked.connect(self._send_text)
        add = QPushButton("Als Seite in die Rotation")
        add.clicked.connect(self._text_to_rotation)
        buttons.addWidget(send)
        buttons.addWidget(add)
        buttons.addStretch(1)
        form.addRow(buttons)
        lay.addWidget(box)

        box = QGroupBox("Bild, GIF oder eingebaute Uhr")
        row = QHBoxLayout(box)
        img = QPushButton("Bild / GIF anzeigen…")
        img.clicked.connect(self._send_image)
        self.native_style = QSpinBox()
        self.native_style.setRange(0, 8)
        self.native_style.setValue(1)
        native = QPushButton("Eingebaute Uhr anzeigen")
        native.clicked.connect(lambda: self.engine.show_message(
            {**config.new_page("native_clock"), "style": self.native_style.value()}, None))
        row.addWidget(img)
        row.addSpacing(20)
        row.addWidget(QLabel("Uhr-Design"))
        row.addWidget(self.native_style)
        row.addWidget(native)
        row.addStretch(1)
        lay.addWidget(box)

        back = QPushButton("Zurück zur Rotation")
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
        path, _ = QFileDialog.getOpenFileName(self, "Bild wählen", "", "Bilder (*.png *.gif *.jpg *.jpeg *.bmp *.webp)")
        if path:
            self.engine.show_message({**config.new_page("image"), "path": path}, self.now_secs.value() or None)

    # --- tab: pages ------------------------------------------------------------------------------

    def _build_pages_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        left = QVBoxLayout()
        self.page_list = QListWidget()
        self.page_list.currentRowChanged.connect(self._select_page)
        self.page_list.itemChanged.connect(self._page_checked)
        left.addWidget(QLabel("Haken = in der Rotation. Reihenfolge = Anzeigereihenfolge."))
        left.addWidget(self.page_list, 1)
        btns = QHBoxLayout()
        add = QToolButton()
        add.setText("+ Seite")
        add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(add)
        for key, label in config.PAGE_TYPES.items():
            menu.addAction(label, lambda k=key: self._add_page(k))
        add.setMenu(menu)
        btns.addWidget(add)
        for text, slot in (("Entfernen", self._remove_page), ("▲", lambda: self._move_page(-1)),
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

    # --- tab: Teams & Claude ---------------------------------------------------------------------

    def _build_live_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        teams = self.cfg["teams"]
        box = QGroupBox("Microsoft Teams")
        form = QFormLayout(box)
        self.teams_now = QLabel("–")
        self.teams_now.setStyleSheet("font-weight:bold")
        form.addRow("Aktueller Status", self.teams_now)
        en = QCheckBox("Teams-Status auslesen")
        en.setChecked(teams.get("enabled", True))
        en.toggled.connect(lambda v: self._set_sub("teams", "enabled", v))
        form.addRow("", en)
        self.light_cb = QCheckBox("Statuslicht – bei diesen Status leuchtet das ganze Panel:")
        self.light_cb.setToolTip("Das Statuslicht hat Vorrang vor der Rotation. Nur „Sofort anzeigen“ geht vor.")
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
        hint = QLabel("Der Status wird aus der Teams-Logdatei gelesen – ohne Anmeldung oder Admin-Rechte. "
                      "Er ändert sich, sobald Teams ihn selbst ändert.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888")
        form.addRow(hint)
        lay.addWidget(box)

        claude = self.cfg["claude"]
        box = QGroupBox("Claude-Nutzung")
        form = QFormLayout(box)
        self.claude_now = QLabel("–")
        self.claude_now.setStyleSheet("font-weight:bold")
        self.claude_now.setWordWrap(True)
        form.addRow("Aktuell", self.claude_now)
        en = QCheckBox("Nutzung abrufen")
        en.setChecked(claude.get("enabled", True))
        en.toggled.connect(lambda v: self._set_sub("claude", "enabled", v))
        form.addRow("", en)
        interval = QSpinBox()
        interval.setRange(30, 3600)
        interval.setSuffix(" s")
        interval.setValue(int(claude.get("interval", 120)))
        interval.valueChanged.connect(lambda v: self._set_sub("claude", "interval", v))
        refresh = QPushButton("Jetzt aktualisieren")
        refresh.clicked.connect(self.engine.refresh_claude)
        row = QHBoxLayout()
        row.addWidget(interval)
        row.addWidget(refresh)
        row.addStretch(1)
        form.addRow("Abfrage alle", row)
        hint = QLabel("Nutzt die Anmeldung von Claude Code auf diesem PC. „Sitzung“ ist das 5-Stunden-Limit, "
                      "„Woche“ das Wochenlimit deines Abos – wie unter claude.ai › Einstellungen › Nutzung.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888")
        form.addRow(hint)
        lay.addWidget(box)
        lay.addStretch(1)
        return w

    def _toggle_light_state(self, key: str, on: bool) -> None:
        states = set(self.cfg["teams"].get("light_states", []))
        states.add(key) if on else states.discard(key)
        self._set_sub("teams", "light_states", sorted(states))

    # --- tab: settings ---------------------------------------------------------------------------

    def _build_settings_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        night = self.cfg["night"]
        box = QGroupBox("Nachtmodus")
        form = QFormLayout(box)
        en = QCheckBox("Nachts dunkler")
        en.setChecked(night.get("enabled", False))
        en.toggled.connect(lambda v: self._set_sub("night", "enabled", v))
        form.addRow("", en)
        for key, label in (("start", "Von"), ("end", "Bis")):
            te = QTimeEdit(QTime.fromString(night.get(key, "22:00"), "HH:mm"))
            te.setDisplayFormat("HH:mm")
            te.timeChanged.connect(lambda t, k=key: self._set_sub("night", k, t.toString("HH:mm")))
            form.addRow(label, te)
        nb = QSpinBox()
        nb.setRange(0, 100)
        nb.setSuffix(" %")
        nb.setValue(int(night.get("brightness", 5)))
        nb.valueChanged.connect(lambda v: self._set_sub("night", "brightness", v))
        form.addRow("Helligkeit nachts", nb)
        lay.addWidget(box)

        box = QGroupBox("Wetter")
        form = QFormLayout(box)
        row = QHBoxLayout()
        self.city = QLineEdit(self.cfg["weather"].get("city", ""))
        self.city.setPlaceholderText("z. B. Köln")
        self.city.returnPressed.connect(self._find_city)
        find = QPushButton("Ort übernehmen")
        find.clicked.connect(self._find_city)
        row.addWidget(self.city, 1)
        row.addWidget(find)
        form.addRow("Ort", row)
        self.weather_now = QLabel("–")
        form.addRow("Aktuell", self.weather_now)
        lay.addWidget(box)

        box = QGroupBox("Programm")
        form = QFormLayout(box)
        auto = QCheckBox("Mit Windows starten")
        auto.setChecked(autostart.is_enabled())
        auto.toggled.connect(self._set_autostart)
        form.addRow("", auto)
        mini = QCheckBox("Minimiert starten (nur Symbol in der Taskleiste)")
        mini.setChecked(self.cfg.get("start_minimized", False))
        mini.toggled.connect(lambda v: self._set_cfg("start_minimized", v))
        form.addRow("", mini)
        path = QLabel(f"Einstellungen: {config.CONFIG_FILE}")
        path.setStyleSheet("color:#888")
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
            QMessageBox.warning(self, "Wetter", f"Ortssuche nicht erreichbar:\n{err}")
            return
        if not hit:
            QMessageBox.information(self, "Wetter", f"„{name}“ nicht gefunden.")
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
        self.conn_label.setText("Suche Panels …")
        fut = self.engine.scan()

        def done() -> None:
            if not fut.done():
                QTimer.singleShot(200, done)
                return
            try:
                panels = fut.result()
            except Exception as err:  # noqa: BLE001
                self.conn_label.setText(f"Suche fehlgeschlagen: {err}")
                return
            self.devices.clear()
            for address, name, rssi in panels:
                self.devices.addItem(f"{name} ({address}, {rssi} dBm)", (address, name))
            self.conn_label.setText(f"{len(panels)} Panel(s) gefunden" if panels else
                                    "Kein Panel gefunden – ist es an und nicht mit der App verbunden?")
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
        self.conn_label.setText(st.connection)
        self.conn_label.setStyleSheet(f"font-weight:bold; color:{'#2a2' if st.connected else '#c80'}")
        self.showing.setText(st.showing)
        self.error_label.setVisible(bool(st.error))
        self.error_label.setText(st.error or "")
        label, color = sources.teams_label(st.teams)
        self.teams_now.setText(label if st.teams else "noch kein Status im Teams-Log gefunden")
        self.teams_now.setStyleSheet(f"font-weight:bold; color:rgb{color}")
        c = st.claude
        if c is None:
            self.claude_now.setText("–")
        elif c.error:
            self.claude_now.setText(c.error)
        else:
            def fmt(pct, reset):
                days = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
                when = f", wird zurückgesetzt {days[reset.weekday()]} {reset:%H:%M}" if reset else ""
                return f"{pct:.0f} %{when}" if pct is not None else "?"
            self.claude_now.setText(f"Sitzung: {fmt(c.session, c.session_reset)}\n"
                                    f"Woche: {fmt(c.week, c.week_reset)}" + (f"\nAbo: {c.plan}" if c.plan else ""))
        wx = st.weather
        if wx:
            self.weather_now.setText(f"{wx.temperature:.0f} °C ({wx.icon}), heute {wx.low:.0f}–{wx.high:.0f} °C")
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
            self.preview.setText(f"<span style='color:#aaa'>{label} – läuft im Panel (keine Vorschau)</span>")
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
        menu.addAction("Öffnen", self.show_window)
        menu.addAction("Nächste Seite", self.engine.next_page)
        menu.addAction("Zurück zur Rotation", self.engine.clear_message)
        menu.addSeparator()
        self.tray_power = QAction("Panel an", menu, checkable=True)
        self.tray_power.setChecked(self.cfg.get("power", True))
        self.tray_power.toggled.connect(self.power.setChecked)
        self.power.toggled.connect(self.tray_power.setChecked)
        menu.addAction(self.tray_power)
        tray_light = QAction("Teams-Statuslicht", menu, checkable=True)
        tray_light.setChecked(self.cfg["teams"].get("status_light", True))
        tray_light.toggled.connect(self.light_cb.setChecked)
        self.light_cb.toggled.connect(tray_light.setChecked)
        menu.addAction(tray_light)
        menu.addSeparator()
        menu.addAction("Beenden", self.quit)
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
            self.tray.showMessage("Pixel Panel läuft weiter",
                                  "Das Panel wird weiter gesteuert. Beenden über das Symbol in der Taskleiste.",
                                  QSystemTrayIcon.MessageIcon.Information, 4000)
            self._tray_hint_shown = True

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
    app.setWindowIcon(app_icon())

    from PySide6.QtCore import QLockFile
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(config.APP_DIR / "pixelpanel.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "Pixel Panel", "Pixel Panel läuft bereits (Symbol in der Taskleiste).")
        return 0

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
    lock.unlock()
    return code
