"""Loopback-only internal control interface (status + fault injection).

Bound to 127.0.0.1 inside the container: not reachable from any Docker network,
the host, or the WAN. Used only via `docker exec ... python -m orchid.tools.faultctl`.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

MAX_BODY = 4096
LOOPBACK = "127.0.0.1"
DEFAULT_PORT = 9100

Handler = Callable[[str, str, dict[str, Any]], tuple[int, dict[str, Any]]]


def serve(handler: Handler, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    class _H(BaseHTTPRequestHandler):
        def _reply(self, code: int, body: dict[str, Any]) -> None:
            data = json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _dispatch(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._reply(413, {"error": "body too large"})
                return
            body: dict[str, Any] = {}
            if length:
                try:
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError
                except ValueError:
                    self._reply(400, {"error": "invalid json"})
                    return
            try:
                code, out = handler(method, self.path, body)
            except (ValueError, KeyError) as exc:
                code, out = 400, {"error": str(exc)}
            self._reply(code, out)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def log_message(self, *args: Any) -> None:  # silence default stderr logging
            return

    server = ThreadingHTTPServer((LOOPBACK, port), _H)
    thread = threading.Thread(target=server.serve_forever, name="internal-http", daemon=True)
    thread.start()
    return server
