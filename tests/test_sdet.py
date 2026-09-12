import json
import os
from pathlib import Path
from unittest.mock import MagicMock

from snapapi.api_client import APIClient
from snapapi.cassette import cassette_key
from snapapi.cli import main
from snapapi.history import read_last_failed, write_last_run
from snapapi.openapi import generate_smoke
from tests.helpers import parse_dsl, run_dsl


def _suite(server, body, extra=""):
    return f"""
SUITE: Local
URL: {server.base_url}
{extra}
{body}
"""


def test_workers_run_all_example_rows(http_server):
    http_server.on("POST", "/users", status=201, json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Create
EXAMPLES:
  name,email
  Jane,jane@example.com
  Bob,bob@example.com
  POST: /users
  BODY: {"name": "${name}", "email": "${email}"}
  EXPECT: status == 201
""",
        ),
        workers=2,
    )
    assert result.ok
    assert sorted(item.name for item in result.tests) == ["Create [Bob]", "Create [Jane]"]
    emails = sorted(item["json"]["email"] for item in http_server.requests)
    assert emails == ["bob@example.com", "jane@example.com"]


def test_workers_with_default_stop_on_failure(http_server):
    http_server.on("GET", "/a", json={"ok": True})
    http_server.on("GET", "/b", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Alpha
  GET: /a
  EXPECT: status == 200
TEST: Beta
  GET: /b
  EXPECT: status == 200
""",
        ),
        workers=2,
    )
    assert result.ok
    assert result.passed == 2
    assert sorted(item["path"] for item in http_server.requests) == ["/a", "/b"]


def test_workers_stop_scheduling_after_failure(http_server):
    http_server.on("GET", "/fail", status=500, json={"ok": False})
    http_server.on("GET", "/ok", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: First
  GET: /fail
  EXPECT: status == 200
TEST: Second
  GET: /ok
  EXPECT: status == 200
""",
        ),
        workers=1,
        stop_on_failure=True,
    )
    assert not result.ok
    assert [item["path"] for item in http_server.requests] == ["/fail"]


def test_workers_keep_setup_helpers(http_server):
    http_server.on("POST", "/login", json={"token": "abc"})
    http_server.on("GET", "/a", json={"ok": True})
    http_server.on("GET", "/b", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Login
  POST: /login
  EXPECT: status == 200
  SAVE: token FROM $.token
TEST: Alpha
SETUP: Login
  GET: /a
  HEADER Authorization: Bearer ${token}
  EXPECT: status == 200
TEST: Beta
SETUP: Login
  GET: /b
  HEADER Authorization: Bearer ${token}
  EXPECT: status == 200
""",
        ),
        workers=2,
    )
    assert result.ok
    auths = [item["headers"].get("Authorization") for item in http_server.requests if item["path"] != "/login"]
    assert auths == ["Bearer abc", "Bearer abc"]


def test_sibling_save_falls_back_to_sequential(http_server):
    http_server.on("POST", "/users", status=201, json={"id": "42"})
    http_server.on("GET", "/users/42", json={"id": "42"})
    result, _, output = run_dsl(
        _suite(
            http_server,
            """
TEST: Create
  POST: /users
  EXPECT: status == 201
  SAVE: userId FROM $.id
TEST: Fetch
  GET: /users/${userId}
  EXPECT: status == 200
""",
        ),
        workers=2,
    )
    assert result.ok
    assert "share SAVE" in output
    assert [item["path"] for item in http_server.requests] == ["/users", "/users/42"]


def test_api_client_tls_kwargs(monkeypatch):
    captured = {}

    class FakeSession:
        def __init__(self):
            self.verify = True
            self.cert = None
            self.proxies = {}
            captured["session"] = self

        def request(self, *args, **kwargs):
            raise AssertionError("should not send")

    monkeypatch.setattr("snapapi.api_client.requests.Session", FakeSession)
    client = APIClient(
        "https://example.com",
        verify=False,
        cert="/tmp/client.pem",
        proxies={"http": "http://proxy:8080", "https": "http://proxy:8080"},
    )
    session = captured["session"]
    assert session.verify is False
    assert session.cert == "/tmp/client.pem"
    assert session.proxies["https"] == "http://proxy:8080"
    assert client.verify is False


