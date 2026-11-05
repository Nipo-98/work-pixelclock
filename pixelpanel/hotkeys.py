"""Global keyboard shortcuts (Windows RegisterHotKey), delivered through Qt's native event filter."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging

from PySide6.QtCore import QAbstractNativeEventFilter

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000

# Ctrl+Alt+<key>: (key, what it does in the settings)
SHORTCUTS = {
    "next": ("N", "next page"),
    "focus": ("F", "start or stop the focus timer"),
    "stopwatch": ("W", "start or stop the stopwatch"),
    "back": ("B", "back to rotation"),
}


class Hotkeys(QAbstractNativeEventFilter):
    """actions: name -> function. register() returns the shortcuts another program already uses."""

    def __init__(self, actions: dict) -> None:
        super().__init__()
        self._actions = {i: fn for i, fn in enumerate(actions.values(), start=1)}
        self._names = {i: name for i, name in enumerate(actions, start=1)}
        self._registered: list[int] = []

    def register(self) -> list[str]:
        busy = []
        for hotkey_id, name in self._names.items():
            key = ord(SHORTCUTS[name][0])
            if ctypes.windll.user32.RegisterHotKey(None, hotkey_id, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, key):
                self._registered.append(hotkey_id)
            else:
                busy.append(f"Ctrl+Alt+{SHORTCUTS[name][0]}")
        return busy

    def unregister(self) -> None:
        for hotkey_id in self._registered:
            ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)
        self._registered.clear()

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt name
        if bytes(event_type) == b"windows_generic_MSG":
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam in self._actions:
                self._actions[msg.wParam]()
                return True, 0
        return False, 0
