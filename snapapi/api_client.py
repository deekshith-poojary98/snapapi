from __future__ import annotations

import socket
from contextlib import contextmanager
from pathlib import Path
from socket import timeout as SocketTimeout

import requests
from requests.adapters import DEFAULT_POOLBLOCK, HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NameResolutionError, NewConnectionError
from urllib3.poolmanager import PoolManager

from snapapi.exceptions import SnapAPIError
from snapapi.safety import assert_public_url, create_safe_connection


class _SafeHTTPConnection(HTTPConnection):
    """HTTP connection that pins validated DNS results at connect time."""

    def _new_conn(self):
        try:
            return create_safe_connection(
                (self._dns_host, self.port),
                self.timeout,
                source_address=self.source_address,
                socket_options=self.socket_options,
            )
        except SnapAPIError:
            raise
        except socket.gaierror as exc:
            raise NameResolutionError(self.host, self, exc) from exc
        except SocketTimeout as exc:
            raise ConnectTimeoutError(
                self,
                f"Connection to {self.host} timed out. (connect timeout={self.timeout})",
            ) from exc
        except OSError as exc:
            raise NewConnectionError(self, f"Failed to establish a new connection: {exc}") from exc


class _SafeHTTPSConnection(HTTPSConnection):
    """HTTPS connection that pins validated DNS results at connect time."""

    def _new_conn(self):
        return _SafeHTTPConnection._new_conn(self)


class _SafeHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _SafeHTTPConnection


class _SafeHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _SafeHTTPSConnection


_SAFE_POOL_CLASSES = {
    "http": _SafeHTTPConnectionPool,
    "https": _SafeHTTPSConnectionPool,
}


class _SafeURLAdapter(HTTPAdapter):
    """Reject disallowed destinations before connecting, including redirect hops.

    Connections use ``create_safe_connection`` so the TCP peer is the same
    address that passed the safe-url policy (no DNS rebinding TOCTOU).
    """

    def init_poolmanager(self, connections, maxsize, block=DEFAULT_POOLBLOCK, **pool_kwargs):
        self._pool_connections = connections
        self._pool_maxsize = maxsize
        self._pool_block = block
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )
        self.poolmanager.pool_classes_by_scheme = _SAFE_POOL_CLASSES

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        manager = super().proxy_manager_for(proxy, **proxy_kwargs)
        manager.pool_classes_by_scheme = _SAFE_POOL_CLASSES
        return manager

    def send(self, request, **kwargs):
        assert_public_url(request.url)
        for proxy_url in (kwargs.get("proxies") or {}).values():
            if proxy_url:
                assert_public_url(proxy_url)
        return super().send(request, **kwargs)


class _SafeSession(requests.Session):
    """Session whose every HTTP send, including redirects, is safe-url checked."""

    def __init__(self):
        super().__init__()
        # Env proxies (HTTP_PROXY) must not smuggle traffic to a private peer.
        self.trust_env = False
        adapter = _SafeURLAdapter()
        self.mount("http://", adapter)
        self.mount("https://", adapter)


class APIClient:
    """Session-backed HTTP client with JSON, form, raw, multipart, and GraphQL bodies."""

    def __init__(
        self,
        base_url=None,
        timeout=30,
        follow_redirects=True,
        verify=True,
        cert=None,
        proxies=None,
        safe_url=False,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self.follow_redirects = follow_redirects
        self.verify = verify
        self.cert = cert
        self.proxies = dict(proxies or {})
        self.safe_url = bool(safe_url)
        self.session = _SafeSession() if self.safe_url else requests.Session()
        self.session.verify = verify
        if cert:
            self.session.cert = cert
        if self.proxies:
            if self.safe_url:
                for proxy_url in self.proxies.values():
                    if proxy_url:
                        assert_public_url(proxy_url)
            self.session.proxies.update(self.proxies)

    def request(
        self,
        method,
        endpoint,
        json=None,
        data=None,
        headers=None,
        timeout=None,
        files=None,
        body_type=None,
        raw=None,
        content_type=None,
        follow_redirects=None,
        auth=None,
    ):
        url = self._build_url(endpoint)
        kwargs = {
            "headers": dict(headers or {}),
            "timeout": self.timeout if timeout is None else timeout,
            "allow_redirects": self.follow_redirects if follow_redirects is None else follow_redirects,
        }
        if auth is not None:
            kwargs["auth"] = auth
        kind = (body_type or "json").lower()
        if files:
            kwargs["files"] = files
            if data is not None:
                kwargs["data"] = data
        elif kind == "form":
            kwargs["data"] = data if data is not None else json
        elif kind == "raw":
            kwargs["data"] = raw if raw is not None else data
            if content_type:
                kwargs["headers"]["Content-Type"] = content_type
        elif kind == "graphql":
            kwargs["json"] = json if json is not None else data
        elif json is not None:
            kwargs["json"] = json
        elif data is not None:
            kwargs["json"] = data
        return self.session.request(method.upper(), url, **kwargs)

    def get(self, endpoint, headers=None, timeout=None, **kwargs):
        if "json" in kwargs:
            return self.request("GET", endpoint, json=kwargs["json"], headers=headers, timeout=timeout)
        return self.request("GET", endpoint, headers=headers, timeout=timeout)

    def post(self, endpoint, data=None, headers=None, timeout=None, **kwargs):
        body = kwargs["json"] if "json" in kwargs else data
        return self.request("POST", endpoint, json=body, headers=headers, timeout=timeout)

    def put(self, endpoint, data=None, headers=None, timeout=None, **kwargs):
        body = kwargs["json"] if "json" in kwargs else data
        return self.request("PUT", endpoint, json=body, headers=headers, timeout=timeout)

    def patch(self, endpoint, data=None, headers=None, timeout=None, **kwargs):
        body = kwargs["json"] if "json" in kwargs else data
        return self.request("PATCH", endpoint, json=body, headers=headers, timeout=timeout)

    def delete(self, endpoint, data=None, headers=None, timeout=None, **kwargs):
        body = kwargs["json"] if "json" in kwargs else data
        return self.request("DELETE", endpoint, json=body, headers=headers, timeout=timeout)

    def head(self, endpoint, headers=None, timeout=None, **kwargs):
        return self.request("HEAD", endpoint, headers=headers, timeout=timeout, **kwargs)

    def options(self, endpoint, headers=None, timeout=None, **kwargs):
        return self.request("OPTIONS", endpoint, headers=headers, timeout=timeout, **kwargs)

    def _build_url(self, endpoint):
        endpoint = (endpoint or "").strip()
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        if not self.base_url:
            raise ValueError(f"No base URL configured for endpoint {endpoint!r}")
        if not endpoint:
            return self.base_url
        if not endpoint.startswith("/"):
            return f"{self.base_url}/{endpoint}"
        return f"{self.base_url}{endpoint}"


def open_files(file_specs, base_dir):
    opened = {}
    handles = []
    try:
        for spec in file_specs or []:
            path = Path(spec["path"])
            if not path.is_absolute():
                path = Path(base_dir) / path
            handle = path.open("rb")
            handles.append(handle)
            opened[spec["field"]] = (path.name, handle)
        return opened, handles
    except Exception:
        for handle in handles:
            handle.close()
        raise


@contextmanager
def opened_files(file_specs, base_dir):
    """Open FILE uploads for a single HTTP attempt and close them afterwards.

    ``requests`` consumes file objects while building the body, so callers must
    not reuse the same handles across RETRY/WAIT attempts.
    """
    opened, handles = open_files(file_specs, base_dir)
    try:
        yield opened
    finally:
        for handle in handles:
            handle.close()
