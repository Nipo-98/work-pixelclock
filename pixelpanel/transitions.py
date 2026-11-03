"""Page transitions: a short GIF from the old picture to the new one.

The last frame shows the new page and is held for HOLD_MS, so the panel does not loop back to the
start of the transition while the real page is being uploaded.
"""

from __future__ import annotations

import random

from PIL import Image, ImageChops, ImageDraw

from .core.render import Rendered

HOLD_MS = 5000

TRANSITIONS: dict[str, str] = {
    "none": "Sofort (springen)",
    "push_left": "Schieben nach links",
    "push_up": "Schieben nach oben",
    "shoot": "Einschießen",
    "fade": "Überblenden",
    "wipe": "Wischen",
    "dissolve": "Auflösen",
    "nyan": "Nyan Cat",
    "random": "Zufällig",
}

RGB = tuple[int, int, int]


def _ease(t: float) -> float:
    """Ease in-out (smoothstep)."""
    return t * t * (3 - 2 * t)


def _ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def _done(frames: list[Image.Image], durations: list[int], new: Image.Image) -> Rendered:
    return Rendered(frames + [new.copy()], durations + [HOLD_MS])


def _push(old: Image.Image, new: Image.Image, vertical: bool, steps: int = 10, ms: int = 45) -> Rendered:
    w, h = old.size
    frames = []
    for i in range(1, steps):
        off = round(_ease(i / steps) * (h if vertical else w))
        f = Image.new("RGB", old.size)
        if vertical:
            f.paste(old, (0, -off))
            f.paste(new, (0, h - off))
        else:
            f.paste(old, (-off, 0))
            f.paste(new, (w - off, 0))
        frames.append(f)
    return _done(frames, [ms] * len(frames), new)


def _shoot(old: Image.Image, new: Image.Image) -> Rendered:
    """New page flies in fast from the right over the old one, with a tiny overshoot."""
    w, _ = old.size
    xs = [round(w * (1 - _ease_out(i / 6))) for i in range(6)] + [-2, 1]
    frames = []
    for x in xs:
        f = old.copy()
        f.paste(new, (x, 0))
        frames.append(f)
    return _done(frames, [30] * len(frames), new)


def _fade(old: Image.Image, new: Image.Image, steps: int = 5, ms: int = 60) -> Rendered:
    black = Image.new("RGB", old.size)
    frames = [Image.blend(old, black, i / steps) for i in range(1, steps + 1)]
    frames += [Image.blend(black, new, i / steps) for i in range(1, steps)]
    return _done(frames, [ms] * len(frames), new)


def _wipe(old: Image.Image, new: Image.Image, steps: int = 12, ms: int = 40) -> Rendered:
    w, h = old.size
    frames = []
    for i in range(1, steps):
        x = round(w * i / steps)
        f = old.copy()
        f.paste(new.crop((0, 0, x, h)), (0, 0))
        ImageDraw.Draw(f).line((min(x, w - 1), 0, min(x, w - 1), h - 1), fill=(255, 255, 255))
        frames.append(f)
    return _done(frames, [ms] * len(frames), new)


