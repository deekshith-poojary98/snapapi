from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from snapapi.exceptions import SnapAPIError


def load_mock_routes(path):
    target = Path(path)
    if not target.is_file():
        raise SnapAPIError(f"Mock file not found: {path}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SnapAPIError(f"Invalid mock JSON: {exc.msg}") from exc
    if isinstance(payload, dict):
        routes = payload.get("routes")
    elif isinstance(payload, list):
        routes = payload
    else:
        raise SnapAPIError("Mock file must be a JSON object with a routes array")
    if not isinstance(routes, list):
        raise SnapAPIError("Mock routes must be a list")
    parsed = []
    for item in routes:
        if not isinstance(item, dict):
            continue
        parsed.append(
            {
                "method": str(item.get("method") or "GET").upper(),
                "path": item.get("path") or "/",
                "status": int(item.get("status") or 200),
                "json": item.get("json"),
                "body": item.get("body"),
                "headers": dict(item.get("headers") or {}),
            }
        )
    return parsed


class MockServer:
    """Tiny JSON-file mock HTTP server for local SnapAPI runs."""

    def __init__(self, routes, host="127.0.0.1", port=8765):
        self.routes = list(routes or [])
        self._httpd = ThreadingHTTPServer((host, int(port)), _Handler)
        self._httpd.router = self
        self._thread = None
        host_name, bound = self._httpd.server_address
        self.host = host_name
        self.port = bound
        self.url = f"http://{host_name}:{bound}"

    def start(self):
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def serve_forever(self):
        self._httpd.serve_forever()

    def stop(self):
        self._httpd.shutdown()
        if self._thread:
            self._thread.join(timeout=2)
        self._httpd.server_close()

    def match(self, method, path):
        for route in self.routes:
            if route["method"] == method.upper() and route["path"] == path:
                return route
        return None

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.stop()
        return False


def start_mock(path, host="127.0.0.1", port=8765):
    return MockServer(load_mock_routes(path), host=host, port=port).start()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        return

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length:
            self.rfile.read(length)
        route = self.server.router.match(method, parsed.path)
        if route is None:
            self._respond(404, {"Content-Type": "application/json"}, {"error": "not found", "path": parsed.path})
            return
        headers = dict(route.get("headers") or {})
        body = route.get("json") if route.get("json") is not None else route.get("body")
        self._respond(route["status"], headers, body)

    def _respond(self, status, headers, body):
        headers = dict(headers or {})
        if isinstance(body, (dict, list)):
            raw = json.dumps(body).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        elif body is None:
            raw = b""
        elif isinstance(body, bytes):
            raw = body
        else:
            raw = str(body).encode("utf-8")
        headers.setdefault("Content-Type", "text/plain")
        headers["Content-Length"] = str(len(raw))
        headers["Connection"] = "close"
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, str(value))
        self.end_headers()
        if self.command != "HEAD" and raw:
            self.wfile.write(raw)
