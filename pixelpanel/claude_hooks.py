"""Know when a Claude Code session needs attention, from Claude Code's own hooks.

Claude Code posts hook events to a small server on 127.0.0.1. The URL carries a random token, so a
web page in the browser cannot raise alerts. Only session id, project folder and event type are
kept; prompts, answers and tool input are dropped on arrival.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PureWindowsPath

log = logging.getLogger(__name__)

PORT = 47813
URL_BASE = f"http://127.0.0.1:{PORT}/pixelpanel"
SETTINGS_FILE = Path.home() / ".claude" / "settings.json"
EXPIRE_AFTER = 3600.0  # a session that vanished without saying so does not alarm forever
MAX_BODY = 4 * 1024 * 1024
KEEP_FIELDS = ("hook_event_name", "session_id", "cwd", "notification_type")

INPUT_TYPES = {"permission_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input"}
NOTIFICATION_MATCHER = "|".join(sorted(INPUT_TYPES | {"idle_prompt"}))
URGENCY = {"error": 3, "input": 2, "idle": 1}

# event -> matcher; events without a matcher are the ones that end an alert
HOOK_EVENTS: dict[str, str | None] = {
    "Notification": NOTIFICATION_MATCHER,
    "StopFailure": None,
    "UserPromptSubmit": None,
    "PostToolUse": None,
    "Stop": None,
    "SessionEnd": None,
}


class HookError(Exception):
    pass


@dataclass(frozen=True)
class Summary:
    """What the panel shows: the most urgent kind, its project and how many sessions wait."""

    kind: str  # input | idle | error
    project: str
    count: int


@dataclass(frozen=True)
class _Alert:
    kind: str
    project: str
    since: float


def _project(cwd: str | None) -> str:
    return (PureWindowsPath(cwd).name if cwd else "") or "Claude"


class Tracker:
    """Open alerts per session. Not thread safe: the engine calls it from its own thread."""

    def __init__(self) -> None:
        self.pending: dict[str, _Alert] = {}

    def apply(self, event: dict, now: float, kinds: set[str]) -> bool:
        """Update from one hook event; True when it raised a new alert."""
        name, session = event.get("hook_event_name"), event.get("session_id") or "?"
        kind = None
        if name == "Notification":
            ntype = event.get("notification_type")
            kind = "input" if ntype in INPUT_TYPES else "idle" if ntype == "idle_prompt" else None
        elif name == "StopFailure":
            kind = "error"
        elif name in ("UserPromptSubmit", "SessionEnd"):
            self.pending.pop(session, None)
        elif name in ("Stop", "PostToolUse"):  # it goes on, so nothing is waiting for a decision anymore
            if session in self.pending and self.pending[session].kind == "input":
                del self.pending[session]
        if kind is None or kind not in kinds:
            return False
        old = self.pending.get(session)
        self.pending[session] = _Alert(kind, _project(event.get("cwd")), old.since if old and old.kind == kind else now)
        return old is None or old.kind != kind

    def purge(self, now: float) -> bool:
        stale = [s for s, a in self.pending.items() if now - a.since > EXPIRE_AFTER]
        for session in stale:
            del self.pending[session]
        return bool(stale)

    def summary(self) -> Summary | None:
        if not self.pending:
            return None
        top = max(self.pending.values(), key=lambda a: (URGENCY[a.kind], a.since))
        return Summary(top.kind, top.project, len(self.pending))


# --- local server ----------------------------------------------------------------------------------

class HookServer:
    """Receives the hook events; on_event gets a dict with KEEP_FIELDS only, on the server's thread."""

    host = "127.0.0.1"

    def __init__(self, token: str, on_event, port: int = PORT) -> None:
        outer = self
        self._path = f"/pixelpanel/{token}"

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server name
                if not hmac.compare_digest(self.path.encode(), outer._path.encode()):
                    self._reply(404)
                    return
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:  # e.g. a huge tool input: not needed, and never blocks Claude
                    self._reply(413)
                    return
                try:
                    data = json.loads(self.rfile.read(length))
                    on_event({k: data[k] for k in KEEP_FIELDS if k in data})
                except (ValueError, TypeError, AttributeError) as err:
                    log.info("hook event not readable: %s", err)
                self._reply(200)  # empty 2xx: "nothing to say" for Claude Code

            def _reply(self, code: int) -> None:
                self.send_response(code)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args) -> None:  # the URL holds the token: keep it out of any log
                pass

        self._server = ThreadingHTTPServer((self.host, port), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, name="claude-hooks", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


# --- Claude Code settings --------------------------------------------------------------------------

def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as err:
        raise HookError(f"{path.name} cannot be read: {err}") from err
    if not isinstance(data, dict):
        raise HookError(f"{path.name} has an unexpected format")
    return data


def _save(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".pixelpanel-tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as err:
        raise HookError(f"{path.name} cannot be written: {err}") from err


def _is_ours(handler: dict) -> bool:
    return isinstance(handler, dict) and str(handler.get("url", "")).startswith(URL_BASE)


def _without_ours(data: dict) -> dict:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return data
    cleaned = {}
    for event, groups in hooks.items():
        kept = []
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept.append(group)
                continue
            handlers = [h for h in group["hooks"] if not _is_ours(h)]
            if handlers:
                kept.append({**group, "hooks": handlers})
        if kept or not isinstance(groups, list):
            cleaned[event] = kept if isinstance(groups, list) else groups
    data = {k: v for k, v in data.items() if k != "hooks"}
    if cleaned:
        data["hooks"] = cleaned
    return data


def is_installed(path: Path = SETTINGS_FILE) -> bool:
    try:
        hooks = _load(path).get("hooks") or {}
    except HookError:
        return False
    return any(_is_ours(h) for groups in hooks.values() if isinstance(groups, list)
               for g in groups if isinstance(g, dict) for h in g.get("hooks") or [])


def install(path: Path, url: str) -> None:
    """Add our hooks to Claude Code's settings; all other settings and hooks stay as they are."""
    data = _without_ours(_load(path))
    backup = path.with_name(path.name + ".pixelpanel-backup")
    if path.exists() and not backup.exists():
        try:
            backup.write_bytes(path.read_bytes())
        except OSError as err:
            raise HookError(f"no backup possible: {err}") from err
    hooks = dict(data.get("hooks") or {})
    handler = {"type": "http", "url": url, "timeout": 2}
    for event, matcher in HOOK_EVENTS.items():
        group = {"hooks": [handler]}
        if matcher:
            group = {"matcher": matcher, **group}
        hooks[event] = [*(hooks.get(event) or []), group]
    _save(path, {**data, "hooks": hooks})


def uninstall(path: Path = SETTINGS_FILE) -> None:
    """Remove only our hooks."""
    if path.exists():
        _save(path, _without_ours(_load(path)))
