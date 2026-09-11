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
    assert '{"STOP-ON-FAILURE": false}' in output


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
    result, _, _ = run_dsl(
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
    assert "ms" in output
    assert "Duration:" in output


def test_undefined_variable_fails(http_server):
    http_server.on("GET", "/x", json={"ok": True})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Vars
  REQUEST: GET /${missing}
  EXPECT: STATUS 200
"""))
    assert not result.ok
    assert "Undefined variable" in (result.tests[0].error or "")
