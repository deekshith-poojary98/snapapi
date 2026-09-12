from __future__ import annotations

from pathlib import Path

import requests


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
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self.follow_redirects = follow_redirects
        self.verify = verify
        self.cert = cert
        self.proxies = dict(proxies or {})
        self.session = requests.Session()
        self.session.verify = verify
        if cert:
            self.session.cert = cert
        if self.proxies:
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
    for spec in file_specs or []:
        path = Path(spec["path"])
        if not path.is_absolute():
            path = Path(base_dir) / path
        handle = path.open("rb")
        handles.append(handle)
        opened[spec["field"]] = (path.name, handle)
    return opened, handles
