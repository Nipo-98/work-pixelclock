"""Render screens for the panel with Pillow: text with icon, scrolling text, clock.

Pure Pillow, no Home Assistant imports, so it can be tested and previewed on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from io import BytesIO

from PIL import Image, ImageDraw

from . import effects, font

RGB = tuple[int, int, int]

WHITE: RGB = (255, 255, 255)
BLACK: RGB = (0, 0, 0)
DIM: RGB = (50, 50, 50)

SCROLL_FRAME_MS = 60
SCROLL_STEP = 1
MAX_FRAMES = 400
BLINK_MS = 500
GIF_PARTIAL_FRAMES = True


@dataclass
class Line:
    text: str
    color: RGB = WHITE


@dataclass
class Screen:
    """What to show: up to height // 8 lines of text, optional icon and progress bar."""

    lines: list[Line]
    icon: Image.Image | None = None
    progress: float | None = None  # 0-100
    progress_color: RGB = (0, 200, 60)
    background: RGB = BLACK
    big: bool = True  # scale up a single line when it fits
    effect: str = "none"  # see effects.EFFECTS


@dataclass
class Rendered:
    frames: list[Image.Image]
    durations: list[int] = field(default_factory=list)  # ms per frame, empty for a still image
    scrolls: bool = False  # text scrolls (effects and the clock's seconds only loop)
    # the picture stays correct until then (clock with seconds: end of the minute); sending it again
    # earlier would restart the GIF and make the seconds jump
    valid_until: datetime | None = None

    @property
    def animated(self) -> bool:
        return len(self.frames) > 1

    @property
    def cycle_ms(self) -> int:
        return sum(self.durations) if self.animated else 0

    @property
    def scroll_ms(self) -> int:
        """Time one pass of scrolling text needs; the page is shown at least that long."""
        return self.cycle_ms if self.scrolls else 0


def _draw_text(img: Image.Image, text: str, x: int, y: int, color, scale: int = 1,
               clip: tuple[int, int] | None = None, clip_y: tuple[int, int] | None = None) -> None:
    """Draw text with its top-left at (x, y); color is an RGB or a function (x, y) -> RGB;
    clip / clip_y = (min, max) exclusive."""
    x_min, x_max = clip if clip else (0, img.width)
    y_min, y_max = clip_y if clip_y else (0, img.height)
    px = img.load()
    cx = x
    for ch in text:
        g = font.glyph(ch)
        for gx, gy in g.pixels():
            for dx in range(scale):
                for dy in range(scale):
                    xx, yy = cx + gx * scale + dx, y + gy * scale + dy
                    if x_min <= xx < x_max and y_min <= yy < y_max:
                        px[xx, yy] = color(xx, yy) if callable(color) else color
        cx += (g.width + 1) * scale


