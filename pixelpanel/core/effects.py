"""Animated background effects behind text and icons.

Every effect is a seamless loop of FRAMES frames (periodic in t) and kept dim, so text stays
readable. Colours are quantised to a few levels so a GIF needs only a small palette.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable

from PIL import Image

FRAMES = 32
FRAME_MS = 80

RGB = tuple[int, int, int]

# "rainbow" colours the text instead of drawing a background
EFFECTS = ("none", "rainbow", "matrix", "twinkle", "fire", "plasma", "snow", "rain", "pulse")
TEXT_EFFECTS = ("rainbow",)


def _q(value: float, step: int = 16) -> int:
    """Quantise a channel (small GIF palette)."""
    return max(0, min(255, int(value) // step * step))


def hue(h: float, level: float = 1.0) -> RGB:
    """Fully saturated colour for hue h (0..1) at brightness level (0..1)."""
    h = h % 1.0
    i = int(h * 6)
    f = h * 6 - i
    v = 255 * level
    p, q, t = 0, v * (1 - f), v * f
    r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i % 6]
    return (_q(r), _q(g), _q(b))


def rainbow_color(t: int) -> Callable[[int, int], RGB]:
    """Text colour function for the rainbow effect: hue moves along x and over time."""
    return lambda x, y: hue(x / 48 + t / FRAMES, 1.0)


def _rng(seed: int) -> random.Random:
    return random.Random(seed)


def _matrix(img, t, w, h):
    px = img.load()
    rng = _rng(1)
    trail = 6
    for x in range(w):
        if x % 2:
            continue
        speed = rng.choice((1, 2))  # loops per period
        offset = rng.randrange(h + trail)
        head = (offset + t * (h + trail) * speed // FRAMES) % (h + trail)
        for k in range(trail):
            y = head - k
            if 0 <= y < h:
                px[x, y] = (0, _q(140 - k * 22, 8), 0) if k else (_q(60), _q(200), _q(60))


def _twinkle(img, t, w, h):
    px = img.load()
    rng = _rng(2)
    for _ in range(w * h // 10):
        x, y = rng.randrange(w), rng.randrange(h)
        phase = rng.random()
        level = math.sin(2 * math.pi * (t / FRAMES * rng.choice((1, 2)) + phase))
        if level > 0.2:
            v = _q(150 * level, 8)
            px[x, y] = (v, v, _q(v * 0.7, 8))


def _fire(img, t, w, h):
    px = img.load()
    for x in range(w):
        flame = (0.35 + 0.18 * math.sin(2 * math.pi * (x / 9 + t / FRAMES))
                 + 0.12 * math.sin(2 * math.pi * (x / 4.3 - 2 * t / FRAMES))
                 + 0.08 * math.sin(2 * math.pi * (x / 2.1 + 3 * t / FRAMES)))
        top = int(h * (1 - flame))
        for y in range(max(0, top), h):
            heat = (y - top) / max(1, h - top)  # 0 at the tip, 1 at the bottom
            px[x, y] = (_q(90 + 110 * heat), _q(10 + 90 * heat * heat), 0)


def _plasma(img, t, w, h):
    px = img.load()
    a = 2 * math.pi * t / FRAMES
    for x in range(w):
        for y in range(h):
            v = (math.sin(x / 5 + a) + math.sin((y + x) / 6 - a) + math.sin(math.hypot(x - w / 2, y - h / 2) / 4 + a)) / 3
            px[x, y] = hue(0.5 + v / 4 + t / FRAMES, 0.28)


def _fall(img, t, w, h, density: int, speed: int, color: RGB, length: int, seed: int):
    px = img.load()
    rng = _rng(seed)
    for _ in range(w * density // 16):
        x = rng.randrange(w)
        offset = rng.randrange(h + length)
        y0 = (offset + t * (h + length) * speed // FRAMES) % (h + length)
        for k in range(length):
            y = y0 - k
            if 0 <= y < h:
                f = 1 - k / length
                px[x, y] = tuple(_q(c * f, 8) for c in color)


def _snow(img, t, w, h):
    _fall(img, t, w, h, density=6, speed=1, color=(170, 170, 190), length=1, seed=3)


def _rain(img, t, w, h):
    _fall(img, t, w, h, density=8, speed=3, color=(40, 90, 200), length=3, seed=4)


def _pulse(img, t, w, h, color: RGB = (0, 120, 255)):
    level = 0.08 + 0.17 * (1 + math.sin(2 * math.pi * t / FRAMES)) / 2
    c = tuple(_q(ch * level, 4) for ch in color)
    img.paste(c, (0, 0, w, h))


_BACKGROUNDS = {
    "matrix": _matrix,
    "twinkle": _twinkle,
    "fire": _fire,
    "plasma": _plasma,
    "snow": _snow,
    "rain": _rain,
    "pulse": _pulse,
}


def has_background(effect: str | None) -> bool:
    return effect in _BACKGROUNDS


def is_animated(effect: str | None) -> bool:
    return bool(effect) and effect != "none" and effect in EFFECTS


def background(effect: str, w: int, h: int, t: int, base: RGB = (0, 0, 0)) -> Image.Image:
    img = Image.new("RGB", (w, h), base)
    if (draw := _BACKGROUNDS.get(effect)) is not None:
        draw(img, t % FRAMES, w, h)
    return img
