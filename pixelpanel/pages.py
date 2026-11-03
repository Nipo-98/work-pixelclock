"""Turn a page (dict from the config) plus live data into a picture or a native panel command."""

from __future__ import annotations

import logging
import os
import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageSequence

from . import sources
from .config import ICON_CACHE
from .core import icons, render
from .core import protocol as proto
from .core.render import Line, Rendered, Screen

log = logging.getLogger(__name__)

WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
EMOJI_FONT = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "seguiemj.ttf"
MAX_GIF_FRAMES = 200


@dataclass
class Context:
    """Live data the pages draw from."""

    teams: str | None = None
    claude: sources.ClaudeUsage | None = None
    weather: sources.Weather | None = None


@dataclass
class Native:
    """A command the panel executes itself instead of a picture (built-in clock faces)."""

    data: bytes
    label: str


# --- icons ---------------------------------------------------------------------------------------

_CLAUDE_ICON = [
    "...O....",
    ".O.O.O..",
    "..OOO...",
    "OOOOOOO.",
    "..OOO...",
    ".O.O.O..",
    "...O....",
    "........",
]
CLAUDE_ORANGE = (217, 119, 87)


def claude_icon() -> Image.Image:
    img = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    for y, row in enumerate(_CLAUDE_ICON):
        for x, c in enumerate(row):
            if c == "O":
                img.putpixel((x, y), CLAUDE_ORANGE + (255,))
    return img


