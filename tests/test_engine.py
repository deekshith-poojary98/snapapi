import pytest

from snapapi.exceptions import SnapAPIError
from tests.helpers import parse_dsl, run_dsl


def _suite(server, body):
    return f"""
SUITE: Local
URL: {server.base_url}
{body}
"""


def test_get_status_pass(http_server):
    http_server.on("GET", "/api/users", json={"data": [{"id": 1}]})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: List
  REQUEST: GET /api/users
  EXPECT: STATUS 200
  EXPECT: CONTAINS data
"""))
    assert result.ok
    assert result.passed == 1
    assert result.tests[0].requests[0].status_code == 200
    assert result.tests[0].requests[0].duration_ms >= 0


def test_get_status_fail(http_server):
    http_server.on("GET", "/api/users", status=500, json={"error": "nope"})
    result, engine, output = run_dsl(_suite(http_server, """
TEST: List
  REQUEST: GET /api/users
  EXPECT: STATUS 200
"""))
    assert not result.ok
    assert result.failed == 1
    assert "Status code expected 200, got 500" in (result.tests[0].error or "")
    assert engine.failures


def test_contains_fail(http_server):
    http_server.on("GET", "/ping", text="pong")
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Ping
  REQUEST: GET /ping
  EXPECT: CONTAINS hello
"""))
    assert not result.ok
    assert "does not contain hello" in (result.tests[0].error or "")


def test_post_put_patch_delete(http_server):
    http_server.on("POST", "/api/users", status=201, json={"id": "9", "name": "Jane"})
    http_server.on("PUT", "/api/users/9", json={"id": "9", "name": "Janet"})
    http_server.on("PATCH", "/api/users/9", json={"id": "9", "job": "lead"})
    http_server.on("DELETE", "/api/users/9", status=204, text="")
    result, _, _ = run_dsl(_suite(http_server, """
TEST: CRUD
  REQUEST: POST /api/users
  DATA: {"name": "Jane"}
  EXPECT: STATUS 201
  REQUEST: PUT /api/users/9
  DATA: {"name": "Janet"}
  EXPECT: STATUS 200
  REQUEST: PATCH /api/users/9
  DATA: {"job": "lead"}
  EXPECT: STATUS 200
  REQUEST: DELETE /api/users/9
  EXPECT: STATUS 204
"""))
    assert result.ok
    methods = [item["method"] for item in http_server.requests]
    assert methods == ["POST", "PUT", "PATCH", "DELETE"]
    assert http_server.requests[0]["json"] == {"name": "Jane"}


def test_headers_sent_on_request(http_server):
    http_server.on("GET", "/api/me", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(http_server, """
TEST: Me
  REQUEST: GET /api/me
  HEADERS: {"Authorization": "Bearer ${TOKEN}", "X-Test": "1"}
  EXPECT: STATUS 200
"""),
        variables={"TOKEN": "secret"},
    )
    assert result.ok
    headers = http_server.requests[0]["headers"]
    assert headers.get("Authorization") == "Bearer secret"
    assert headers.get("X-Test") == "1"


def test_json_and_header_assertions(http_server):
    http_server.on(
        "GET",
        "/api/users/2",
        json={"data": {"email": "jane@example.com", "id": 2}},
        headers={"Content-Type": "application/json", "X-Trace": "abc"},
    )
    result, _, _ = run_dsl(_suite(http_server, """
TEST: User
  REQUEST: GET /api/users/2
  EXPECT: JSON $.data.email == "jane@example.com"
  EXPECT: JSON $.data.id == 2
  EXPECT: HEADER Content-Type CONTAINS json
  EXPECT: HEADER X-Trace == abc
"""))
    assert result.ok


def test_json_assertion_fail(http_server):
    http_server.on("GET", "/api/users/2", json={"data": {"email": "other@example.com"}})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: User
  REQUEST: GET /api/users/2
  EXPECT: JSON $.data.email == "jane@example.com"
"""))
    assert not result.ok
    assert "jane@example.com" in (result.tests[0].error or "")


def test_save_and_interpolation_across_tests(http_server):
    http_server.on("POST", "/api/users", status=201, json={"data": {"id": "42", "email": "jane@example.com"}})
    http_server.on("GET", "/api/users/42", json={"data": {"id": "42", "email": "jane@example.com"}})
    result, engine, _ = run_dsl(_suite(http_server, """
TEST: Create
  REQUEST: POST /api/users
  DATA: {"email": "jane@example.com"}
  EXPECT: STATUS 201
  SAVE: userId FROM $.data.id
  SAVE: email FROM $.data.email

