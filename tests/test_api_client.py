from pathlib import Path

import pytest
import requests

from snapapi.api_client import APIClient, open_files, opened_files
from snapapi.exceptions import SnapAPIError
from snapapi.safety import assert_public_url
from tests.helpers import PUBLIC_TEST_HOST, stub_dns_rebinding, stub_public_host_http


def test_post_sends_body_once(http_server):
    http_server.on("POST", "/echo", json={"ok": True})
    client = APIClient(http_server.base_url, timeout=2)
    response = client.post("/echo", data={"name": "Jane"})
    assert response.status_code == 200
    assert http_server.requests[0]["json"] == {"name": "Jane"}

    response = client.post("/echo", json={"name": "John"})
    assert response.status_code == 200
    assert http_server.requests[1]["json"] == {"name": "John"}


def test_get_put_patch_delete(http_server):
    http_server.on("GET", "/items", json={"n": 1})
    http_server.on("PUT", "/items/1", json={"n": 2})
    http_server.on("PATCH", "/items/1", json={"n": 3})
    http_server.on("DELETE", "/items/1", status=204, text="")
    client = APIClient(http_server.base_url, timeout=2)
    assert client.get("/items").json() == {"n": 1}
    assert client.put("/items/1", data={"n": 2}).json() == {"n": 2}
    assert client.patch("/items/1", data={"n": 3}).json() == {"n": 3}
    assert client.delete("/items/1").status_code == 204


def test_head_and_options(http_server):
    http_server.on("HEAD", "/x", status=204, text="")
    http_server.on("OPTIONS", "/cors", status=204, text="")
    client = APIClient(http_server.base_url, timeout=2)
    assert client.head("/x").status_code == 204
    assert client.options("/cors").status_code == 204


def test_opened_files_closes_handles(tmp_path):
    path = tmp_path / "avatar.bin"
    path.write_bytes(b"avatar-bytes")
    specs = [{"field": "avatar", "path": str(path)}]
    with opened_files(specs, tmp_path) as files:
        handle = files["avatar"][1]
        assert files["avatar"][0] == "avatar.bin"
        assert handle.read() == b"avatar-bytes"
        assert not handle.closed
    assert handle.closed


def test_opened_files_closes_handles_on_error(tmp_path):
    path = tmp_path / "avatar.bin"
    path.write_bytes(b"avatar-bytes")
    specs = [{"field": "avatar", "path": str(path)}]
    handle = None
    with pytest.raises(RuntimeError, match="boom"):
        with opened_files(specs, tmp_path) as files:
            handle = files["avatar"][1]
            raise RuntimeError("boom")
    assert handle is not None and handle.closed


def test_follows_redirects_on_same_origin_without_safe_url(http_server):
    http_server.on("GET", "/from", status=302, headers={"Location": "/to"}, text="")
    http_server.on("GET", "/to", json={"ok": True})
    client = APIClient(http_server.base_url, timeout=2)
    response = client.get("/from")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert [item["path"] for item in http_server.requests] == ["/from", "/to"]


def test_safe_url_blocks_loopback_before_http(http_server):
    http_server.on("GET", "/secret", json={"ok": True})
    client = APIClient(http_server.base_url, timeout=2, safe_url=True)
    with pytest.raises(SnapAPIError, match="Blocked"):
        client.get("/secret")
    assert http_server.requests == []


def test_safe_url_disabled_still_reaches_loopback(http_server):
    http_server.on("GET", "/secret", json={"ok": True})
    client = APIClient(http_server.base_url, timeout=2, safe_url=False)
    assert client.get("/secret").json() == {"ok": True}
    assert len(http_server.requests) == 1


def test_safe_url_blocks_redirect_to_loopback(http_server, monkeypatch):
    http_server.on("GET", "/secret", json={"pwned": True})
    sent = stub_public_host_http(
        monkeypatch,
        {"/start": (302, {"Location": f"{http_server.base_url}/secret"}, b"")},
    )
    client = APIClient("http://example.com", timeout=2, safe_url=True)
    with pytest.raises(SnapAPIError, match="Blocked"):
        client.get("/start")
    assert http_server.requests == []
    assert sent == ["http://example.com/start"]
    assert all("127.0.0.1" not in url for url in sent)