def test_cli_tls_options_helper():
    from types import SimpleNamespace

    from snapapi.cli import _tls_options

    insecure = _tls_options(SimpleNamespace(insecure=True, cacert="/ca.pem", cert="/c.pem", proxy="http://p:1"))
    assert insecure["verify"] is False
    assert insecure["cert"] == "/c.pem"
    assert insecure["proxies"]["https"] == "http://p:1"
    cacert = _tls_options(SimpleNamespace(insecure=False, cacert="/ca.pem", cert="/c.pem", proxy=None))
    assert cacert["verify"] == "/ca.pem"
    assert cacert["proxies"] is None


def test_last_failed_identity_two_files_same_name(http_server, tmp_path):
    http_server.on("GET", "/ok", json={"ok": True})
    http_server.on("GET", "/fail", status=500, json={})
    first = tmp_path / "alpha.snaptest"
    second = tmp_path / "beta.snaptest"
    first.write_text(
        f"""
SUITE: Alpha
URL: {http_server.base_url}
TEST: Boom
  GET: /fail
  EXPECT: status == 200
""",
        encoding="utf-8",
    )
    second.write_text(
        f"""
SUITE: Beta
URL: {http_server.base_url}
TEST: Boom
  GET: /ok
  EXPECT: status == 200
""",
        encoding="utf-8",
    )
    old = Path.cwd()
    try:
        os.chdir(tmp_path)
        assert main([str(first), str(second)]) == 1
        failed = read_last_failed(tmp_path)
        assert len(failed) == 1
        assert failed[0]["name"] == "Boom"
        assert failed[0]["suite"] == "Alpha"
        http_server.requests.clear()
        assert main([str(first), str(second), "--last-failed"]) == 1
        assert [item["path"] for item in http_server.requests] == ["/fail"]
    finally:
        os.chdir(old)


def test_last_failed_example_row_identity(http_server, tmp_path):
    http_server.on("POST", "/ok", status=201, json={"ok": True})
    http_server.on("POST", "/fail", status=500, json={})

    def handler(record):
        payload = record.get("json") or {}
        if payload.get("name") == "Jane":
            return 500, {"Content-Type": "application/json"}, {"ok": False}
        return 201, {"Content-Type": "application/json"}, {"ok": True}

    http_server.on("POST", "/users", handler=handler)
    suite = tmp_path / "examples.snaptest"
    suite.write_text(
        f"""
SUITE: Rows
URL: {http_server.base_url}
TEST: Create
EXAMPLES:
  name,email
  Jane,jane@example.com
  Bob,bob@example.com
  POST: /users
  BODY: {{"name": "${{name}}", "email": "${{email}}"}}
  EXPECT: status == 201
""",
        encoding="utf-8",
    )
    old = Path.cwd()
    try:
        os.chdir(tmp_path)
        assert main([str(suite)]) == 1
        failed = read_last_failed(tmp_path)
        assert failed[0]["name"] == "Create [Jane]"
        http_server.requests.clear()
        assert main([str(suite), "--last-failed"]) == 1
        assert [item["json"]["name"] for item in http_server.requests] == ["Jane"]
    finally:
        os.chdir(old)


def test_write_last_run_stores_identity(tmp_path):
    suite = MagicMock()
    suite.source = str(tmp_path / "a.snaptest")
    suite.name = "Alpha"
    test = MagicMock()
    test.status = "failed"
    test.name = "Boom [row]"
    suite.tests = [test]
    payload = write_last_run([suite], cwd=tmp_path)
    assert payload["failed"] == [
        {"file": str(tmp_path / "a.snaptest"), "suite": "Alpha", "name": "Boom [row]"}
    ]


