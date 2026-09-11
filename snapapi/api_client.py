from __future__ import annotations

import requests


class APIClient:
    """Thin requests wrapper. Pass a body once via ``json=`` — never double-wrap."""

    def __init__(self, base_url=None, timeout=30):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout

    def request(self, method, endpoint, json=None, headers=None, timeout=None):
        url = self._build_url(endpoint)
        kwargs = {
            "headers": headers,
            "timeout": self.timeout if timeout is None else timeout,
        }
        if json is not None:
            kwargs["json"] = json
        return requests.request(method.upper(), url, **kwargs)

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