def _paste_icon(img: Image.Image, icon: Image.Image, size: int, top: int | None = None) -> None:
    """Icon scaled to size x size at the left edge, vertically centred unless top is given."""
    if icon.size != (size, size):
        if icon.width <= size and icon.height <= size and size % icon.width == 0 and size % icon.height == 0:
            icon = icon.resize((size, size), Image.Resampling.NEAREST)
        else:
            icon = icon.copy()
            icon.thumbnail((size, size), Image.Resampling.NEAREST)
    y = (img.height - icon.height) // 2 if top is None else top + (size - icon.height) // 2
    img.paste(icon, ((size - icon.width) // 2, y), icon if icon.mode == "RGBA" else None)


def _wrap(text: str, max_w: int, scale: int = 1) -> list[str]:
    """Greedy word wrap; words wider than max_w are split between characters."""
    rows: list[str] = []
    for word in text.split():
        while font.text_width(word, scale) > max_w and len(word) > 1:
            cut = 1
            while cut < len(word) and font.text_width(word[:cut + 1], scale) <= max_w:
                cut += 1
            rows.append(word[:cut])
            word = word[cut:]
        if rows and font.text_width(f"{rows[-1]} {word}", scale) <= max_w:
            rows[-1] = f"{rows[-1]} {word}"
        else:
            rows.append(word)
    return rows or [""]


def _timeline(effect: str, scroll_cycle: int, scroll_ms: int) -> list[tuple[int, int, int]]:
    """Frames as (scroll offset, effect frame, duration ms); [] means a still image."""
    animated_effect = effects.is_animated(effect)
    if not scroll_cycle:
        return [(0, i, effects.FRAME_MS) for i in range(effects.FRAMES)] if animated_effect else []
    step = SCROLL_STEP * (2 if animated_effect else 1)  # effect frames are full redraws: fewer, longer frames
    ms = scroll_ms * (2 if animated_effect else 1)
    if -(-scroll_cycle // step) > MAX_FRAMES:
        step = -(-scroll_cycle // MAX_FRAMES)
    return [(t, i, ms) for i, t in enumerate(range(0, scroll_cycle, step))]


def _canvas(screen: Screen, width: int, height: int, t_effect: int) -> Image.Image:
    if effects.has_background(screen.effect):
        return effects.background(screen.effect, width, height, t_effect, screen.background)
    return Image.new("RGB", (width, height), screen.background)


def _text_color(screen: Screen, line: Line, t_effect: int):
    return effects.rainbow_color(t_effect) if screen.effect == "rainbow" else line.color


def _render_frames(screen: Screen, width: int, height: int, scroll_cycle: int, scroll_ms: int,
                   decorate, draw_text) -> Rendered:
    """Shared frame loop: background effect, icon/progress (decorate), text (draw_text(img, t, te))."""
    timeline = _timeline(screen.effect, scroll_cycle, scroll_ms)
    if not timeline:
        img = _canvas(screen, width, height, 0)
        decorate(img)
        draw_text(img, 0, 0)
        return Rendered([img])
    frames, durations = [], []
    for t, te, ms in timeline:
        img = _canvas(screen, width, height, te)
        decorate(img)
        draw_text(img, t, te)
        frames.append(img)
        durations.append(ms)
    return Rendered(frames, durations, scrolls=bool(scroll_cycle))


def _render_tall(screen: Screen, width: int, height: int) -> Rendered:
    """Portrait layout: icon on top, wrapped text below, scrolling upwards when it does not fit."""
    icon_size = width if screen.icon is not None else 0
    y0 = icon_size + 1 if icon_size else 0
    area_h = height - y0 - (2 if screen.progress is not None else 0)

    scale = 1
    if screen.big:
        for s in range(max(1, width // 5), 1, -1):
            words = [w for line in screen.lines for w in line.text.split()]
            rows = [(r, line) for line in screen.lines for r in _wrap(line.text, width, s)]
            if all(font.text_width(w, s) <= width for w in words) and \
                    len(rows) * font.LINE_HEIGHT * s - s <= area_h:
                scale = s
                break
    rows = [(r, line) for line in screen.lines for r in _wrap(line.text, width, scale)]
    line_h = font.LINE_HEIGHT * scale
    block_h = len(rows) * line_h - scale  # no gap after the last line

    def decorate(img: Image.Image) -> None:
        if screen.icon is not None:
            _paste_icon(img, screen.icon, icon_size, top=0)
        if screen.progress is not None:
            p = max(0.0, min(100.0, screen.progress))
            for x in range(width):
                img.putpixel((x, height - 1), screen.progress_color if x < round(width * p / 100) else DIM)

    scrolling = block_h > area_h

    def draw_text(img: Image.Image, t: int, te: int) -> None:
        top = y0 + area_h - t if scrolling else y0 + (area_h - block_h) // 2
        for i, (text, line) in enumerate(rows):
            x = (width - font.text_width(text, scale)) // 2
            _draw_text(img, text, x, top + i * line_h, _text_color(screen, line, te), scale, clip_y=(y0, y0 + area_h))

    # vertical scrolling reads better slower
    return _render_frames(screen, width, height, block_h + area_h if scrolling else 0, SCROLL_FRAME_MS * 2,
                          decorate, draw_text)


def _layout(screen: Screen, width: int, height: int) -> tuple[int, int, int, list[int]]:
    """Return text area x0, area width, scale and the top of each line."""
    icon_size = height if screen.icon is not None else 0
    x0 = icon_size + 1 if icon_size else 0
    area_w = width - x0
    lines = screen.lines[: max(1, height // font.LINE_HEIGHT)]
    if not lines:
        return x0, area_w, 1, []

    scale = 1
    if screen.big and len(lines) == 1:
        text = lines[0].text
        glyph_h = font.LINE_HEIGHT if font.has_descender(text) else font.CAP_HEIGHT
        avail_h = height - (1 if screen.progress is not None else 0)
        for s in range(max(1, avail_h // glyph_h), 1, -1):
            if font.text_width(text, s) <= area_w:
                scale = s
                break

    if len(lines) == 1:
        text = lines[0].text
        glyph_h = (font.LINE_HEIGHT if font.has_descender(text) else font.CAP_HEIGHT) * scale
        avail_h = height - (2 if screen.progress is not None and scale == 1 else 0)
        tops = [max(0, (avail_h - glyph_h) // 2)]
    else:
        block = len(lines) * font.LINE_HEIGHT
        top = max(0, (height - block) // 2)
        tops = [top + i * font.LINE_HEIGHT for i in range(len(lines))]
    return x0, area_w, scale, tops


def render_screen(screen: Screen, width: int, height: int) -> Rendered:
    if height > width:
        return _render_tall(screen, width, height)
    x0, area_w, scale, tops = _layout(screen, width, height)
    lines = screen.lines[: len(tops)]
    widths = [font.text_width(line.text, scale) for line in lines]
    overflow = [w > area_w for w in widths]

    def decorate(img: Image.Image) -> None:
        if screen.icon is not None:
            _paste_icon(img, screen.icon, height)
        if screen.progress is not None:
            p = max(0.0, min(100.0, screen.progress))
            for x in range(x0, width):
                img.putpixel((x, height - 1), screen.progress_color if x < x0 + round(area_w * p / 100) else DIM)

    def draw_text(img: Image.Image, t: int, te: int) -> None:
        for line, w, top, over in zip(lines, widths, tops, overflow, strict=True):
            x = x0 + area_w - (t % (w + area_w)) if over else x0 + (area_w - w) // 2
            _draw_text(img, line.text, x, top, _text_color(screen, line, te), scale, clip=(x0, width))

    cycle = max((w + area_w for w, over in zip(widths, overflow, strict=True) if over), default=0)
    return _render_frames(screen, width, height, cycle, SCROLL_FRAME_MS, decorate, draw_text)


SECONDS_COLOR: RGB = (150, 150, 160)


def _clock_timeline(now: datetime, effect: str, blink: bool, seconds: bool) -> list[tuple[int, bool, int, int]]:
    """(effect frame, colon visible, ms, second shown) per frame.

    With seconds the GIF counts the rest of the current minute in 500 ms steps (Home Assistant sends
    a new one when the minute changes). Without seconds: the effect loop or a 1 s colon blink.
    """
    if seconds:
        steps = (60 - now.second) * 2
        return [(k % effects.FRAMES, not blink or k % 2 == 0, BLINK_MS, now.second + k // 2) for k in range(steps)]
    if effects.is_animated(effect):
        return [(i, not blink or (i * effects.FRAME_MS) % 1000 < 500, effects.FRAME_MS, now.second)
                for i in range(effects.FRAMES)]
    if blink:
        return [(0, True, BLINK_MS, now.second), (0, False, BLINK_MS, now.second)]
    return [(0, True, 0, now.second)]


def _clock_frames(now: datetime, width: int, height: int, effect: str, blink: bool, seconds: bool, draw) -> Rendered:
    timeline = _clock_timeline(now, effect, blink, seconds)
    frames = []
    for te, colon, _, second in timeline:
        if effects.has_background(effect):
            img = effects.background(effect, width, height, te)
        else:
            img = Image.new("RGB", (width, height), BLACK)
        draw(img, colon, te, second)
        frames.append(img)
    if len(frames) == 1:
        return Rendered(frames)
    valid_until = now.replace(second=0, microsecond=0) + timedelta(minutes=1) if seconds else None
    return Rendered(frames, [ms for _, _, ms, _ in timeline], valid_until=valid_until)


def render_clock(now: datetime, width: int, height: int, color: RGB = WHITE,
                 weekday_color: RGB | None = None, blink: bool = True,
                 weekday_bar: bool = True, weekdays: tuple[str, ...] | None = None,
                 effect: str = "none", seconds: bool = False) -> Rendered:
    """Big HH:MM with a blinking colon, optional small running seconds and a Monday-first weekday bar."""
    if height > width:
        return _render_clock_tall(now, width, height, color, blink, weekday_color, weekdays or WEEKDAYS_SHORT,
                                  effect, seconds)
    bar_h = 2 if weekday_bar and height >= 16 else (1 if weekday_bar and height >= 12 else 0)
    avail_h = height - bar_h
    digits = now.strftime("%H:%M")
    sec_w = font.text_width("00") + 2 if seconds else 0  # small seconds right of the minutes
    scale = 1
    for s in range(max(1, avail_h // font.CAP_HEIGHT), 0, -1):
        if font.text_width(digits, s) + sec_w <= width:
            scale = s
            break
    text_w = font.text_width(digits, scale) + sec_w
    x = (width - text_w) // 2
    y = max(0, (avail_h - font.CAP_HEIGHT * scale) // 2)

    def draw(img: Image.Image, colon: bool, te: int, second: int) -> None:
        c = effects.rainbow_color(te) if effect == "rainbow" else color
        hh, mm = digits.split(":")
        _draw_text(img, hh, x, y, c, scale)
        colon_x = x + font.text_width(hh, scale) + scale
        if colon:
            _draw_text(img, ":", colon_x, y, c, scale)
        mm_x = colon_x + (font.glyph(":").width + 1) * scale
        _draw_text(img, mm, mm_x, y, c, scale)
        if seconds:
            # bottom-aligned with the big digits
            sx = mm_x + font.text_width(mm, scale) + 2
            _draw_text(img, f"{second % 60:02d}", sx, y + font.CAP_HEIGHT * (scale - 1), SECONDS_COLOR)
        if bar_h:
            seg = (width - 6) // 7
            left = (width - (seg * 7 + 6)) // 2
            for day in range(7):
                dc = (weekday_color or color) if day == now.weekday() else DIM
                for xx in range(left + day * (seg + 1), left + day * (seg + 1) + seg):
                    for yy in range(height - bar_h, height):
                        img.putpixel((xx, yy), dc)

    return _clock_frames(now, width, height, effect, blink, seconds, draw)


WEEKDAYS_SHORT = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def _render_clock_tall(now: datetime, width: int, height: int, color: RGB, blink: bool,
                       weekday_color: RGB | None, weekdays: tuple[str, ...] = WEEKDAYS_SHORT,
                       effect: str = "none", seconds: bool = False) -> Rendered:
    """Portrait clock: HH, blinking colon, MM, optional seconds; below weekday, day and month when there is room."""

    def centered(img: Image.Image, text: str, y: int, c) -> None:
        _draw_text(img, text, (width - font.text_width(text)) // 2, y, c)

    shift = 9 if seconds else 0

    def draw(img: Image.Image, colon: bool, te: int, second: int) -> None:
        c = effects.rainbow_color(te) if effect == "rainbow" else color
        centered(img, now.strftime("%H"), 1, c)
        if colon:
            cx = width // 2 - 1
            for y in (10, 12):
                for xx in (cx, cx + 1):
                    img.putpixel((xx, y), c(xx, y) if callable(c) else c)
        centered(img, now.strftime("%M"), 15, c)
        if seconds:
            centered(img, f"{second % 60:02d}", 24, SECONDS_COLOR)
        if height >= 56 + shift:
            centered(img, weekdays[now.weekday()], 28 + shift, weekday_color or (255, 190, 0))
            centered(img, now.strftime("%d."), 38 + shift, (0, 200, 230))
            centered(img, now.strftime("%m."), 47 + shift, (0, 200, 230))
        elif height >= 47 + shift:
            centered(img, weekdays[now.weekday()], 28 + shift, weekday_color or (255, 190, 0))
            centered(img, now.strftime("%d."), 38 + shift, (0, 200, 230))

    return _clock_frames(now, width, height, effect, blink, seconds, draw)


# orientation -> counter-clockwise rotation of the drawn picture; portrait_left = the end that is
# left in landscape points up (the top of the picture lands on the left end of the panel)
ORIENTATIONS: dict[str, int] = {"landscape": 0, "portrait_left": 90, "portrait_right": 270, "upside_down": 180}


def view_size(width: int, height: int, orientation: str) -> tuple[int, int]:
    """Size to draw on: portrait swaps the panel's width and height."""
    return (height, width) if ORIENTATIONS.get(orientation, 0) in (90, 270) else (width, height)


def to_panel(rendered: Rendered, orientation: str) -> Rendered:
    angle = ORIENTATIONS.get(orientation, 0)
    if not angle:
        return rendered
    return Rendered([f.rotate(angle, expand=True) for f in rendered.frames], rendered.durations)


def encode(rendered: Rendered) -> tuple[bytes, bool]:
    """Encode for the panel: PNG for a still image, GIF for an animation. Returns (data, is_gif)."""
    out = BytesIO()
    if not rendered.animated:
        rendered.frames[0].save(out, format="PNG")
        return out.getvalue(), False
    colors: set[RGB] = set()
    for f in rendered.frames:
        colors.update(c for _, c in (f.getcolors(256 * 256) or []))
    if len(colors) <= 256:
        # exact shared palette (no anti-aliasing, so there are only a few colours)
        palette_img = Image.new("P", (1, 1))
        # only the colours in use: a padded 256-entry palette is written again with every frame
        palette_img.putpalette([v for c in sorted(colors) for v in c])
        frames = [f.quantize(palette=palette_img, dither=Image.Dither.NONE) for f in rendered.frames]
    else:
        frames = [f.convert("P", palette=Image.Palette.ADAPTIVE, colors=256) for f in rendered.frames]
    frames[0].save(
        out,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=rendered.durations,
        loop=0,
        # 1 = keep the previous frame: Pillow then stores only the changed rectangle per frame
        disposal=1 if GIF_PARTIAL_FRAMES else 2,
        optimize=False,
    )
    return out.getvalue(), True


def preview(rendered: Rendered, scale: int = 8, animated_scale: int = 4) -> tuple[bytes, str]:
    """Upscaled image for the Home Assistant image entity, with dark gaps between the LEDs.

    Animations use a smaller scale and every second frame (twice as long): the preview of a long
    scrolling text was ~500 KB and took seconds on a Raspberry Pi.
    """
    if rendered.animated and len(rendered.frames) > 2:
        scale = animated_scale
        rendered = Rendered(rendered.frames[::2], [d * 2 for d in rendered.durations[::2]])

    def up(img: Image.Image) -> Image.Image:
        big = img.resize((img.width * scale, img.height * scale), Image.Resampling.NEAREST)
        if scale >= 4:
            draw = ImageDraw.Draw(big)
            for x in range(0, big.width, scale):
                draw.line([(x, 0), (x, big.height)], fill=(15, 15, 15))
            for y in range(0, big.height, scale):
                draw.line([(0, y), (big.width, y)], fill=(15, 15, 15))
        return big

    big = Rendered([up(f) for f in rendered.frames], rendered.durations)
    data, is_gif = encode(big)
    return data, "image/gif" if is_gif else "image/png"


def parse_color(value, default: RGB = WHITE) -> RGB:
    """Accept [r, g, b], (r, g, b), '#rrggbb' or 'rrggbb'."""
    if value is None or value == "":
        return default
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return tuple(max(0, min(255, int(v))) for v in value)  # type: ignore[return-value]
    if isinstance(value, str):
        v = value.strip().lstrip("#")
        if len(v) == 6:
            try:
                return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
            except ValueError:
                pass
    return default