def test_safe_url_allows_redirect_between_public_urls(monkeypatch):
    sent = stub_public_host_http(
        monkeypatch,
        {
            "/from": (302, {"Location": "http://example.com/to"}, b""),
            "/to": (200, {"Content-Type": "application/json"}, b'{"ok": true}'),
        },
    )
    client = APIClient("http://example.com", timeout=2, safe_url=True)
    response = client.get("/from")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert sent == ["http://example.com/from", "http://example.com/to"]


def test_safe_url_blocks_dns_rebinding_before_connect(http_server, monkeypatch):
    http_server.on("GET", "/secret", json={"pwned": True})
    host = "rebinder.test"
    stub_dns_rebinding(monkeypatch, host, connect_ip="127.0.0.1")
    client = APIClient(f"http://{host}:{http_server.port}", timeout=2, safe_url=True)
    with pytest.raises(SnapAPIError, match="Blocked"):
        client.get("/secret")
    assert http_server.requests == []


def test_safe_url_blocks_env_http_proxy(http_server, monkeypatch):
    http_server.on("GET", "/start", json={"via-proxy": True})
    monkeypatch.setenv("HTTP_PROXY", http_server.base_url)
    monkeypatch.setenv("http_proxy", http_server.base_url)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    stub_public_host_http(monkeypatch, {"/start": (200, {"Content-Type": "application/json"}, b'{"ok": true}')})
    client = APIClient(f"http://{PUBLIC_TEST_HOST}", timeout=2, safe_url=True)
    response = client.get("/start")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert http_server.requests == [], "env HTTP_PROXY must not receive traffic under safe_url"


def test_safe_url_blocks_explicit_loopback_proxy(http_server):
    http_server.on("GET", "/start", json={"via-proxy": True})
    with pytest.raises(SnapAPIError, match="Blocked"):
        APIClient(
            f"http://{PUBLIC_TEST_HOST}",
            timeout=2,
            safe_url=True,
            proxies={"http": http_server.base_url, "https": http_server.base_url},
        )
    assert http_server.requests == []


def test_explicit_loopback_proxy_still_allowed_without_safe_url(http_server):
    """safe_url=False must not reject private proxies at construction time."""
    client = APIClient(
        http_server.base_url,
        timeout=2,
        safe_url=False,
        proxies={"http": http_server.base_url, "https": http_server.base_url},
    )
    assert client.session.trust_env is True
    assert client.session.proxies["http"] == http_server.base_url

    http_server.on("GET", "/direct", json={"ok": True})
    direct = APIClient(http_server.base_url, timeout=2, safe_url=False)
    assert direct.session.trust_env is True
    assert direct.get("/direct").json() == {"ok": True}
    assert len(http_server.requests) == 1


def test_safe_session_disables_trust_env():
    client = APIClient("http://example.com", timeout=2, safe_url=True)
    assert client.session.trust_env is False



def test_safe_url_blocks_cgnat_before_connect(monkeypatch):
    with pytest.raises(SnapAPIError, match="Blocked"):
        assert_public_url("http://100.100.100.200/")

    def fail_send(*args, **kwargs):
        raise AssertionError("HTTP send must not run for CGNAT destinations")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fail_send)
    client = APIClient("http://100.100.100.200", timeout=2, safe_url=True)
    with pytest.raises(SnapAPIError, match="Blocked"):
        client.get("/")


def test_open_files_closes_already_opened_handles_on_partial_failure(tmp_path, monkeypatch):
    existing = tmp_path / "ok.bin"
    existing.write_bytes(b"ok")
    specs = [
        {"field": "ok", "path": str(existing)},
        {"field": "missing", "path": str(tmp_path / "missing.bin")},
    ]
    opened = []
    original_open = Path.open

    def tracking_open(self, *args, **kwargs):
        handle = original_open(self, *args, **kwargs)
        opened.append(handle)
        return handle

    monkeypatch.setattr(Path, "open", tracking_open)
    with pytest.raises(FileNotFoundError):
        open_files(specs, tmp_path)
    assert opened
    assert all(handle.closed for handle in opened)
