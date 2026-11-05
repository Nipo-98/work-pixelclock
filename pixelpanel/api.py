"""Local interface so scripts and tools can write to the panel.

Only on 127.0.0.1, and the URL holds a random token, so a web page in the browser cannot use it.

  curl -X POST http://127.0.0.1:47814/api/<token>/message -d "Backup done"
  curl -X POST http://127.0.0.1:47814/api/<token>/message -d "{\\"text\\": \\"Build\\", \\"progress\\": 42}"
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)

PORT = 47814
MAX_BODY = 64 * 1024
DEFAULT_SECONDS = 10
_COLOR = re.compile(r"^#?[0-9a-fA-F]{6}$")


def parse_message(body: bytes, effects: tuple[str, ...]) -> tuple[dict, float | None]:
    """Page fields and seconds from a request body: plain text or JSON. ValueError when unusable."""
    text = body.decode("utf-8", "replace").strip()
    data = {"text": text}
    if text.startswith("{"):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
    message = str(data.get("text") or "").strip()[:500]
    if not message:
        raise ValueError("text is missing")
    page = {"type": "text", "text": message, "color": "#ffbe00", "icon": "", "effect": "none"}
    color = str(data.get("color") or "")
    if _COLOR.match(color):
        page["color"] = color if color.startswith("#") else f"#{color}"
    icon = str(data.get("icon") or "")
    if icon and "/" not in icon and "\\" not in icon:  # an emoji or a name, never a file on this PC
        page["icon"] = icon[:40]
    if data.get("effect") in effects:
        page["effect"] = data["effect"]
    if data.get("progress") is not None:
        page["progress"] = max(0.0, min(100.0, float(data["progress"])))
    seconds = float(data.get("seconds", DEFAULT_SECONDS))
    if not 0 <= seconds <= 86400:
        raise ValueError("seconds must be between 0 and 86400")
    return page, seconds or None  # 0 = until cleared


class LocalApi:
    """on_message(page, seconds), on_clear(), on_next(), status() -> dict run on the server's threads."""

    host = "127.0.0.1"

    def __init__(self, token: str, on_message, on_clear, on_next, status, effects: tuple[str, ...], port: int = PORT) -> None:
        prefix = f"/api/{token}/"

        class Handler(BaseHTTPRequestHandler):
            def _action(self) -> str | None:
                path = self.path.split("?")[0]
                head, tail = path[:len(prefix)], path[len(prefix):]
                return tail if hmac.compare_digest(head.encode(), prefix.encode()) else None

            def _reply(self, code: int, data: dict) -> None:
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - http.server name
                if self._action() == "status":
                    self._reply(200, status())
                else:
                    self._reply(404, {"ok": False})

            def do_POST(self) -> None:  # noqa: N802 - http.server name
                action = self._action()
                length = int(self.headers.get("Content-Length") or 0)
                if action is None:
                    self._reply(404, {"ok": False})
                elif length > MAX_BODY:
                    self._reply(413, {"ok": False, "error": "too large"})
                elif action == "message":
                    try:
                        page, seconds = parse_message(self.rfile.read(length), effects)
                    except (ValueError, TypeError) as err:
                        self._reply(400, {"ok": False, "error": str(err)})
                        return
                    on_message(page, seconds)
                    self._reply(200, {"ok": True})
                elif action == "clear":
                    on_clear()
                    self._reply(200, {"ok": True})
                elif action == "next":
                    on_next()
                    self._reply(200, {"ok": True})
                else:
                    self._reply(404, {"ok": False})

            def log_message(self, *args) -> None:  # the URL holds the token: keep it out of any log
                pass

        self._server = ThreadingHTTPServer((self.host, port), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, name="local-api", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
