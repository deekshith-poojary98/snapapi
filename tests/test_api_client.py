from pathlib import Path

import pytest

from snapapi.api_client import APIClient, open_files, opened_files


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
