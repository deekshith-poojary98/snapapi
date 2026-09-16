import io
import socket
from urllib.parse import urlsplit

import requests
from requests import Response
from requests.structures import CaseInsensitiveDict

from snapapi.engine import Engine
from snapapi.parser import TestParser

PUBLIC_TEST_HOST = "example.com"
PUBLIC_TEST_IP = "93.184.216.34"


def stub_dns_rebinding(monkeypatch, host, check_ip=PUBLIC_TEST_IP, connect_ip="127.0.0.1"):
    """Simulate DNS TOCTOU: early lookups return ``check_ip``, later ones ``connect_ip``.

    ``assert_public_url`` / engine pre-checks resolve with ``port=None`` and see the
    public address. Connect-time ``getaddrinfo`` (with a real port) returns the
    private address — unless SnapAPI pins the validated sockaddr.
    """
    real_getaddrinfo = socket.getaddrinfo
    calls = []

    def getaddrinfo(name, port, *args, **kwargs):
        if name == host:
            # Validation-style lookups use port=None; connect uses the service port.
            ip = check_ip if port is None else connect_ip
            calls.append((name, port, ip))
            port_num = 0 if port is None else port
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port_num))]
        return real_getaddrinfo(name, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return calls


def stub_public_host_http(monkeypatch, routes, host=PUBLIC_TEST_HOST, ip=PUBLIC_TEST_IP):
    """Answer HTTP for ``host`` locally while leaving other destinations unchanged.

    ``routes`` maps path -> (status, headers, body). Returns the list of URLs that
    reached ``HTTPAdapter.send`` (the actual HTTP transport).
    """
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(name, port, *args, **kwargs):
        if name == host:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0 if port is None else port))]
        return real_getaddrinfo(name, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)

    sent = []
    real_send = requests.adapters.HTTPAdapter.send

    def send(self, request, **kwargs):
        sent.append(request.url)
        parsed = urlsplit(request.url)
        if parsed.hostname == host:
            status, headers, body = routes[parsed.path or "/"]
            response = Response()
            response.status_code = status
            response.headers = CaseInsensitiveDict(headers or {})
            response.url = request.url
            response.request = request
            response.reason = "Found" if status in {301, 302, 303, 307, 308} else "OK"
            if body is None:
                raw = b""
            elif isinstance(body, bytes):
                raw = body
            else:
                raw = str(body).encode("utf-8")
            response._content = raw
            response.encoding = "utf-8"
            return response
        return real_send(self, request, **kwargs)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    return sent


def parse_dsl(text, filename="<string>"):
    return TestParser().parse_text(text, filename=filename)


def run_dsl(text, variables=None, timeout=None, tags=None, names=None, stop_on_failure=None, retry_backoff=0, **kwargs):
    suite = parse_dsl(text)
    stream = io.StringIO()
    engine = Engine(
        suite,
        variables=variables,
        timeout=timeout,
        tags=tags,
        names=names,
        stop_on_failure=stop_on_failure,
        retry_backoff=retry_backoff,
        stream=stream,
        **kwargs,
    )
    result = engine.run()
    return result, engine, stream.getvalue()
