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

from . import claude_hooks, sources, transitions
from .config import ICON_CACHE
from .m365 import Meeting
from .core import icons, render
from .core import protocol as proto
from .core.render import Line, Rendered, Screen

log = logging.getLogger(__name__)

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def date_lines(now: datetime) -> list[str]:
    """Weekday and day + month on two lines, e.g. Wed / 30 Sep (one line does not fit next to an icon)."""
    return [WEEKDAYS[now.weekday()], f"{now.day} {MONTHS[now.month - 1]}"]
EMOJI_FONT = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "seguiemj.ttf"
MAX_GIF_FRAMES = 200


@dataclass
class Context:
    """Live data the pages draw from."""

    teams: str | None = None
    claude: sources.ClaudeUsage | None = None
    weather: sources.Weather | None = None
    meeting: Meeting | None = None
    attention: claude_hooks.Summary | None = None


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


# Clawd, Claude Code's mascot, from its terminal art "▐▛███▜▌ / ▝▜█████▛▘ / ▘▘ ▝▝":
# every quadrant block is one pixel wide and two tall (terminal cells are twice as tall as wide)
_CLAWD = [
    "..XXXXXXXXXXXX..",
    "..XX.XXXXXX.XX..",
    "XXXXXXXXXXXXXXXX",
    "..XXXXXXXXXXXX..",
    "...X.X....X.X...",
]


def clawd_icon() -> Image.Image:
    img = Image.new("RGBA", (16, 10), (0, 0, 0, 0))
    for y, row in enumerate(_CLAWD):
        for x, c in enumerate(row):
            if c == "X":
                img.putpixel((x, 2 * y), CLAUDE_ORANGE + (255,))
                img.putpixel((x, 2 * y + 1), CLAUDE_ORANGE + (255,))
    return img


# Clawd at full resolution (16 x 10, every terminal row doubled) for the animation
_CLAWD_FULL = [row for row in _CLAWD for _ in (0, 1)]
CLAWD_GROUND = 14  # row below Clawd's feet on the 16 x 16 icon canvas


def _clawd_pose(eyes: str = "open", arm: str | None = None, dy: int = 0, sit: bool = False,
                step: bool = False) -> Image.Image:
    """One frame of Clawd on a 16 x 16 canvas.

    eyes: open | closed | left | right; arm: None | out | in (right arm raised, hand tipping); dy: lift (hop);
    sit: legs folded away and the body lowered onto the ground."""
    rows = [list(r) for r in _CLAWD_FULL]
    if step:  # walking: the legs are one pixel further
        rows[8] = rows[9] = list("..X.X....X.X....")
    for y in (2, 3):  # eye holes at x 4 and 11
        for x in (4, 11):
            rows[y][x] = "X"
    if eyes != "closed":
        shift = {"left": -1, "right": 1}.get(eyes, 0)
        for y in (2, 3):
            for x in (4 + shift, 11 + shift):
                rows[y][x] = "."
    else:
        for x in (4, 11):
            rows[3][x] = "."  # a thin slit: closed eye
    if arm:
        for y in (4, 5):  # the right arm leaves its resting place
            rows[y][14] = rows[y][15] = "."
    if sit:
        rows = rows[:8]  # no legs
    top = CLAWD_GROUND - len(rows) - dy
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))

    def put(x: int, y: int) -> None:
        if 0 <= x < 16 and 0 <= y < 16:
            img.putpixel((x, y), CLAUDE_ORANGE + (255,))

    for y, row in enumerate(rows):
        for x, c in enumerate(row):
            if c == "X":
                put(x, top + y)
    if arm:
        # arm from the shoulder up past the head; the hand tips inwards ("in") or outwards ("out")
        for y in range(top - 1, top + 4):
            put(14, y)
            put(15, y)
        hand_x = (13, 14) if arm == "in" else (14, 15)  # "in" still touches the arm at x 14
        for x in hand_x:
            put(x, top - 3)
            put(x, top - 2)
    return img


