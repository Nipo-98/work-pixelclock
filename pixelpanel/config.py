"""Settings, stored as JSON in %APPDATA%\\PixelPanel."""

from __future__ import annotations

import copy
import json
import logging
import os
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "PixelPanel"
CONFIG_FILE = APP_DIR / "config.json"
ICON_CACHE = APP_DIR / "icons"

PAGE_TYPES: dict[str, str] = {
    "clock": "Clock",
    "native_clock": "Clock (built into the panel)",
    "date": "Date",
    "text": "Text / Emoji",
    "image": "Image / GIF",
    "weather": "Weather",
    "teams": "Teams status",
    "claude": "Claude usage",
    "calendar": "Next meeting",
}

PAGE_DEFAULTS: dict[str, dict] = {
    "clock": {"color": "#ffffff", "effect": "none", "seconds": False, "weekday_bar": True},
    "native_clock": {"style": 1, "show_date": True},
    "date": {"color": "#00c8e6", "icon": "calendar", "effect": "none"},
    "text": {"text": "Hello!", "color": "#ffbe00", "icon": "😀", "effect": "none"},
    "image": {"path": ""},
    "weather": {"color": "#ffffff", "effect": "none"},
    "teams": {},
    "claude": {"show": "both", "logo": "claude", "animate": True},
    "calendar": {},
}


def new_page(page_type: str) -> dict:
    return {"id": uuid.uuid4().hex[:8], "type": page_type, "enabled": True, "duration": 10,
            **copy.deepcopy(PAGE_DEFAULTS[page_type])}


def default_config() -> dict:
    pages = [new_page("clock"), new_page("claude"), new_page("teams"), new_page("text")]
    pages[0]["duration"] = 15
    pages[2]["enabled"] = False
    pages[3]["enabled"] = False
    return {
        "address": None,
        "name": None,
        "power": True,
        "brightness": 60,
        "orientation": "landscape",
        "pages": pages,
        "transition": "push_left",  # see transitions.TRANSITIONS
        "teams": {
            "enabled": True,
            "status_light": True,
            "status_frame": True,
            "bar_width": 2,  # px of the status stripes left and right (1-3)
            "light_states": ["Busy", "DoNotDisturb", "InACall", "InAMeeting", "Presenting", "OnThePhone"],
        },
        "claude": {"enabled": True, "interval": 120},
        # provider: "outlook" (classic Outlook on this PC), "ics" (calendar link) or "graph"
        # (Microsoft 365 sign-in; client/tenant id from an app registration)
        "calendar": {"enabled": False, "provider": "outlook", "ics_url": "", "client_id": "", "tenant_id": "",
                     "remind_minutes": 5},
        "weather": {"city": "", "lat": None, "lon": None},
        "night": {"enabled": False, "start": "22:00", "end": "07:00", "brightness": 5},
        "autostart": False,
        "auto_update": True,
        "start_minimized": False,
    }


def _merge(base: dict, saved: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in saved.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    cfg = default_config()
    try:
        cfg = _merge(cfg, json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as err:
        log.warning("config not readable, using defaults: %s", err)
    # pages saved by an older version get the new keys of their type
    cfg["pages"] = [{**PAGE_DEFAULTS.get(p.get("type"), {}), **p} for p in cfg["pages"]
                    if p.get("type") in PAGE_TYPES]
    return cfg


def save(cfg: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CONFIG_FILE)
