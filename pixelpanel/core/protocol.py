"""BLE protocol of iPixel Color panels (vendor app "iPixels", com.wifiled.ipixels).

GATT service 0x00FA: commands are written to FA02 (write with response), the panel answers on
FA03 (notify). Every command is framed as <len u16 LE><cmd u8><type u8><payload>, len counting
the whole frame. Byte layouts follow the app (UUIDs and name prefix checked in iPixels 3.8.1) and
the MIT-licensed reverse engineering in pypixelcolor (https://github.com/lucagoc/pypixelcolor).
"""

from __future__ import annotations

import binascii
from dataclasses import dataclass
from datetime import datetime

SERVICE_UUID = "000000fa-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000fa02-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000fa03-0000-1000-8000-00805f9b34fb"

NAME_PREFIX = "LED_BLE_"
MANUFACTURER_ID = 0x5254  # "TR"

# Images are sent in windows of 12 KiB; the panel acknowledges each window.
WINDOW_SIZE = 12 * 1024
MAX_CHUNK = 509  # bytes per GATT write, limited further by the negotiated MTU (panel: 517)

# ACK notification: 05 00 <cmd> <type> <status>; status 0/1 = window received, 3 = transfer complete
ACK_WINDOW = (0, 1)
ACK_DONE = 3

# Device type byte (info response byte 4, last byte of the manufacturer data) -> (width, height)
PANEL_SIZES: dict[int, tuple[int, int]] = {
    128: (64, 64),
    129: (32, 32),
    130: (32, 16),
    131: (64, 16),
    132: (96, 16),
    133: (64, 20),
    134: (128, 32),
    135: (144, 16),
    136: (192, 16),
    137: (48, 24),
    138: (64, 32),
    139: (96, 32),
    140: (128, 32),
    141: (96, 32),
    142: (160, 32),
    143: (192, 32),
    144: (256, 32),
    145: (320, 32),
    146: (384, 32),
    147: (448, 32),
}
DEFAULT_SIZE = (64, 16)

CLOCK_STYLES = 9  # 0-8, built-in clock faces of the panel


def _frame(cmd: int, typ: int, payload: bytes = b"") -> bytes:
    return (4 + len(payload)).to_bytes(2, "little") + bytes([cmd, typ]) + payload


def set_power(on: bool) -> bytes:
    return _frame(0x07, 0x01, bytes([1 if on else 0]))


def set_brightness(level: int) -> bytes:
    return _frame(0x04, 0x80, bytes([max(0, min(100, int(level)))]))


def set_time(now: datetime) -> bytes:
    """Set the clock; the panel answers with its device info (same command)."""
    return _frame(0x01, 0x80, bytes([now.hour, now.minute, now.second, 0]))


def set_clock_mode(style: int, now: datetime, show_date: bool = True, format_24: bool = True) -> bytes:
    """Switch to the built-in clock face `style` (0-8)."""
    return _frame(0x06, 0x01, bytes([
        int(style) % CLOCK_STYLES,
        1 if format_24 else 0,
        1 if show_date else 0,
        now.year % 100,
        now.month,
        now.day,
        now.isoweekday(),
    ]))


def set_orientation(orientation: int) -> bytes:
    """0 = normal, 1-3 = rotated/flipped."""
    return _frame(0x06, 0x80, bytes([int(orientation) & 0x03]))


def show_slot(slot: int) -> bytes:
    return _frame(0x08, 0x80, bytes([1, 0, int(slot) & 0xFF]))


def image_windows(data: bytes, is_gif: bool, slot: int = 0) -> list[bytes]:
    """Frames for a PNG or GIF upload; slot 0 shows it without storing it."""
    size = len(data).to_bytes(4, "little")
    crc = (binascii.crc32(data) & 0xFFFFFFFF).to_bytes(4, "little")
    kind = 0x03 if is_gif else 0x02
    tail = bytes([0x02 if is_gif else 0x00, slot & 0xFF])
    windows = []
    for index, pos in enumerate(range(0, len(data), WINDOW_SIZE)):
        option = 0x00 if index == 0 else 0x02
        body = bytes([kind, 0x00, option]) + size + crc + tail + data[pos:pos + WINDOW_SIZE]
        windows.append((len(body) + 2).to_bytes(2, "little") + body)
    return windows


def chunks(window: bytes, size: int):
    for pos in range(0, len(window), size):
        yield window[pos:pos + size]


def ack_status(data: bytes) -> int | None:
    """Status byte of an ACK notification, None for other notifications."""
    if len(data) >= 5 and data[0] == 0x05 and data[1] == 0x00:
        return data[4]
    return None


@dataclass
class DeviceInfo:
    device_type: int
    width: int
    height: int
    password_flag: int
    raw: bytes


def parse_device_info(data: bytes) -> DeviceInfo:
    if len(data) < 5:
        raise ValueError(f"device info too short: {data.hex()}")
    device_type = data[4]
    width, height = PANEL_SIZES.get(device_type, DEFAULT_SIZE)
    return DeviceInfo(device_type, width, height, data[10] if len(data) > 10 else 255, bytes(data))


def size_from_manufacturer_data(data: bytes | None) -> tuple[int, int] | None:
    """Panel size from the advertisement: its last byte is the device type."""
    if not data:
        return None
    return PANEL_SIZES.get(data[-1])