def clawd_frames() -> list[tuple[str, Image.Image, int]]:
    """Clawd's loop (~8 s): blink, look around, wave, hop, sit down and get up. (name, picture, ms)"""
    seq = [
        ("idle", {}, 1400), ("blink", {"eyes": "closed"}, 140), ("idle", {}, 900),
        ("look_left", {"eyes": "left"}, 600), ("look_right", {"eyes": "right"}, 600), ("idle", {}, 400),
        ("wave_up", {"arm": "out"}, 220), ("wave_in", {"arm": "in"}, 220),
        ("wave_up", {"arm": "out"}, 220), ("wave_in", {"arm": "in"}, 220),
        ("wave_up", {"arm": "out"}, 220), ("idle", {}, 600),
        ("hop_crouch", {"sit": True}, 120), ("hop_up", {"dy": 2}, 120), ("hop_top", {"dy": 3}, 160),
        ("hop_up", {"dy": 2}, 120), ("idle", {}, 700),
        ("sit", {"sit": True}, 1600), ("blink_sit", {"sit": True, "eyes": "closed"}, 140),
        ("sit", {"sit": True}, 600), ("idle", {}, 200),
    ]
    return [(name, _clawd_pose(**kw), ms) for name, kw, ms in seq]


CUSTOM_ICONS = {"claude": claude_icon, "clawd": clawd_icon}
CLAUDE_LOGOS = {"claude": "Claude logo", "clawd": "Clawd (mascot)"}


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
    if spec in CUSTOM_ICONS:
        return CUSTOM_ICONS[spec]()
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


BAR_GAP = 1  # dark column between a status stripe and the page, so icons never touch the stripe


def inner_width(width: int, bars: int) -> int:
    """Width left for the page next to status stripes of `bars` px (plus their gap)."""
    return width - 2 * (bars + BAR_GAP) if bars else width


def with_status_bars(inner: Rendered, color: tuple[int, int, int], bars: int) -> Rendered:
    """Put `bars` px wide stripes in `color` left and right of a page drawn `inner_width` wide.

    The page is drawn narrower instead of painting over it, so the stripes never cover content.
    Only the sides: two text lines need all 16 rows, a top/bottom border would cut them."""
    framed = []
    for f in inner.frames:
        out = Image.new("RGB", (f.width + 2 * (bars + BAR_GAP), f.height), render.BLACK)
        draw = ImageDraw.Draw(out)
        draw.rectangle((0, 0, bars - 1, f.height - 1), fill=color)
        draw.rectangle((out.width - bars, 0, out.width - 1, f.height - 1), fill=color)
        out.paste(f, (bars + BAR_GAP, 0))
        framed.append(out)
    return Rendered(framed, inner.durations, inner.scrolls, inner.valid_until)


def claude_screen(usage: sources.ClaudeUsage | None, show: str, logo: str = "claude") -> Screen:
    icon = CUSTOM_ICONS.get(logo, claude_icon)()
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
                   Line(f"Wk {pct(usage.week)}", usage_color(usage.week))], icon=icon)


LED_UNLIT = (44, 50, 66)
LED_BACK = (20, 23, 31)


