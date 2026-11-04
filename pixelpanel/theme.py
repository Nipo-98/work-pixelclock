"""Look of the window: slate surfaces, LED amber as the one accent, Windows 11 system type."""

from __future__ import annotations

BASE = "#1b1f2a"  # window
SIDEBAR = "#161a23"
SURFACE = "#232837"  # inputs, list
RAISED = "#2c3243"  # hover
LINE = "#343b4f"
TEXT = "#e7eaf3"
MUTED = "#8b93a7"
ACCENT = "#ffbe00"  # the panel's default text colour
ACCENT_TEXT = "#1b1f2a"
OK = "#3ccf6e"
WARN = "#ffb547"
ERROR = "#ff6b6b"

CHECK_SVG = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7" '
             f'fill="none" stroke="{ACCENT_TEXT}" stroke-width="2.2" stroke-linecap="round" '
             f'stroke-linejoin="round"/></svg>')


def stylesheet(asset_dir) -> str:
    """The stylesheet, with the check mark written next to the settings (Qt needs a file for it)."""
    from pathlib import Path
    path = Path(asset_dir) / "check.svg"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CHECK_SVG, encoding="utf-8")
        check = f"image: url({path.as_posix()});"
    except OSError:
        check = ""
    return STYLE.replace("/*CHECK*/", check)


FONT = '"Segoe UI Variable Text", "Segoe UI", sans-serif'
DISPLAY = '"Segoe UI Variable Display", "Segoe UI", sans-serif'

STYLE = f"""
* {{ font-family: {FONT}; font-size: 10pt; color: {TEXT}; }}
QMainWindow, #root, #main {{ background: {BASE}; }}
QScrollArea, QScrollArea > QWidget > QWidget, QStackedWidget > QWidget {{ background: transparent; }}

#sidebar {{ background: {SIDEBAR}; border-right: 1px solid {LINE}; }}
#brand {{ font-family: {DISPLAY}; font-size: 15pt; font-weight: 600; padding-left: 6px; }}
#nav {{ background: transparent; border: none; outline: none; }}
#nav::item {{ padding: 9px 10px; margin: 1px 0; border-radius: 8px; color: {MUTED}; }}
#nav::item:hover {{ background: {SURFACE}; color: {TEXT}; }}
#nav::item:selected {{ background: {SURFACE}; color: {TEXT}; border-left: 3px solid {ACCENT}; }}
#conn {{ color: {MUTED}; font-size: 9pt; padding: 6px; }}
#conn[ok="true"] {{ color: {OK}; }}

#preview {{ background: #14171f; border: 1px solid {LINE}; border-radius: 12px; }}
#showing {{ font-family: {DISPLAY}; font-size: 11pt; font-weight: 600; }}
#muted {{ color: {MUTED}; }}
#hint {{ color: {MUTED}; font-size: 9pt; }}
#error {{ color: {ERROR}; }}

QGroupBox {{ border: none; border-top: 1px solid {LINE}; margin-top: 28px; padding-top: 14px;
            font-family: {DISPLAY}; font-size: 12pt; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 0; top: 4px; padding: 0; }}
QGroupBox > QWidget, QGroupBox QLabel, QGroupBox QCheckBox {{ font-family: {FONT}; font-size: 10pt;
            font-weight: normal; }}

QPushButton, QToolButton {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 7px;
            padding: 6px 14px; }}
QPushButton:hover, QToolButton:hover {{ background: {RAISED}; }}
QPushButton:pressed {{ background: {LINE}; }}
QPushButton#primary {{ background: {ACCENT}; color: {ACCENT_TEXT}; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: #ffcb33; }}
QPushButton#power {{ min-width: 64px; }}
QPushButton#power:checked {{ background: {ACCENT}; color: {ACCENT_TEXT}; border: none; font-weight: 600; }}
QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTimeEdit:focus {{
            border: 1px solid {ACCENT}; }}

QLineEdit, QComboBox, QSpinBox, QTimeEdit {{ background: {SURFACE}; border: 1px solid {LINE};
            border-radius: 7px; padding: 5px 8px; selection-background-color: {ACCENT};
            selection-color: {ACCENT_TEXT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {LINE}; selection-background-color: {RAISED};
            selection-color: {TEXT}; outline: none; }}
QSpinBox::up-button, QSpinBox::down-button, QTimeEdit::up-button, QTimeEdit::down-button {{ width: 16px;
            border: none; background: transparent; }}

QListWidget {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 8px; padding: 4px; outline: none; }}
QListWidget::item {{ padding: 7px 6px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {RAISED}; color: {TEXT}; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {MUTED};
            background: {SURFACE}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; /*CHECK*/ }}
QListWidget::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {MUTED}; }}
QListWidget::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; /*CHECK*/ }}

QSlider::groove:horizontal {{ height: 4px; background: {LINE}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QToolTip {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {LINE}; padding: 4px; }}
QMenu {{ background: {SURFACE}; border: 1px solid {LINE}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 5px; }}
QMenu::item:selected {{ background: {RAISED}; }}
"""
