from snapapi.api_client import APIClient


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
