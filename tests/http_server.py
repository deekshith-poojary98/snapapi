from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


class MockHTTPServer:
    """Local HTTP server for offline SnapAPI tests."""

    def __init__(self, host="127.0.0.1"):
        self._httpd = ThreadingHTTPServer((host, 0), _Handler)
        self._httpd.router = self
        self._thread = None
        self._lock = threading.Lock()
        self._routes = {}
        self.requests = []
        host_name, port = self._httpd.server_address
        self.host = host_name
        self.port = port
        self.base_url = f"http://{host_name}:{port}"

    def start(self):
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._httpd.shutdown()
        if self._thread:
            self._thread.join(timeout=2)
        self._httpd.server_close()

    def on(self, method, path, status=200, json=None, text=None, headers=None, fail_times=0, handler=None):
        key = (method.upper(), path)
        if handler is not None:
            self._routes[key] = handler
            return self

        state = {"failures": 0}

        def _handler(record):
            if fail_times and state["failures"] < fail_times:
                state["failures"] += 1
                return 500, {"Content-Type": "application/json"}, {"error": "temporary"}
            body = text if text is not None else json
            response_headers = dict(headers or {})
            if json is not None and "Content-Type" not in response_headers:
                response_headers["Content-Type"] = "application/json"
            return status, response_headers, body

        self._routes[key] = _handler
        return self

    def match(self, method, path):
        return self._routes.get((method.upper(), path))

    def record(self, entry):
        with self._lock:
            self.requests.append(entry)

    def bodies(self):
        return [item.get("json") or item.get("body") for item in self.requests]

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.stop()
        return False


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
        body = self._read_body()
        payload = None
        if body:
            try:
                payload = json.loads(body.decode("utf-8"))
            except ValueError:
                payload = None
        record = {
            "method": method,
            "path": parsed.path,
            "query": parsed.query,
            "headers": {key: value for key, value in self.headers.items()},
            "body": body.decode("utf-8") if body else "",
            "json": payload,
        }
        self.server.router.record(record)
        handler = self.server.router.match(method, parsed.path)
        if handler is None:
            self._respond(404, {"Content-Type": "application/json"}, {"error": "not found", "path": parsed.path})
            return
        status, headers, response_body = handler(record)
        self._respond(status, headers, response_body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

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