TEST: Fetch
  REQUEST: GET /api/users/${userId}
  EXPECT: STATUS 200
  EXPECT: JSON $.data.email == "${email}"
"""))
    assert result.ok
    assert engine.variables["userId"] == "42"
    assert http_server.requests[1]["path"] == "/api/users/42"


def test_setup_teardown_run_order(http_server):
    http_server.on("POST", "/log", status=200, json={"ok": True})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: SetupA
  REQUEST: POST /log
  DATA: {"event": "setupA"}
  EXPECT: STATUS 200

TEST: SetupB
SETUP: SetupA
  REQUEST: POST /log
  DATA: {"event": "setupB"}
  EXPECT: STATUS 200

TEST: Tear
  REQUEST: POST /log
  DATA: {"event": "tear"}
  EXPECT: STATUS 200

TEST: Main
SETUP: SetupB
TEARDOWN: Tear
  REQUEST: POST /log
  DATA: {"event": "main"}
  EXPECT: STATUS 200
"""))
    assert result.ok
    assert [item["json"]["event"] for item in http_server.requests] == ["setupA", "setupB", "main", "tear"]
    assert [test.name for test in result.tests] == ["Main"]
    assert result.total == 1


def test_stop_on_failure_true_halts(http_server):
    http_server.on("GET", "/ok", json={"ok": True})
    http_server.on("GET", "/fail", status=500, json={"ok": False})
    result, _, output = run_dsl(_suite(http_server, """
OPTIONS: {"STOP-ON-FAILURE": true}
TEST: First
  REQUEST: GET /fail
  EXPECT: STATUS 200
TEST: Second
  REQUEST: GET /ok
  EXPECT: STATUS 200
"""))
    assert not result.ok
    assert result.failed == 1
    assert result.passed == 0
    assert [item["path"] for item in http_server.requests] == ["/fail"]
    assert "STOP-ON-FAILURE: false" in output


def test_stop_on_failure_false_continues(http_server):
    http_server.on("GET", "/ok", json={"ok": True})
    http_server.on("GET", "/fail", status=500, json={"ok": False})
    result, _, _ = run_dsl(_suite(http_server, """
OPTIONS: {"STOP-ON-FAILURE": false}
TEST: First
  REQUEST: GET /fail
  EXPECT: STATUS 200
TEST: Second
  REQUEST: GET /ok
  EXPECT: STATUS 200
"""))
    assert not result.ok
    assert result.failed == 1
    assert result.passed == 1
    assert [item["path"] for item in http_server.requests] == ["/fail", "/ok"]


def test_tag_filter(http_server):
    http_server.on("GET", "/users", json={"ok": True})
    http_server.on("GET", "/health", json={"ok": True})
    result, _, output = run_dsl(
        _suite(http_server, """
TEST: Users
TAG: user
  REQUEST: GET /users
  EXPECT: STATUS 200
TEST: Health
TAG: health
  REQUEST: GET /health
  EXPECT: STATUS 200
"""),
        tags=["user"],
    )
    assert result.ok
    assert result.passed == 1
    assert result.skipped == 1
    assert [item["path"] for item in http_server.requests] == ["/users"]
    assert "1 passed" in output
    assert "1 skipped" in output


def test_tag_filter_still_runs_untagged_setup(http_server):
    http_server.on("POST", "/setup", json={"ok": True})
    http_server.on("GET", "/users", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(http_server, """
TEST: Boot
  REQUEST: POST /setup
  EXPECT: STATUS 200
TEST: Users
TAG: user
SETUP: Boot
  REQUEST: GET /users
  EXPECT: STATUS 200
TEST: Other
TAG: other
  REQUEST: GET /users
  EXPECT: STATUS 200
"""),
        tags=["user"],
    )
    assert result.ok
    assert [item["path"] for item in http_server.requests] == ["/setup", "/users"]


def test_name_filter(http_server):
    http_server.on("GET", "/users", json={"ok": True})
    http_server.on("GET", "/health", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(http_server, """
TEST: Users
  REQUEST: GET /users
  EXPECT: STATUS 200
TEST: Health
  REQUEST: GET /health
  EXPECT: STATUS 200
"""),
        names=["Users"],
    )
    assert result.ok
    assert result.passed == 1
    assert result.skipped == 0
    assert [item["path"] for item in http_server.requests] == ["/users"]