def test_openapi_emits_non_get_and_params(tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "servers": [{"url": "https://api.example.com"}],
                "paths": {
                    "/users/{id}": {
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "example": 9,
                            }
                        ],
                        "get": {
                            "operationId": "getUser",
                            "parameters": [
                                {"name": "verbose", "in": "query", "required": True, "schema": {"default": "true"}}
                            ],
                            "responses": {"200": {"description": "ok"}},
                        },
                        "delete": {
                            "operationId": "deleteUser",
                            "responses": {"204": {"description": "gone"}},
                        },
                    },
                    "/users": {
                        "post": {
                            "operationId": "createUser",
                            "requestBody": {
                                "content": {
                                    "application/json": {"example": {"name": "Jane"}}
                                }
                            },
                            "responses": {"201": {"description": "created"}},
                        },
                        "put": {
                            "operationId": "replaceUsers",
                            "responses": {"200": {"description": "ok"}},
                        },
                        "patch": {
                            "operationId": "patchUsers",
                            "responses": {"200": {"description": "ok"}},
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    text = generate_smoke(spec)
    assert "GET: /users/9" in text
    assert "QUERY: verbose=true" in text
    assert "POST: /users" in text
    assert 'BODY: {"name": "Jane"}' in text
    assert "PUT: /users" in text
    assert "PATCH: /users" in text
    assert "DELETE: /users/9" in text
    assert "EXPECT: status == 201" in text
    assert "EXPECT: status == 204" in text
    assert "BODY: {}" in text


def test_vcr_query_and_record_on_miss(http_server, tmp_path):
    http_server.on("GET", "/search", json={"q": "ok"})
    cassette_dir = tmp_path / "cassettes"
    suite = tmp_path / "vcr.snaptest"
    suite.write_text(
        f"""
SUITE: VCR
OPTIONS: {{"CASSETTE_DIR": "{cassette_dir.as_posix()}", "MODE": "record"}}
URL: {http_server.base_url}
TEST: One
  GET: /search
  QUERY: q=one
  EXPECT: status == 200
TEST: Two
  GET: /search
  QUERY: q=two
  EXPECT: status == 200
""",
        encoding="utf-8",
    )
    old = Path.cwd()
    try:
        os.chdir(tmp_path)
        assert main([str(suite)]) == 0
        keys = {cassette_key("GET", f"{http_server.base_url}/search?q=one"), cassette_key("GET", f"{http_server.base_url}/search?q=two")}
        files = {item.stem for item in cassette_dir.glob("*.json")}
        assert keys == files
        http_server.requests.clear()
        suite.write_text(
            f"""
SUITE: VCR
OPTIONS: {{"CASSETTE_DIR": "{cassette_dir.as_posix()}"}}
URL: {http_server.base_url}
TEST: One
  GET: /search
  QUERY: q=one
  EXPECT: status == 200
""",
            encoding="utf-8",
        )
        assert main([str(suite), "--mode", "replay"]) == 0
        assert http_server.requests == []
        http_server.on("GET", "/new", json={"fresh": True})
        suite.write_text(
            f"""
SUITE: VCR
OPTIONS: {{"CASSETTE_DIR": "{cassette_dir.as_posix()}"}}
URL: {http_server.base_url}
TEST: Miss
  GET: /new
  EXPECT: status == 200
""",
            encoding="utf-8",
        )
        assert main([str(suite), "--mode", "replay", "--record-on-miss"]) == 0
        assert [item["path"] for item in http_server.requests] == ["/new"]
        assert list(cassette_dir.glob("*.json"))
    finally:
        os.chdir(old)


def test_vcr_restores_set_cookie(http_server, tmp_path):
    def handler(record):
        return 200, {"Set-Cookie": "JSESSIONID=abc; Path=/"}, {"ok": True}

    http_server.on("GET", "/sess", handler=handler)
    cassette_dir = tmp_path / "cassettes"
    suite = tmp_path / "cookie.snaptest"
    suite.write_text(
        f"""
SUITE: Cookie
OPTIONS: {{"CASSETTE_DIR": "{cassette_dir.as_posix()}", "MODE": "record"}}
URL: {http_server.base_url}
TEST: Sess
  GET: /sess
  EXPECT: status == 200
  SAVE: sid FROM cookie JSESSIONID
""",
        encoding="utf-8",
    )
    old = Path.cwd()
    try:
        os.chdir(tmp_path)
        assert main([str(suite)]) == 0
        suite.write_text(
            f"""
SUITE: Cookie
OPTIONS: {{"CASSETTE_DIR": "{cassette_dir.as_posix()}"}}
URL: {http_server.base_url}
TEST: Sess
  GET: /sess
  EXPECT: status == 200
  SAVE: sid FROM cookie JSESSIONID
""",
            encoding="utf-8",
        )
        assert main([str(suite), "--mode", "replay"]) == 0
    finally:
        os.chdir(old)


def test_jsonpath_wildcard_assert_and_html_report(http_server, tmp_path):
    http_server.on("GET", "/items", json={"items": [{"id": 1}, {"id": 2}]})
    suite = tmp_path / "wild.snaptest"
    html = tmp_path / "out.html"
    suite.write_text(
        f"""
SUITE: Wild
URL: {http_server.base_url}
TEST: Items
  GET: /items
  EXPECT: json $.items[*].id == [1, 2]
""",
        encoding="utf-8",
    )
    assert main([str(suite), "--report", f"html:{html}"]) == 0
    text = html.read_text(encoding="utf-8")
    assert "GET" in text
    assert "/items" in text
    assert "response:" in text


def test_wait_and_expect_retry_until_ready(http_server):
    state = {"n": 0}

    def handler(record):
        state["n"] += 1
        if state["n"] < 3:
            return 200, {"Content-Type": "application/json"}, {"status": "pending"}
        return 200, {"Content-Type": "application/json"}, {"status": "ready"}

    http_server.on("GET", "/job", handler=handler)
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Poll
  GET: /job
  WAIT: json $.status == "ready" TIMEOUT 2s BACKOFF 0.01s
  EXPECT: status == 200
""",
        )
    )
    assert result.ok
    assert state["n"] >= 3

    state["n"] = 0
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Retry json
  GET: /job
  EXPECT: status == 200
  EXPECT: json $.status == "ready" RETRY 20 BACKOFF 0.01s
""",
        )
    )
    assert result.ok


