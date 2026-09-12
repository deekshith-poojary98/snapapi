from __future__ import annotations

import json
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from snapapi.exceptions import SnapAPIError
from snapapi.openapi import _path_matches


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
                "match": item.get("match") if isinstance(item.get("match"), dict) else {},
                "delay_ms": int(item.get("delay_ms") or 0),
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

    def match(self, method, path, query="", body=None):
        method = (method or "").upper()
        candidates = []
        for route in self.routes:
            if route["method"] != method:
                continue
            exact = route["path"] == path
            templated = (not exact) and _path_matches(route["path"], path)
            if not exact and not templated:
                continue
            if not _constraints_match(route.get("match") or {}, query, body):
                continue
            candidates.append((0 if exact else 1, route))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.stop()
        return False


def start_mock(path, host="127.0.0.1", port=8765):
    return MockServer(load_mock_routes(path), host=host, port=port).start()


def _constraints_match(match, query, body):
    if not match:
        return True
    expected_query = match.get("query")
    if expected_query:
        actual_query = dict(parse_qsl(query or "", keep_blank_values=True))
        if not _subset_match(expected_query, actual_query):
            return False
    expected_body = match.get("body")
    if expected_body is not None:
        actual_body = body
        if isinstance(expected_body, dict) and isinstance(body, str):
            try:
                actual_body = json.loads(body) if body else {}
            except json.JSONDecodeError:
                return False
        if not _subset_match(expected_body, actual_body):
            return False
    return True


def _subset_match(expected, actual):
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(key in actual and _subset_match(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        return expected == actual
    return str(expected) == str(actual) if not isinstance(expected, bool) else expected == actual


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

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_OPTIONS(self):
        self._dispatch("OPTIONS")

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        payload = None
        text = raw.decode("utf-8") if raw else ""
        if text:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = text
        route = self.server.router.match(method, parsed.path, query=parsed.query, body=payload)
        if route is None:
            self._respond(404, {"Content-Type": "application/json"}, {"error": "not found", "path": parsed.path})
            return
        delay_ms = int(route.get("delay_ms") or 0)
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)
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