def test_name_filter_runs_named_helper(http_server):
    http_server.on("POST", "/setup", json={"ok": True})
    http_server.on("GET", "/users", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(http_server, """
TEST: Boot
  REQUEST: POST /setup
  EXPECT: STATUS 200
TEST: Users
SETUP: Boot
  REQUEST: GET /users
  EXPECT: STATUS 200
"""),
        names=["Boot"],
    )
    assert result.ok
    assert [item["path"] for item in http_server.requests] == ["/setup"]


def test_name_filter_still_runs_setup(http_server):
    http_server.on("POST", "/setup", json={"ok": True})
    http_server.on("GET", "/users", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(http_server, """
TEST: Boot
  REQUEST: POST /setup
  EXPECT: STATUS 200
TEST: Users
SETUP: Boot
  REQUEST: GET /users
  EXPECT: STATUS 200
"""),
        names=["Users"],
    )
    assert result.ok
    assert [item["path"] for item in http_server.requests] == ["/setup", "/users"]


def test_name_filter_unknown_raises(http_server):
    with pytest.raises(SnapAPIError, match="Unknown test name: Missing"):
        run_dsl(
            _suite(http_server, """
TEST: Users
  REQUEST: GET /users
  EXPECT: STATUS 200
"""),
            names=["Missing"],
        )


def test_retry_until_success(http_server):
    http_server.on("GET", "/flaky", json={"ok": True}, fail_times=2)
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Flaky
  REQUEST: GET /flaky
  EXPECT: STATUS 200 RETRY 5
"""))
    assert result.ok
    assert len(http_server.requests) == 3


def test_retry_exhausted_fails(http_server):
    http_server.on("GET", "/flaky", json={"ok": True}, fail_times=10)
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Flaky
  REQUEST: GET /flaky
  EXPECT: STATUS 200 RETRY 3
"""))
    assert not result.ok
    assert len(http_server.requests) == 3


def test_duration_printed(http_server):
    http_server.on("GET", "/ping", json={"ok": True})
    result, _, output = run_dsl(_suite(http_server, """
TEST: Ping
  REQUEST: GET /ping
  EXPECT: STATUS 200
"""))
    assert result.ok
    assert "PASS" in output
    assert "1 passed" in output
    assert "0 failed" in output
    assert "ms" in output or "s" in output


def test_console_output_compact_tree(http_server):
    http_server.on("POST", "/users", status=201, json={"id": "7"})
    http_server.on("GET", "/users/7", json={"id": "7"})
    http_server.on("DELETE", "/users/7", status=204, text="")
    result, _, output = run_dsl(
        _suite(
            http_server,
            """
DESC: Example tests against local server
TEST: Create User
DESC: Should not appear in compact output
TAG: users, write
  REQUEST: POST /users
  DATA: {"name": "Jane"}
  EXPECT: STATUS 201
  SAVE: userId FROM $.id
TEST: Cleanup User
DESC: teardown helper should not dump description
  REQUEST: DELETE /users/${userId}
  EXPECT: STATUS 204
TEST: Fetch User
TAG: users
SETUP: Create User
TEARDOWN: Cleanup User
  REQUEST: GET /users/${userId}
  EXPECT: STATUS 200
""",
        )
    )
    assert result.ok
    assert "SnapAPI  Local" in output
    assert "Example tests against local server" in output
    assert "Fetch User  [users]" in output
    assert "setup Create User" in output
    assert "teardown Cleanup User" in output
    assert "saved userId=7" in output
    assert "POST /users" in output
    assert "GET /users/7" in output
    assert "DELETE /users/7" in output
    assert "PASS" in output
    assert "1 passed" in output
    assert "0 failed" in output
    assert "Should not appear in compact output" not in output
    assert "teardown helper should not dump description" not in output
    assert "[users, write]" not in output
    assert "Running test" not in output
    assert "Test description:" not in output
    assert "Test tag:" not in output
    assert "Test status:" not in output
    assert "Setup status:" not in output
    assert "Setup description:" not in output
    assert "✔" not in output
    assert "SnapAPI Running" not in output
    assert "- - -" not in output


def test_console_failure_reason_under_request(http_server):
    http_server.on("GET", "/api/users", status=500, json={"error": "nope"})
    result, _, output = run_dsl(
        _suite(
            http_server,
            """
TEST: List
  REQUEST: GET /api/users
  EXPECT: STATUS 200
""",
        )
    )
    assert not result.ok
    lines = output.splitlines()
    request_line = next(line for line in lines if "GET /api/users" in line)
    reason_line = next(line for line in lines if "Status code expected 200, got 500" in line)
    assert len(reason_line) - len(reason_line.lstrip(" ")) > len(request_line) - len(request_line.lstrip(" "))
    assert "FAIL" in output
    assert "Failed:" in output
    assert "- List:" in output
    assert "0 passed" in output
    assert "1 failed" in output
    assert "Test status:" not in output
    assert "Reason:" not in output