def test_oauth_password_and_refresh(http_server):
    def token_handler(record):
        body = record.get("body") or ""
        if "grant_type=refresh_token" in body:
            return 200, {"Content-Type": "application/json"}, {"access_token": "tok-2"}
        assert "grant_type=password" in body
        assert "username=jane" in body
        return 200, {"Content-Type": "application/json"}, {"access_token": "tok-1", "refresh_token": "ref-1"}

    calls = {"n": 0}

    def secure_handler(record):
        calls["n"] += 1
        auth = record["headers"].get("Authorization")
        if auth == "Bearer tok-1":
            return 401, {"Content-Type": "application/json"}, {"error": "expired"}
        assert auth == "Bearer tok-2"
        return 200, {"Content-Type": "application/json"}, {"ok": True}

    http_server.on("POST", "/oauth/token", handler=token_handler)
    http_server.on("GET", "/secure", handler=secure_handler)
    result, _, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: Password
  GET: /secure
  AUTH: oauth2 grant=password token_url={http_server.base_url}/oauth/token client_id=id username=jane password=secret
  EXPECT: status == 200
""",
        )
    )
    assert result.ok
    assert calls["n"] == 2


def test_history_cli(tmp_path, capsys):
    old = Path.cwd()
    try:
        os.chdir(tmp_path)
        history = tmp_path / ".snapapi"
        history.mkdir()
        (history / "history.jsonl").write_text(
            json.dumps(
                {
                    "time": 1700000000,
                    "suite": "Local",
                    "name": "Boom",
                    "status": "failed",
                    "duration_ms": 12,
                }
            )
            + "\n"
            + json.dumps(
                {
                    "time": 1700000001,
                    "suite": "Local",
                    "name": "Ok",
                    "status": "passed",
                    "duration_ms": 4,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert main(["history", "--failed"]) == 0
        out = capsys.readouterr().out
        assert "Boom" in out
        assert "Ok" not in out
        assert "failed" in out
        assert main(["history", "--since", "7d"]) == 0
    finally:
        os.chdir(old)


def test_parse_wait():
    suite = parse_dsl(
        """