def dot_icon(color: tuple[int, int, int], size: int = 16) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    m = max(1, size // 8)
    ImageDraw.Draw(img).ellipse((m, m, size - 1 - m, size - 1 - m), fill=color + (255,))
    return img


EMOJI_RE = re.compile(r"[\U0001F000-\U0001FFFF\u2300-\u27BF\u2B00-\u2BFF]\ufe0f?(\u200d[^\s]\ufe0f?)*")


def split_emoji(text: str) -> tuple[str, str | None]:
    """Text without emojis, and the first emoji (shown as the icon)."""
    first = EMOJI_RE.search(text)
    rest = EMOJI_RE.sub("", text).replace("\ufe0f", "")
    return " ".join(rest.split()), first.group(0) if first else None


@lru_cache(maxsize=64)
def emoji_icon(emoji: str, size: int = 16) -> Image.Image | None:
    try:
        f = ImageFont.truetype(str(EMOJI_FONT), 109)
    except OSError:
        return None
    big = Image.new("RGBA", (180, 160), (0, 0, 0, 0))
    ImageDraw.Draw(big).text((90, 80), emoji.replace("\ufe0f", ""), font=f, embedded_color=True, anchor="mm")
    box = big.getbbox()
    if not box:
        return None
    img = big.crop(box)
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, ((size - img.width) // 2, (size - img.height) // 2))
    # LEDs have no half transparency: snap alpha so edges do not turn into dark fringes
    alpha = out.getchannel("A").point(lambda a: 255 if a >= 96 else 0)
    out.putalpha(alpha)
    return out


@lru_cache(maxsize=64)
def lametric_icon(icon_id: str) -> Image.Image | None:
    ICON_CACHE.mkdir(parents=True, exist_ok=True)
    path = ICON_CACHE / f"lametric_{icon_id}"
    if not path.exists():
        try:
            req = urllib.request.Request(icons.lametric_url(icon_id), headers={"User-Agent": sources.USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                path.write_bytes(resp.read())
        except OSError as err:
            log.warning("LaMetric icon %s: %s", icon_id, err)
            return None
    try:
        return icons.icon_from_bytes(path.read_bytes())
    except OSError:
        return None


def load_icon(spec: str | None, size: int = 16) -> Image.Image | None:
    """Icon from a built-in name, 'lm:<id>' / a number (LaMetric), an image file or an emoji."""
    spec = (spec or "").strip()
    if not spec or spec == "none":
        return None
    if (img := icons.builtin_icon(spec)) is not None:
        return img
    if spec.startswith("lm:") or spec.isdigit():
        return lametric_icon(spec.removeprefix("lm:"))
    if os.path.isfile(spec):
        try:
            return icons.icon_from_bytes(Path(spec).read_bytes())
        except OSError:
            return None
    return emoji_icon(spec, size)


# --- pages ---------------------------------------------------------------------------------------

def _color(page: dict, key: str = "color", default=render.WHITE):
    return render.parse_color(page.get(key), default)


def usage_color(pct: float | None) -> tuple[int, int, int]:
    if pct is None:
        return (110, 110, 120)
    return (0, 200, 60) if pct < 50 else (255, 190, 0) if pct < 80 else (230, 20, 20)


def teams_screen(state: str | None, light: bool) -> Screen:
    if light:  # status light: the whole panel glows in the state's colour, text over the full width
        label, color = sources.teams_label(state)
        bg = tuple(c // 4 for c in color)
        return Screen([Line(label, render.WHITE)], background=bg, big=False)
    label, color = sources.teams_label(state, short=True)
    return Screen([Line(label, color)], icon=dot_icon(color), big=False)


def with_status_bars(inner: Rendered, color: tuple[int, int, int], bars: int) -> Rendered:
    """Put `bars` px wide stripes in `color` left and right of a picture drawn `2 * bars` narrower.

    The page is drawn narrower instead of painting over it, so the stripes never cover content.
    Only the sides: two text lines need all 16 rows, a top/bottom border would cut them."""
    framed = []
    for f in inner.frames:
        out = Image.new("RGB", (f.width + 2 * bars, f.height), color)
        out.paste(f, (bars, 0))
        framed.append(out)
    return Rendered(framed, inner.durations, inner.scrolls, inner.valid_until)


def claude_screen(usage: sources.ClaudeUsage | None, show: str) -> Screen:
    icon = claude_icon()
    if usage is None:
        return Screen([Line("Claude ...", CLAUDE_ORANGE)], icon=icon, big=False)
    if usage.error:
        return Screen([Line(f"Claude: {usage.error}", CLAUDE_ORANGE)], icon=icon, big=False)

    def pct(v):
        return "?" if v is None else f"{v:.0f}%"

    if show == "session":
        return Screen([Line(pct(usage.session), usage_color(usage.session))], icon=icon,
                      progress=usage.session or 0, progress_color=usage_color(usage.session))
    if show == "week":
        return Screen([Line(pct(usage.week), usage_color(usage.week))], icon=icon,
                      progress=usage.week or 0, progress_color=usage_color(usage.week))
    return Screen([Line(f"5h {pct(usage.session)}", usage_color(usage.session)),
                   Line(f"Wo {pct(usage.week)}", usage_color(usage.week))], icon=icon)


def render_image_file(path: str, w: int, h: int) -> Rendered:
    with Image.open(path) as img:
        frames, durations = [], []
        for frame in ImageSequence.Iterator(img):
            f = frame.convert("RGBA")
            f.thumbnail((w, h), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (w, h), render.BLACK)
            canvas.paste(f, ((w - f.width) // 2, (h - f.height) // 2), f)
            frames.append(canvas)
            durations.append(max(20, int(frame.info.get("duration", 100) or 100)))
            if len(frames) >= MAX_GIF_FRAMES:
                break
    return Rendered(frames, durations if len(frames) > 1 else [])


def text_screen(page: dict) -> Screen:
    text, emoji = split_emoji(page.get("text", ""))
    icon_spec = page.get("icon") or emoji
    return Screen([Line(text or " ", _color(page))], icon=load_icon(icon_spec),
                  effect=page.get("effect", "none"))


def render_page(page: dict, ctx: Context, w: int, h: int, now: datetime) -> Rendered | Native:
    t = page["type"]
    if t == "native_clock":
        return Native(proto.set_clock_mode(int(page.get("style", 1)), now, bool(page.get("show_date", True))),
                      f"Eingebaute Uhr {page.get('style', 1)}")
    if t == "clock":
        return render.render_clock(now, w, h, _color(page), blink=True, weekday_bar=page.get("weekday_bar", True),
                                   effect=page.get("effect", "none"), seconds=page.get("seconds", False))
    if t == "date":
        screen = Screen([Line(f"{WEEKDAYS[now.weekday()]} {now:%d.%m.}", _color(page))],
                        icon=load_icon(page.get("icon")), effect=page.get("effect", "none"), big=False)
    elif t == "text":
        screen = text_screen(page)
    elif t == "image":
        path = page.get("path", "")
        if path and os.path.isfile(path):
            try:
                return render_image_file(path, w, h)
            except OSError as err:
                log.warning("image %s: %s", path, err)
        screen = Screen([Line("Kein Bild", (110, 110, 120))], big=False)
    elif t == "weather":
        wx = ctx.weather
        if wx is None:
            screen = Screen([Line("Wetter ...", (110, 110, 120))], icon=icons.builtin_icon("cloud"), big=False)
        else:
            lines = [Line(f"{wx.temperature:.0f}°", _color(page))]
            if wx.low is not None and wx.high is not None and h >= 16:
                lines.append(Line(f"{wx.low:.0f}/{wx.high:.0f}°", (0, 200, 230)))
            screen = Screen(lines, icon=icons.builtin_icon(wx.icon), effect=page.get("effect", "none"))
    elif t == "teams":
        screen = teams_screen(ctx.teams, light=False)
    elif t == "claude":
        screen = claude_screen(ctx.claude, page.get("show", "both"))
    else:
        screen = Screen([Line("?")])
    return render.render_screen(screen, w, h)


def signature(page: dict, ctx: Context, now: datetime) -> tuple:
    """Changes whenever the picture of the page would change; equal -> no need to send again."""
    t = page["type"]
    base = (page.get("id"), tuple(sorted((k, str(v)) for k, v in page.items() if k not in ("duration", "enabled"))))
    if t == "clock":
        return base + (now.strftime("%Y%m%d%H%M"),)
    if t == "native_clock":
        return base
    if t == "date":
        return base + (now.date(),)
    if t == "image":
        try:
            return base + (os.path.getmtime(page.get("path", "")),)
        except OSError:
            return base
    if t == "weather":
        return base + (ctx.weather,)
    if t == "teams":
        return base + (ctx.teams,)
    if t == "claude":
        u = ctx.claude
        return base + ((u.session, u.week, u.error) if u else None,)
    return base