def test_undefined_variable_fails(http_server):
    http_server.on("GET", "/x", json={"ok": True})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Vars
  REQUEST: GET /${missing}
  EXPECT: STATUS 200
"""))
    assert not result.ok
    assert "Undefined variable" in (result.tests[0].error or "")


def test_new_syntax_request_body_auth_query_and_expect(http_server):
    http_server.on(
        "POST",
        "/users",
        status=201,
        json={"id": "7", "email": "jane@example.com"},
        headers={"Content-Type": "application/json"},
    )
    http_server.on(
        "GET",
        "/users/7",
        json={"id": "7", "email": "jane@example.com"},
        headers={"Content-Type": "application/json"},
    )
    http_server.on("GET", "/users", json={"data": []})
    result, engine, _ = run_dsl(
        _suite(
            http_server,
            """
TIMEOUT: 5
STOP-ON-FAILURE: false
HEADER Content-Type: application/json
TEST: Create
  POST: /users
  AUTH: bearer ${TOKEN}
  BODY: {"name": "Jane", "email": "jane@example.com"}
  EXPECT: status == 201
  EXPECT: body contains id
  SAVE: userId FROM $.id
TEST: Fetch
SETUP: Create
  GET: /users/${userId}
  EXPECT: status == 200
  EXPECT: json $.email == "jane@example.com"
  EXPECT: header Content-Type contains json
TEST: List
  GET: /users
  QUERY: page=2&limit=10
  PARAM: sort name
  EXPECT: status == 200
""",
        ),
        variables={"TOKEN": "secret"},
    )
    assert result.ok
    assert engine.variables["userId"] == "7"
    create_req, fetch_req, list_req = http_server.requests
    assert create_req["method"] == "POST"
    assert create_req["json"] == {"name": "Jane", "email": "jane@example.com"}
    assert create_req["headers"].get("Authorization") == "Bearer secret"
    assert fetch_req["path"] == "/users/7"
    assert list_req["path"] == "/users"
    assert "page=2" in list_req["query"]
    assert "limit=10" in list_req["query"]
    assert "sort=name" in list_req["query"]


def test_query_overrides_path_query_string(http_server):
    http_server.on("GET", "/items", json={"ok": True})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Items
  GET: /items?page=1
  PARAM: page 2
  EXPECT: status == 200
"""))
    assert result.ok
    assert http_server.requests[0]["query"] == "page=2"


def test_first_class_timeout_is_used(http_server):
    http_server.on("GET", "/ping", json={"ok": True})
    result, engine, _ = run_dsl(_suite(http_server, """
TIMEOUT: 2.5
TEST: Ping
  GET: /ping
  EXPECT: status == 200
"""))
    assert result.ok
    assert engine.timeout == 2.5


def test_recommended_syntax_file(http_server):
    from pathlib import Path

    from snapapi.engine import Engine
    from snapapi.parser import TestParser
    import io

    http_server.on(
        "POST",
        "/users",
        status=201,
        json={"id": "7", "email": "jane@example.com"},
        headers={"Content-Type": "application/json"},
    )
    http_server.on(
        "GET",
        "/users/7",
        json={"id": "7", "email": "jane@example.com"},
        headers={"Content-Type": "application/json"},
    )
    http_server.on("GET", "/users", json={"data": []})
    http_server.on("GET", "/health", json={"ok": True})

    suite = TestParser().parse(Path(__file__).parent / "recommended.snaptest")
    stream = io.StringIO()
    engine = Engine(
        suite,
        variables={"BASE_URL": http_server.base_url, "TOKEN": "secret"},
        retry_backoff=0,
        stream=stream,
    )
    result = engine.run()
    assert result.ok
    assert result.passed == 3
    paths = [item["path"] for item in http_server.requests]
    assert paths[0] == "/users"
    assert http_server.requests[0]["method"] == "POST"
    assert http_server.requests[0]["headers"].get("Authorization") == "Bearer secret"
    assert "/users/7" in paths
    list_req = next(item for item in http_server.requests if item["path"] == "/users" and item["method"] == "GET")
    assert "page=2" in list_req["query"]
    assert "sort=name" in list_req["query"]
    assert "/health" in paths