def _dissolve(old: Image.Image, new: Image.Image, steps: int = 8, ms: int = 60) -> Rendered:
    w, h = old.size
    pixels = [(x, y) for x in range(w) for y in range(h)]
    random.Random(7).shuffle(pixels)
    per = -(-len(pixels) // steps)
    f = old.copy()
    src = new.load()
    frames = []
    for i in range(steps - 1):
        px = f.load()
        for x, y in pixels[i * per:(i + 1) * per]:
            px[x, y] = src[x, y]
        frames.append(f.copy())
    return _done(frames, [ms] * len(frames), new)


# --- Nyan Cat --------------------------------------------------------------------------------------

RAINBOW: list[RGB] = [(255, 0, 0), (255, 150, 0), (255, 255, 0), (50, 255, 0), (0, 150, 255), (110, 70, 255)]
_CAT_COLORS: dict[str, RGB] = {
    "b": (255, 204, 153),  # crust
    "p": (255, 153, 220),  # icing
    "d": (255, 40, 150),  # sprinkles
    "g": (150, 150, 150),  # cat
    "k": (1, 1, 1),  # eyes (not transparent)
    "c": (255, 120, 140),  # cheeks
}
# 16 x 10, two leg/tail positions
_CAT = [
    [
        "..bbbbbbbb......",
        ".bppppdpppb.....",
        ".bpdppppgpg..g..",
        "gbppppppgggggg..",
        "gbpppdppgkggkg..",
        ".bppppppgcgggc..",
        ".bpdpppppggggg..",
        "..bbbbbbbb......",
        "..g.g....g.g....",
        "................",
    ],
    [
        "..bbbbbbbb......",
        ".bppppdpppb.....",
        ".bpdppppgpg..g..",
        ".bppppppgggggg..",
        "gbpppdppgkggkg..",
        "gbppppppgcgggc..",
        ".bpdpppppggggg..",
        "..bbbbbbbb......",
        "...g.g....g.g...",
        "................",
    ],
]
CAT_W, CAT_H = 16, 10


def _cat(frame: int) -> Image.Image:
    img = Image.new("RGBA", (CAT_W, CAT_H), (0, 0, 0, 0))
    for y, row in enumerate(_CAT[frame % 2]):
        for x, c in enumerate(row):
            if c in _CAT_COLORS:
                img.putpixel((x, y), _CAT_COLORS[c] + (255,))
    return img


def _rainbow(w: int, h: int, upto: int, phase: int, top: int) -> Image.Image:
    """RGBA rainbow trail from x=0 to `upto`, bands wave up/down in 4 px steps."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    band = max(1, (h - 4) // len(RAINBOW))
    px = img.load()
    for x in range(max(0, min(upto, w))):
        wave = 1 if ((x // 4) + phase) % 2 else 0
        for i, color in enumerate(RAINBOW):
            for dy in range(band):
                y = top + wave + i * band + dy
                if 0 <= y < h:
                    px[x, y] = color + (255,)
    return img


def _nyan(old: Image.Image, new: Image.Image) -> Rendered:
    w, h = old.size
    top = max(0, (h - CAT_H) // 2)
    frames, durations = [], []
    step = 2
    for k, x in enumerate(range(-CAT_W, w + 1, step)):
        nose = x + 12  # where the cat's face is: ahead of it the old page is still visible
        f = new.copy()
        if nose < w:
            f.paste(old.crop((max(0, nose), 0, w, h)), (max(0, nose), 0))
        trail = _rainbow(w, h, x + 3, k // 2, top - 1)
        f.paste(trail, (0, 0), trail)
        cat = _cat(k // 2)
        f.paste(cat, (x, top + (k // 2) % 2), cat)
        frames.append(f)
        durations.append(45)
    # the cat is gone: the rainbow fades out over the new page
    trail = _rainbow(w, h, w, 0, top - 1)
    for level in (0.75, 0.5, 0.3, 0.15):
        f = new.copy().convert("RGBA")
        faded = trail.copy()
        faded.putalpha(ImageChops.multiply(trail.getchannel("A"), Image.new("L", (w, h), int(255 * level))))
        f.alpha_composite(_dim(faded, level))
        frames.append(f.convert("RGB"))
        durations.append(80)
    return _done(frames, durations, new)


def _dim(img: Image.Image, level: float) -> Image.Image:
    """Darker colours instead of real transparency (LEDs mix badly); keeps the palette small."""
    r, g, b, a = img.split()
    return Image.merge("RGBA", [c.point(lambda v: int(v * level)) for c in (r, g, b)] + [a])


# --------------------------------------------------------------------------------------------------

_BUILDERS = {
    "push_left": lambda o, n: _push(o, n, vertical=False),
    "push_up": lambda o, n: _push(o, n, vertical=True),
    "shoot": _shoot,
    "fade": _fade,
    "wipe": _wipe,
    "dissolve": _dissolve,
    "nyan": _nyan,
}


def build(kind: str, old: Image.Image, new: Image.Image) -> Rendered | None:
    """Transition from `old` to `new` (same size), or None for an immediate switch."""
    if kind == "random":
        kind = random.choice(list(_BUILDERS))
    builder = _BUILDERS.get(kind)
    if builder is None or old.size != new.size:
        return None
    return builder(old.convert("RGB"), new.convert("RGB"))


def play_ms(rendered: Rendered) -> int:
    """How long the transition runs before it reaches the held last frame."""
    return sum(rendered.durations[:-1])