@lru_cache(maxsize=8)
def _dot_mask(w: int, h: int, cell: int) -> Image.Image:
    tile = Image.new("L", (cell, cell), 0)
    m = max(1, cell // 8)
    ImageDraw.Draw(tile).ellipse((m, m, cell - 1 - m, cell - 1 - m), fill=255)
    mask = Image.new("L", (w * cell, h * cell), 0)
    for x in range(w):
        for y in range(h):
            mask.paste(tile, (x * cell, y * cell))
    return mask


def led_preview(rendered: Rendered, cell: int = 8) -> tuple[bytes, str]:
    """Preview that looks like the panel: round LEDs, unlit ones faintly visible."""
    if len(rendered.frames) > 60:  # long scrolls: half the frames, twice as long (short loops keep every frame)
        rendered = Rendered(rendered.frames[::2], [d * 2 for d in rendered.durations[::2]])
    w, h = rendered.frames[0].size
    dots = _dot_mask(w, h, cell)
    out = []
    for f in rendered.frames:
        big = f.convert("RGB").resize((w * cell, h * cell), Image.Resampling.NEAREST)
        lit = big.convert("L").point(lambda v: 255 if v > 8 else 0)
        leds = Image.new("RGB", big.size, LED_UNLIT)
        leds.paste(big, mask=lit)
        frame = Image.new("RGB", big.size, LED_BACK)
        frame.paste(leds, mask=dots)
        out.append(frame)
    data, is_gif = render.encode(Rendered(out, rendered.durations if len(out) > 1 else []))
    return data, "image/gif" if is_gif else "image/png"


CAL_COLOR = (0, 200, 230)


def calendar_screen(meeting: Meeting | None, now: datetime, remind: bool) -> Screen:
    """Two lines: when (time, countdown or 'bis HH:MM') and the subject (scrolls when long)."""
    icon = icons.builtin_icon("calendar")
    if meeting is None:
        return Screen([Line("No", sources.GREY), Line("meetings", sources.GREY)], icon=icon)
    start, end = meeting.start.astimezone(), meeting.end.astimezone()
    now = now.astimezone()
    if meeting.running(now):
        when, color = f"till {end:%H:%M}", (230, 20, 20)
    elif remind:
        mins = max(0, -(-int((start - now).total_seconds()) // 60))
        when, color = ("now" if mins == 0 else f"in {mins} min"), (255, 110, 0)
    elif start.date() == now.date():
        when, color = f"{start:%H:%M}", CAL_COLOR
    else:
        when, color = f"{WEEKDAYS[start.weekday()]} {start:%H:%M}", CAL_COLOR
    return Screen([Line(when, color), Line(meeting.subject, render.WHITE)], icon=icon)


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


ATTENTION_COLORS = {"input": (255, 190, 0), "idle": (0, 200, 60), "error": (230, 20, 20)}
ATTENTION_TITLES = {"input": "Attention", "idle": "Finished", "error": "Error"}


def attention_title(summary: claude_hooks.Summary) -> str:
    return f"{summary.count} waiting" if summary.count > 1 else ATTENTION_TITLES[summary.kind]


def _attention_timeline() -> list[tuple[Image.Image, int]]:
    """Clawd calling for attention: waves, then a short pause with a blink."""
    wave = [(_clawd_pose(arm="out"), 220), (_clawd_pose(arm="in"), 220)]
    return [*wave, *wave, *wave, (_clawd_pose(), 500), (_clawd_pose(eyes="closed"), 140), (_clawd_pose(), 700)]


def _paste_pose(frame: Image.Image, pose: Image.Image, x: int = 0) -> None:
    """Clawd on a cleared square at x; larger panels get him scaled up by a whole factor."""
    size = min(frame.size)
    if size != pose.width:
        pose = pose.resize((size, size), Image.Resampling.NEAREST)
    ImageDraw.Draw(frame).rectangle((x, 0, x + size - 1, size - 1), fill=render.BLACK)
    frame.paste(pose, (x, 0), pose)


def render_attention(summary: claude_hooks.Summary, w: int, h: int) -> Rendered:
    """Clawd waving next to the reason and the project; a long project name scrolls meanwhile."""
    screen = Screen([Line(attention_title(summary), ATTENTION_COLORS[summary.kind]),
                     Line(summary.project, render.WHITE)], icon=_clawd_pose())
    base = render.render_screen(screen, w, h)
    if min(w, h) % 16:  # no clean scaling for Clawd on this panel size
        return base
    timeline = _attention_timeline()
    if not base.animated:
        frames = [f.copy() for f in base.frames * len(timeline)]
        for frame, (pose, _ms) in zip(frames, timeline, strict=True):
            _paste_pose(frame, pose)
        return Rendered(frames, [ms for _, ms in timeline])
    # scrolling text sets the frames; Clawd's pose follows the elapsed time
    cycle = sum(ms for _, ms in timeline)
    frames, elapsed = [], 0
    for frame, duration in zip(base.frames, base.durations, strict=True):
        at, frame = elapsed % cycle, frame.copy()
        for pose, ms in timeline:
            if at < ms:
                _paste_pose(frame, pose)
                break
            at -= ms
        frames.append(frame)
        elapsed += duration
    return Rendered(frames, base.durations, scrolls=True)


def attention_pull(old: Image.Image, final: Image.Image) -> Rendered:
    """Clawd walks in from the right and pulls the alert behind him over the old page."""
    w, h = final.size
    step = 2
    frames = []
    for k, x in enumerate(range(w - step, 0, -step)):
        frame = old.copy()
        frame.paste(final.crop((0, 0, w - x, h)), (x, 0))
        _paste_pose(frame, _clawd_pose(step=bool((k // 2) % 2)), x)
        frames.append(frame)
    return Rendered([*frames, final.copy()], [50] * len(frames) + [transitions.HOLD_MS])


def animate_icon(screen: Screen, w: int, h: int) -> Rendered:
    """The page once per Clawd pose. Pages that scroll anyway keep a still Clawd (one loop only)."""
    still = render.render_screen(screen, w, h)
    if still.animated:
        return still
    frames, durations = [], []
    for _name, pose, ms in clawd_frames():
        frames.append(render.render_screen(Screen(**{**screen.__dict__, "icon": pose}), w, h).frames[0])
        durations.append(ms)
    return Rendered(frames, durations)


def render_page(page: dict, ctx: Context, w: int, h: int, now: datetime) -> Rendered | Native:
    t = page["type"]
    if t == "attention":
        summary = ctx.attention or claude_hooks.Summary("idle", "Claude", 1)
        return render_attention(summary, w, h)
    if t == "native_clock":
        return Native(proto.set_clock_mode(int(page.get("style", 1)), now, bool(page.get("show_date", True))),
                      f"Built-in clock {page.get('style', 1)}")
    if t == "clock":
        return render.render_clock(now, w, h, _color(page), blink=True, weekday_bar=page.get("weekday_bar", True),
                                   effect=page.get("effect", "none"), seconds=page.get("seconds", False))
    if t == "date":
        screen = Screen([Line(text, _color(page)) for text in date_lines(now)],
                        icon=load_icon(page.get("icon")), effect=page.get("effect", "none"))
    elif t == "text":
        screen = text_screen(page)
    elif t == "image":
        path = page.get("path", "")
        if path and os.path.isfile(path):
            try:
                return render_image_file(path, w, h)
            except OSError as err:
                log.warning("image %s: %s", path, err)
        screen = Screen([Line("No image", (110, 110, 120))], big=False)
    elif t == "weather":
        wx = ctx.weather
        if wx is None:
            screen = Screen([Line("Weather...", (110, 110, 120))], icon=icons.builtin_icon("cloud"), big=False)
        else:
            lines = [Line(f"{wx.temperature:.0f}°", _color(page))]
            if wx.low is not None and wx.high is not None and h >= 16:
                lines.append(Line(f"{wx.low:.0f}/{wx.high:.0f}°", (0, 200, 230)))
            screen = Screen(lines, icon=icons.builtin_icon(wx.icon), effect=page.get("effect", "none"))
    elif t == "teams":
        screen = teams_screen(ctx.teams, light=False)
    elif t == "claude":
        screen = claude_screen(ctx.claude, page.get("show", "both"), page.get("logo", "claude"))
        if page.get("logo") == "clawd" and page.get("animate", True):
            return animate_icon(screen, w, h)
    elif t == "calendar":
        screen = calendar_screen(ctx.meeting, now, remind=bool(page.get("remind")))
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
    if t == "attention":
        return base + (ctx.attention,)
    if t == "calendar":  # the minute matters for the countdown and for "running"
        return base + (ctx.meeting, now.strftime("%Y%m%d%H%M"))
    return base