SUITE: Demo
TEST: A
  GET: /x
  WAIT: json $.status == "ready" TIMEOUT 10s BACKOFF 0.5s
  EXPECT: status == 200
"""
    )
    wait = suite["tests"][0]["steps"][0]["wait"]
    assert wait["timeout"] == 10
    assert wait["backoff"] == 0.5
    assert wait["check"]["path"] == "$.status"


def test_set_assigns_without_http(http_server):
    http_server.on("GET", "/echo", json={"ok": True})
    result, engine, _ = run_dsl(
        _suite(
            http_server,
            """
SET: marker hello
TEST: Echo
  SET: orderId ${uuid()}
  GET: /echo
  QUERY: id=${orderId}
  EXPECT: status == 200
  EXPECT: json $.ok == true
""",
        )
    )
    assert result.ok
    assert engine.variables["marker"] == "hello"
    assert engine.variables["orderId"]
    assert "id=" in http_server.requests[0]["query"]


def test_mock_server_from_json(tmp_path):
    import requests as http

    from snapapi.mock import MockServer, load_mock_routes

    spec = tmp_path / "mock.json"
    spec.write_text('{"routes":[{"method":"GET","path":"/ping","status":200,"json":{"ok":true}}]}', encoding="utf-8")
    with MockServer(load_mock_routes(spec), port=0) as server:
        assert server.url.startswith("http://")
        response = http.get(f"{server.url}/ping", timeout=2)
        assert response.status_code == 200
        assert response.json() == {"ok": True}


def test_watch_snapshot_mtimes(tmp_path):
    import os

    from snapapi.watch import snapshot_mtimes

    path = tmp_path / "a.snaptest"
    path.write_text("SUITE: X\n", encoding="utf-8")
    previous, changed = snapshot_mtimes([path])
    assert changed == []
    key = str(path.resolve())
    mtime_ns, size = previous[key]
    os.utime(path, (mtime_ns / 1e9 + 5, mtime_ns / 1e9 + 5))
    _, changed = snapshot_mtimes([path], previous)
    assert changed


def test_openapi_contract_options_and_expect(http_server, tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "paths": {
                    "/ping": {
                        "get": {
                            "responses": {
                                "200": {
                                    "content": {
                                        "application/json": {
                                            "schema": {
                                                "type": "object",
                                                "required": ["ok"],
                                                "properties": {"ok": {"type": "boolean"}},
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    http_server.on("GET", "/ping", json={"ok": True})
    result, _, _ = run_dsl(
        f"""
SUITE: Contract
OPTIONS: {{"OPENAPI": "{spec.as_posix()}"}}
URL: {http_server.base_url}
TEST: Ping
  GET: /ping
  EXPECT: status == 200
"""
    )
    assert result.ok
    http_server.on("GET", "/ping", json={"nope": 1})
    result, _, _ = run_dsl(
        f"""
SUITE: Contract
OPTIONS: {{"OPENAPI": "{spec.as_posix()}"}}
URL: {http_server.base_url}
TEST: Ping
  GET: /ping
  EXPECT: status == 200
"""
    )
    assert not result.ok
    http_server.on("GET", "/ping", json={"ok": True})
    result, _, _ = run_dsl(
        f"""
SUITE: Contract
URL: {http_server.base_url}
TEST: Ping
  GET: /ping
  EXPECT: openapi {spec.as_posix()}
"""
    )
    assert result.ok
    http_server.on("GET", "/bad", json={"nope": 1})
    result, _, _ = run_dsl(
        f"""
SUITE: Contract
URL: {http_server.base_url}
TEST: Bad
  GET: /bad
  EXPECT: openapi {spec.as_posix()}
"""
    )
    # /bad is not in the spec; missing schema is a skip, not a failure
    assert result.ok


def test_offline_replay_fixture():
    root = Path(__file__).resolve().parents[1]
    suite = root / "tests" / "fixtures" / "offline.snaptest"
    old = Path.cwd()
    try:
        os.chdir(root)
        assert main(["run", str(suite), "--mode", "replay"]) == 0
    finally:
        os.chdir(old)


