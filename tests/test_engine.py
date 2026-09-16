import io
from types import SimpleNamespace

import pytest

import requests

from snapapi.engine import Engine
from snapapi.exceptions import SnapAPIError
from tests.helpers import parse_dsl, run_dsl, stub_dns_rebinding, stub_public_host_http


def _suite(server, body):
    return f"""
SUITE: Local
URL: {server.base_url}
{body}
"""


def _assert_each_upload_contains(http_server, *payloads):
    assert http_server.requests, "expected uploaded requests"
    for index, request in enumerate(http_server.requests, start=1):
        raw = request.get("raw") or b""
        body = request.get("body") or ""
        assert raw, f"attempt {index} sent an empty body"
        for payload in payloads:
            assert payload in raw, (
                f"attempt {index} did not upload complete file bytes {payload!r}; raw={raw!r} body={body!r}"
            )


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


def test_expect_or_and_grouping(http_server):
    http_server.on("POST", "/login", status=401, json={"success": False, "error": "bad credentials"})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Login
  POST: /login
  EXPECT: status == 400 OR status == 401
  EXPECT: json $.success == false AND body contains error
  EXPECT: (status == 400 OR status == 401) AND json $.success == false
"""))
    assert result.ok


def test_expect_or_all_fail(http_server):
    http_server.on("GET", "/x", status=200, json={"ok": True})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Status
  GET: /x
  EXPECT: status == 400 OR status == 401
"""))
    assert not result.ok
    assert "OR expected at least one check to pass" in (result.tests[0].error or "")


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


def test_helper_suite_setup_is_not_a_primary(http_server):
    http_server.on("POST", "/login", json={"token": "abc"})
    http_server.on("GET", "/me", json={"ok": True})
    result, _, output = run_dsl(
        _suite(
            http_server,
            """
HELPER: Authenticate
  POST: /login
  EXPECT: status == 200
  SAVE: token FROM $.token
SUITE-SETUP: Authenticate
TEST: Me
  GET: /me
  HEADER Authorization: Bearer ${token}
  EXPECT: status == 200
""",
        )
    )
    assert result.ok
    assert [test.name for test in result.tests] == ["Me"]
    assert "Authenticate" not in [test.name for test in result.tests]
    assert [item["path"] for item in http_server.requests] == ["/login", "/me"]
    assert "setup Authenticate" in output


def test_suite_setup_failure_is_not_a_failed_test(http_server):
    http_server.on("POST", "/login", status=401, json={"ok": False})
    http_server.on("GET", "/me", json={"ok": True})
    result, _, output = run_dsl(
        _suite(
            http_server,
            """
HELPER: Authenticate
  POST: /login
  EXPECT: status == 200
SUITE-SETUP: Authenticate
TEST: Me
  GET: /me
  EXPECT: status == 200
TEST: Profile
  GET: /me
  EXPECT: status == 200
""",
        )
    )
    assert not result.ok
    assert result.failed == 0
    assert result.passed == 0
    assert result.skipped == 2
    assert [test.name for test in result.tests] == ["Me", "Profile"]
    assert all(test.status == "skipped" for test in result.tests)
    assert "suite setup 'Authenticate' failed" in result.tests[0].error
    assert result.error_name == "SUITE-SETUP (Authenticate)"
    assert "Status code expected 200, got 401" in (result.error or "")
    assert result.total == 2
    assert "0 passed" in output
    assert "0 failed" in output
    assert "2 skipped" in output
    assert "SUITE-SETUP (Authenticate)" in output
    assert [item["path"] for item in http_server.requests] == ["/login"]


def test_suite_teardown_failure_fails_suite(http_server):
    http_server.on("GET", "/ok", json={"ok": True})
    http_server.on("POST", "/cleanup", status=500, json={"ok": False})
    result, _, output = run_dsl(
        _suite(
            http_server,
            """
HELPER: Cleanup
  POST: /cleanup
  EXPECT: status == 200
SUITE-TEARDOWN: Cleanup
TEST: Ok
  GET: /ok
  EXPECT: status == 200
""",
        )
    )
    assert result.passed == 1
    assert result.failed == 0
    assert not result.ok
    assert result.error_name == "SUITE-TEARDOWN (Cleanup)"
    assert "Status code expected 200, got 500" in (result.error or "")
    assert [item["path"] for item in http_server.requests] == ["/ok", "/cleanup"]
    assert "SUITE-TEARDOWN (Cleanup)" in output


def test_suite_teardown_runs_after_failed_test(http_server):
    http_server.on("GET", "/boom", status=500, json={})
    http_server.on("POST", "/cleanup", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
HELPER: Cleanup
  POST: /cleanup
  EXPECT: status == 200
SUITE-TEARDOWN: Cleanup
TEST: Boom
  GET: /boom
  EXPECT: status == 200
""",
        )
    )
    assert not result.ok
    assert result.failed == 1
    assert result.error is None
    assert [item["path"] for item in http_server.requests] == ["/boom", "/cleanup"]


def test_suite_teardown_failure_with_failed_test_still_reported(http_server):
    http_server.on("GET", "/boom", status=500, json={})
    http_server.on("POST", "/cleanup", status=500, json={})
    result, _, output = run_dsl(
        _suite(
            http_server,
            """
HELPER: Cleanup
  POST: /cleanup
  EXPECT: status == 200
SUITE-TEARDOWN: Cleanup
TEST: Boom
  GET: /boom
  EXPECT: status == 200
""",
        )
    )
    assert not result.ok
    assert result.failed == 1
    assert result.error_name == "SUITE-TEARDOWN (Cleanup)"
    assert "SUITE-TEARDOWN (Cleanup)" in output
    assert [item["path"] for item in http_server.requests] == ["/boom", "/cleanup"]


def test_suite_teardown_runs_after_suite_setup_failure(http_server):
    http_server.on("POST", "/login", status=401, json={})
    http_server.on("POST", "/cleanup", json={"ok": True})
    http_server.on("GET", "/me", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
HELPER: Authenticate
  POST: /login
  EXPECT: status == 200
HELPER: Cleanup
  POST: /cleanup
  EXPECT: status == 200
SUITE-SETUP: Authenticate
SUITE-TEARDOWN: Cleanup
TEST: Me
  GET: /me
  EXPECT: status == 200
""",
        )
    )
    assert not result.ok
    assert result.error_name == "SUITE-SETUP (Authenticate)"
    assert result.skipped == 1
    assert [item["path"] for item in http_server.requests] == ["/login", "/cleanup"]


def test_suite_setup_and_teardown_both_fail_reports_both(http_server):
    http_server.on("POST", "/login", status=401, json={})
    http_server.on("POST", "/cleanup", status=500, json={})
    result, _, output = run_dsl(
        _suite(
            http_server,
            """
HELPER: Authenticate
  POST: /login
  EXPECT: status == 200
HELPER: Cleanup
  POST: /cleanup
  EXPECT: status == 200
SUITE-SETUP: Authenticate
SUITE-TEARDOWN: Cleanup
TEST: Me
  GET: /me
  EXPECT: status == 200
""",
        )
    )
    assert not result.ok
    assert result.error_name == "SUITE-SETUP (Authenticate)"
    assert "SUITE-TEARDOWN (Cleanup) also failed" in (result.error or "")
    assert [item["path"] for item in http_server.requests] == ["/login", "/cleanup"]
    assert "SUITE-SETUP (Authenticate)" in output


def test_suite_teardown_multi_step_failure_fails_suite(http_server):
    http_server.on("GET", "/ok", json={"ok": True})
    http_server.on("POST", "/cleanup/1", json={"ok": True})
    http_server.on("POST", "/cleanup/2", status=500, json={})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
HELPER: Cleanup
  POST: /cleanup/1
  EXPECT: status == 200
  POST: /cleanup/2
  EXPECT: status == 200
SUITE-TEARDOWN: Cleanup
TEST: Ok
  GET: /ok
  EXPECT: status == 200
""",
        )
    )
    assert not result.ok
    assert result.error_name == "SUITE-TEARDOWN (Cleanup)"
    assert [item["path"] for item in http_server.requests] == ["/ok", "/cleanup/1", "/cleanup/2"]


def test_suite_setup_teardown_happy_path(http_server):
    http_server.on("POST", "/login", json={"token": "t"})
    http_server.on("GET", "/me", json={"ok": True})
    http_server.on("POST", "/cleanup", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
HELPER: Authenticate
  POST: /login
  EXPECT: status == 200
HELPER: Cleanup
  POST: /cleanup
  EXPECT: status == 200
SUITE-SETUP: Authenticate
SUITE-TEARDOWN: Cleanup
TEST: Me
  GET: /me
  EXPECT: status == 200
""",
        )
    )
    assert result.ok
    assert result.error is None
    assert [item["path"] for item in http_server.requests] == ["/login", "/me", "/cleanup"]


def test_depends_skips_when_upstream_fails(http_server):
    http_server.on("GET", "/create", status=500, json={})
    http_server.on("GET", "/get", json={"ok": True})
    result, _, output = run_dsl(
        _suite(
            http_server,
            """
TEST: Create User
  GET: /create
  EXPECT: status == 200
TEST: Get User
DEPENDS: Create User
  GET: /get
  EXPECT: status == 200
""",
        )
    )
    assert not result.ok
    assert result.failed == 1
    assert result.skipped == 1
    assert result.tests[1].status == "skipped"
    assert "depends on 'Create User' which failed" in result.tests[1].error
    assert [item["path"] for item in http_server.requests] == ["/create"]
    assert "SKIP" in output


def test_depends_skips_when_upstream_skipped(http_server):
    http_server.on("GET", "/create", json={"ok": True})
    http_server.on("GET", "/get", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Create User
SKIP: wip
  GET: /create
  EXPECT: status == 200
TEST: Get User
DEPENDS: Create User
  GET: /get
  EXPECT: status == 200
""",
        )
    )
    assert result.ok
    assert result.skipped == 2
    assert "depends on 'Create User' which was skipped" in result.tests[1].error
    assert http_server.requests == []


def test_depends_reorders_later_dependency(http_server):
    http_server.on("GET", "/one", json={"ok": True})
    http_server.on("GET", "/two", json={"ok": True})
    http_server.on("GET", "/three", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: tc1
  GET: /one
  EXPECT: status == 200
TEST: tc2
DEPENDS: tc3
  GET: /two
  EXPECT: status == 200
TEST: tc3
  GET: /three
  EXPECT: status == 200
""",
        )
    )
    assert result.ok
    assert [test.name for test in result.tests] == ["tc1", "tc3", "tc2"]
    assert [item["path"] for item in http_server.requests] == ["/one", "/three", "/two"]


def test_depends_keeps_unrelated_file_order(http_server):
    http_server.on("GET", "/one", json={"ok": True})
    http_server.on("GET", "/two", json={"ok": True})
    http_server.on("GET", "/three", json={"ok": True})
    http_server.on("GET", "/four", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: tc1
  GET: /one
  EXPECT: status == 200
TEST: tc2
DEPENDS: tc4
  GET: /two
  EXPECT: status == 200
TEST: tc3
  GET: /three
  EXPECT: status == 200
TEST: tc4
  GET: /four
  EXPECT: status == 200
""",
        )
    )
    assert result.ok
    assert [test.name for test in result.tests] == ["tc1", "tc4", "tc2", "tc3"]
    assert [item["path"] for item in http_server.requests] == ["/one", "/four", "/two", "/three"]


def test_depends_skips_when_upstream_not_run(http_server):
    http_server.on("GET", "/create", json={"ok": True})
    http_server.on("GET", "/get", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Create User
  GET: /create
  EXPECT: status == 200
TEST: Get User
DEPENDS: Create User
  GET: /get
  EXPECT: status == 200
""",
        ),
        names=["Get User"],
    )
    assert result.ok
    assert result.skipped == 1
    assert result.passed == 0
    assert "depends on 'Create User' which has not run" in result.tests[0].error
    assert http_server.requests == []


def test_depends_runs_when_upstream_passes(http_server):
    http_server.on("GET", "/create", json={"ok": True})
    http_server.on("GET", "/get", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Create User
  GET: /create
  EXPECT: status == 200
TEST: Get User
DEPENDS: Create User
  GET: /get
  EXPECT: status == 200
""",
        )
    )
    assert result.ok
    assert result.passed == 2
    assert [item["path"] for item in http_server.requests] == ["/create", "/get"]


def test_depends_chain_skips_transitively(http_server):
    http_server.on("GET", "/a", status=500, json={})
    http_server.on("GET", "/b", json={"ok": True})
    http_server.on("GET", "/c", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: A
  GET: /a
  EXPECT: status == 200
TEST: B
DEPENDS: A
  GET: /b
  EXPECT: status == 200
TEST: C
DEPENDS: B
  GET: /c
  EXPECT: status == 200
""",
        )
    )
    assert not result.ok
    assert result.failed == 1
    assert result.skipped == 2
    assert "depends on 'A' which failed" in result.tests[1].error
    assert "depends on 'B' which was skipped" in result.tests[2].error
    assert [item["path"] for item in http_server.requests] == ["/a"]


def test_stop_on_failure_true_halts(http_server):
    http_server.on("GET", "/ok", json={"ok": True})
    http_server.on("GET", "/fail", status=500, json={"ok": False})
    result, _, output = run_dsl(
        _suite(
            http_server,
            """
TEST: First
  REQUEST: GET /fail
  EXPECT: STATUS 200
TEST: Second
  REQUEST: GET /ok
  EXPECT: STATUS 200
""",
        ),
        stop_on_failure=True,
    )
    assert not result.ok
    assert result.failed == 1
    assert result.passed == 0
    assert [item["path"] for item in http_server.requests] == ["/fail"]
    assert "omit -x / --stop-on-failure to continue" in output


def test_stop_on_failure_false_continues(http_server):
    http_server.on("GET", "/ok", json={"ok": True})
    http_server.on("GET", "/fail", status=500, json={"ok": False})
    result, _, _ = run_dsl(_suite(http_server, """
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


def test_retry_file_upload_sends_complete_body_each_attempt(http_server, tmp_path):
    payload = b"BUG003-PHOTO-PAYLOAD-COMPLETE"
    photo = tmp_path / "photo.bin"
    photo.write_bytes(payload)
    http_server.on("POST", "/upload", json={"ok": True}, fail_times=1)
    result, _, _ = run_dsl(_suite(http_server, f"""
TEST: Upload
  POST: /upload
  FILE: avatar FROM {photo}
  EXPECT: status == 200 RETRY 3 ON 5xx BACKOFF 0s
"""))
    assert result.ok
    assert len(http_server.requests) == 2
    _assert_each_upload_contains(http_server, payload)
    assert "multipart" in http_server.requests[0]["headers"].get("Content-Type", "")
    assert "multipart" in http_server.requests[1]["headers"].get("Content-Type", "")


def test_wait_file_upload_sends_complete_body_each_attempt(http_server, tmp_path):
    payload = b"BUG003-WAIT-PHOTO-PAYLOAD-COMPLETE"
    photo = tmp_path / "photo.bin"
    photo.write_bytes(payload)
    state = {"n": 0}

    def handler(record):
        state["n"] += 1
        if state["n"] < 2:
            return 200, {"Content-Type": "application/json"}, {"status": "pending"}
        return 200, {"Content-Type": "application/json"}, {"status": "ready"}

    http_server.on("POST", "/upload", handler=handler)
    result, _, _ = run_dsl(_suite(http_server, f"""
TEST: Poll upload
  POST: /upload
  FILE: avatar FROM {photo}
  WAIT: json $.status == "ready" TIMEOUT 2s BACKOFF 0s
  EXPECT: status == 200
"""))
    assert result.ok
    assert state["n"] == 2
    assert len(http_server.requests) == 2
    _assert_each_upload_contains(http_server, payload)


def test_retry_multiple_file_uploads_send_complete_bodies_each_attempt(http_server, tmp_path):
    photo_bytes = b"BUG003-MULTI-PHOTO-PAYLOAD-COMPLETE"
    banner_bytes = b"BUG003-MULTI-BANNER-PAYLOAD-COMPLETE"
    photo = tmp_path / "photo.bin"
    banner = tmp_path / "banner.bin"
    photo.write_bytes(photo_bytes)
    banner.write_bytes(banner_bytes)
    http_server.on("POST", "/upload", json={"ok": True}, fail_times=1)
    result, _, _ = run_dsl(_suite(http_server, f"""
TEST: Upload both
  POST: /upload
  FILE: avatar FROM {photo}
  FILE: banner FROM {banner}
  EXPECT: status == 200 RETRY 3 ON 5xx BACKOFF 0s
"""))
    assert result.ok
    assert len(http_server.requests) == 2
    _assert_each_upload_contains(http_server, photo_bytes, banner_bytes)
    for request in http_server.requests:
        body = request["body"]
        assert "photo.bin" in body
        assert "banner.bin" in body


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


def test_soft_collects_all_expect_failures(http_server):
    http_server.on("GET", "/x", json={"ok": False, "email": "other@example.com"})
    result, _, output = run_dsl(_suite(http_server, """
TEST: Soft
  GET: /x
  EXPECT: status == 201
  EXPECT: json $.ok == true
  EXPECT: json $.email == "ada@example.com"
"""))
    assert not result.ok
    error = result.tests[0].error or ""
    assert "Status code expected 201, got 200" in error
    assert "JSON $.ok expected True, got False" in error
    assert "ada@example.com" in error
    assert "Status code expected 201, got 200" in output
    assert "JSON $.ok expected True, got False" in output


def test_soft_collect_retries_until_last_attempt(http_server):
    http_server.on("GET", "/flaky", json={"ok": True}, fail_times=2)
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Flaky
  GET: /flaky
  EXPECT: status == 200
  EXPECT: json $.ok == true RETRY 5
"""))
    assert result.ok
    assert len(http_server.requests) == 3


def test_expect_new_json_operators(http_server):
    http_server.on(
        "GET",
        "/payload",
        json={
            "ok": True,
            "id": "abc",
            "status": "open",
            "email": "ada@example.com",
            "items": [],
            "tags": ["b", "a"],
            "ids": [1, 2, 3],
            "count": 5,
            "score": 0.329,
            "missing": None,
        },
        headers={"Content-Type": "application/json; charset=utf-8", "X-Status": "open"},
    )
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Ops
  GET: /payload
  EXPECT: json $.items empty
  EXPECT: json $.tags not empty
  EXPECT: json $.id type string
  EXPECT: json $.count type number
  EXPECT: json $.ok type boolean
  EXPECT: json $.items type array
  EXPECT: json $ type object
  EXPECT: json $.missing type null
  EXPECT: json $.status in ["open","pending"]
  EXPECT: json $.email starts-with "ada@"
  EXPECT: json $.email ends-with "@example.com"
  EXPECT: json $.tags contains-only ["a","b"]
  EXPECT: json $.tags contains-any ["z","a"]
  EXPECT: json $.ids unique
  EXPECT: json $.count between 1 10
  EXPECT: json $.score close-to 0.33 delta 0.01
  EXPECT: json $.email not matches @tempmail
  EXPECT: json $.password absent
  EXPECT: json $.id exists
  EXPECT: json $.missing exists
  EXPECT: json $.nope not exists
  EXPECT: header Content-Type starts-with application
  EXPECT: header X-Status in ["open","pending"]
  EXPECT: header X-Debug absent
  EXPECT: header Content-Type exists
  EXPECT: header Content-Type not contains xml
  EXPECT: body not empty
"""))
    assert result.ok


def test_expect_collection_and_key_operators(http_server):
    http_server.on(
        "GET",
        "/payload",
        json={
            "status": "open",
            "events": ["created", "paid", "shipped"],
            "roles": ["editor", "viewer"],
            "ids": [1, 2, 3],
            "desc_ids": [3, 2, 1],
            "user": {"id": 1, "email": "Ada@Example.com", "name": "Ada"},
            "count": 5,
            "balance": 0,
            "debt": -2,
            "tags": ["a", "b"],
        },
        headers={"Content-Type": "Application/JSON", "X-Env": "Stage"},
    )
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Collections
  GET: /payload
  EXPECT: json $.status not in ["error","failed"]
  EXPECT: json $.events contains-sequence ["created","paid"]
  EXPECT: json $.roles subset-of ["admin","editor","viewer"]
  EXPECT: json $.ids sorted
  EXPECT: json $.desc_ids sorted desc
  EXPECT: json $.user contains-keys ["id","email"]
  EXPECT: json $.user not contains-keys ["password","ssn"]
  EXPECT: json $.tags not contains "z"
  EXPECT: json $.user.email equals-ignoring-case "ada@example.com"
  EXPECT: json $.count positive
  EXPECT: json $.balance zero
  EXPECT: json $.debt negative
  EXPECT: header Content-Type contains-ignoring-case json
  EXPECT: header X-Env equals-ignoring-case stage
"""))
    assert result.ok


def test_expect_collection_operators_fail(http_server):
    http_server.on(
        "GET",
        "/payload",
        json={
            "status": "error",
            "events": ["created", "shipped"],
            "roles": ["root"],
            "ids": [3, 1, 2],
            "user": {"id": 1, "password": "x"},
        },
    )
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Failures
  GET: /payload
  EXPECT: json $.status not in ["error"]
  EXPECT: json $.events contains-sequence ["created","paid"]
  EXPECT: json $.roles subset-of ["admin","editor"]
  EXPECT: json $.ids sorted
  EXPECT: json $.user contains-keys ["email"]
  EXPECT: json $.user not contains-keys ["password"]
"""))
    assert not result.ok
    error = result.tests[0].error or ""
    assert "unexpectedly in" in error
    assert "does not contain sequence" in error
    assert "not a subset" in error
    assert "expected ascending sort" in error
    assert "missing keys" in error
    assert "unexpectedly has keys" in error
    http_server.on("GET", "/user", json={"password": "secret"})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Leak
  GET: /user
  EXPECT: json $.password absent
"""))
    assert not result.ok
    assert "should be absent" in (result.tests[0].error or "")


def test_expect_exists_fails_when_path_missing(http_server):
    http_server.on("GET", "/user", json={"id": 1})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Missing
  GET: /user
  EXPECT: json $.email exists
"""))
    assert not result.ok
    assert "should exist" in (result.tests[0].error or "")


def test_json_equality_uses_json_types_not_python(http_server):
    """JSON == / != match playground JS === (bool ≠ number; 1 == 1.0)."""
    http_server.on(
        "GET",
        "/types",
        json={
            "ok": True,
            "off": False,
            "count": 1,
            "ratio": 1.0,
            "nested": {"enabled": True},
            "flags": [True],
        },
    )
    for label, expect in [
        ("bool-as-one", "EXPECT: json $.ok == 1"),
        ("false-as-zero", "EXPECT: json $.off == 0"),
        ("nested", 'EXPECT: json $.nested == {"enabled": 1}'),
        ("array", "EXPECT: json $.flags == [1]"),
    ]:
        result, _, _ = run_dsl(
            _suite(
                http_server,
                f"""
TEST: {label}
  GET: /types
  {expect}
""",
            )
        )
        assert not result.ok, label
        assert "expected" in (result.tests[0].error or ""), label

    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Ok
  GET: /types
  EXPECT: json $.ok == true
  EXPECT: json $.ok != 1
  EXPECT: json $.off == false
  EXPECT: json $.off != 0
  EXPECT: json $.count == 1
  EXPECT: json $.count == 1.0
  EXPECT: json $.ratio == 1
  EXPECT: json $.nested == {"enabled": true}
  EXPECT: json $.nested != {"enabled": 1}
  EXPECT: json $.flags == [true]
  EXPECT: json $.flags != [1]
""",
        )
    )
    assert result.ok, result.tests[0].error


def test_json_equal_unit():
    from snapapi.engine import _json_equal

    assert _json_equal(True, True)
    assert not _json_equal(True, 1)
    assert not _json_equal(False, 0)
    assert _json_equal(1, 1.0)
    assert _json_equal({"enabled": True}, {"enabled": True})
    assert not _json_equal({"enabled": True}, {"enabled": 1})
    assert _json_equal([True], [True])
    assert not _json_equal([True], [1])
    assert _json_equal(None, None)
    assert not _json_equal(None, False)


def test_package_version_is_single_source():
    from snapapi import __version__
    from snapapi.engine import SNAPAPI_VERSION, _as_har
    from snapapi.version import __version__ as source

    assert __version__ == source == "0.5.0"
    assert SNAPAPI_VERSION == source
    har = _as_har(
        SimpleNamespace(
            method="GET",
            url="http://example.com",
            status_code=200,
            request_headers={},
            response_headers={},
            response_body="{}",
            duration_ms=1,
        )
    )
    assert har["log"]["creator"]["version"] == source


def test_expect_absent_exists_wildcard_semantics(http_server):
    """exists = at least one match; absent = zero matches (null still present)."""
    cases = [
        (
            "empty-list",
            {"items": []},
            """
  EXPECT: json $.items[*].password absent
  EXPECT: json $.items[*].password exists
""",
            False,
            "should exist",
        ),
        (
            "no-password",
            {"items": [{"id": 1}]},
            """
  EXPECT: json $.items[*].password absent
""",
            True,
            None,
        ),
        (
            "all-have-password",
            {"items": [{"id": 1, "password": "x"}]},
            """
  EXPECT: json $.items[*].password exists
  EXPECT: json $.items[*].password absent
""",
            False,
            "should be absent",
        ),
        (
            "mixed-must-not-absent",
            {"items": [{"id": 1}, {"id": 2, "password": "x"}]},
            """
  EXPECT: json $.items[*].password absent
""",
            False,
            "should be absent",
        ),
        (
            "mixed-exists-passes",
            {"items": [{"id": 1}, {"id": 2, "password": "x"}]},
            """
  EXPECT: json $.items[*].password exists
""",
            True,
            None,
        ),
        (
            "null-password-is-present",
            {"items": [{"id": 1, "password": None}]},
            """
  EXPECT: json $.items[*].password exists
  EXPECT: json $.items[*].password absent
""",
            False,
            "should be absent",
        ),
        (
            "scalar-null-present",
            {"password": None},
            """
  EXPECT: json $.password exists
""",
            True,
            None,
        ),
    ]
    for name, payload, expects, ok, error_snip in cases:
        http_server.on("GET", f"/{name}", json=payload)
        result, _, _ = run_dsl(
            _suite(
                http_server,
                f"""
TEST: {name}
  GET: /{name}
{expects}
""",
            )
        )
        assert result.ok is ok, f"{name}: ok={result.ok} error={result.tests[0].error!r}"
        if error_snip:
            assert error_snip in (result.tests[0].error or ""), name


def test_expect_header_absent(http_server):
    http_server.on("GET", "/ok", json={"ok": True}, headers={"Content-Type": "application/json"})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Headers
  GET: /ok
  EXPECT: header X-Debug absent
  EXPECT: header Content-Type present
"""))
    assert result.ok


def test_expect_new_json_operators_fail_with_expected_vs_actual(http_server):
    http_server.on(
        "GET",
        "/payload",
        json={"email": "bob@tempmail.test", "tags": ["a", "a"], "count": 50, "score": 1.0, "items": [1]},
    )
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Ops fail
  GET: /payload
  EXPECT: json $.items empty
  EXPECT: json $.email starts-with "ada@"
  EXPECT: json $.tags unique
  EXPECT: json $.count between 1 10
  EXPECT: json $.score close-to 0.33 delta 0.01
  EXPECT: json $.email not matches @tempmail
"""))
    assert not result.ok
    error = result.tests[0].error or ""
    assert "expected empty" in error
    assert "does not start with" in error
    assert "expected unique" in error
    assert "expected between 1.0 and 10.0" in error or "expected between 1 and 10" in error
    assert "±" in error
    assert "unexpectedly matches" in error


def test_because_prefixes_failure(http_server):
    http_server.on("GET", "/login", json={"ok": False})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Login
  GET: /login
  EXPECT: json $.ok == true BECAUSE "login should succeed"
"""))
    assert not result.ok
    error = result.tests[0].error or ""
    assert error.startswith("login should succeed:")
    assert "expected True, got False" in error


def test_body_empty_and_starts_with(http_server):
    http_server.on("GET", "/empty", text="", headers={"Content-Type": "text/plain"})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Empty
  GET: /empty
  EXPECT: body empty
"""))
    assert result.ok

    http_server.on("GET", "/hello", text="hello world")
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Hello
  GET: /hello
  EXPECT: body starts-with hello
  EXPECT: body ends-with world
  EXPECT: body matches hell
  EXPECT: body not matches stack
"""))
    assert result.ok


def test_wait_attempt_cap_fails_test_not_runner(monkeypatch):
    suite = parse_dsl("""
SUITE: Local
URL: http://example.test
TEST: Poll
  GET: /job
  WAIT: json $.status == "ready" TIMEOUT 3600s BACKOFF 0s
""")
    stream = io.StringIO()
    engine = Engine(suite, stream=stream, retry_backoff=0)
    pending = SimpleNamespace(
        status_code=200,
        url="http://example.test/job",
        headers={"Content-Type": "application/json"},
        text='{"status": "pending"}',
        json=lambda: {"status": "pending"},
    )
    monkeypatch.setattr(engine, "_dispatch", lambda *args, **kwargs: pending)
    monkeypatch.setattr("snapapi.engine.time.sleep", lambda _seconds: None)
    result = engine.run()
    assert result.ok is False
    assert result.failed == 1
    assert result.tests[0].status == "failed"
    assert result.tests[0].error


def test_invalid_matches_regex_fails_test_not_runner(http_server):
    http_server.on("GET", "/user", json={"email": "ada@example.com"})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Regex
  GET: /user
  EXPECT: json $.email matches [
"""))
    assert not result.ok
    assert result.tests[0].status == "failed"
    error = (result.tests[0].error or "").lower()
    assert "regex" in error or "pattern" in error


def test_json_length_on_null_fails_test_not_runner(http_server):
    http_server.on("GET", "/user", json={"email": None})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Null length
  GET: /user
  EXPECT: json $.email length == 0
"""))
    assert not result.ok
    assert result.tests[0].status == "failed"
    error = (result.tests[0].error or "").lower()
    assert "length" in error
    assert "null" in error


def test_json_length_on_number_fails_test_not_runner(http_server):
    http_server.on("GET", "/user", json={"id": 1})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Number length
  GET: /user
  EXPECT: json $.id length == 1
"""))
    assert not result.ok
    assert result.tests[0].status == "failed"
    error = (result.tests[0].error or "").lower()
    assert "length" in error
    assert "number" in error or "int" in error


def test_safe_url_blocks_oauth_loopback_token_url(http_server):
    http_server.on("POST", "/oauth/token", json={"access_token": "should-not-issue"})
    http_server.on("GET", "/public", json={"ok": True})
    result, _, _ = run_dsl(
        f"""
SUITE: Safe
URL: http://example.com
TEST: Token
  GET: /public
  AUTH: oauth2 token_url={http_server.base_url}/oauth/token client_id=id client_secret=s
  EXPECT: status == 200
""",
        safe_url=True,
        timeout=2,
    )
    assert not result.ok
    assert result.tests[0].status == "failed"
    error = result.tests[0].error or ""
    assert "Blocked" in error
    assert "127.0.0.1" in error
    assert http_server.requests == []


def test_safe_url_blocks_oauth_private_token_url(monkeypatch, http_server):
    http_server.on("POST", "/oauth/token", json={"access_token": "should-not-issue"})

    def fail_send(*args, **kwargs):
        raise AssertionError("HTTP send must not run for a private OAuth token URL")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fail_send)
    result, _, _ = run_dsl(
        """
SUITE: Safe
URL: http://example.com
TEST: Token
  GET: /public
  AUTH: oauth2 token_url=http://10.0.0.1:9/oauth/token client_id=id client_secret=s
  EXPECT: status == 200
""",
        safe_url=True,
        timeout=2,
    )
    assert not result.ok
    assert result.tests[0].status == "failed"
    error = result.tests[0].error or ""
    assert "Blocked" in error
    assert "10.0.0.1" in error
    assert http_server.requests == []


def test_oauth_loopback_token_url_still_works_without_safe_url(http_server):
    http_server.on("POST", "/oauth/token", json={"access_token": "tok-ok"})
    http_server.on("GET", "/secure", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: Token
  GET: /secure
  AUTH: oauth2 token_url={http_server.base_url}/oauth/token client_id=id client_secret=s
  EXPECT: status == 200
""",
        ),
        safe_url=False,
        timeout=2,
    )
    assert result.ok
    assert [item["path"] for item in http_server.requests] == ["/oauth/token", "/secure"]
    assert http_server.requests[1]["headers"].get("Authorization") == "Bearer tok-ok"


def test_safe_url_blocks_redirect_to_loopback(http_server, monkeypatch):
    http_server.on("GET", "/secret", json={"pwned": True})
    sent = stub_public_host_http(
        monkeypatch,
        {"/start": (302, {"Location": f"{http_server.base_url}/secret"}, b"")},
    )
    result, _, _ = run_dsl(
        """
SUITE: Safe
URL: http://example.com
TEST: Redir
  GET: /start
  EXPECT: status == 200
""",
        safe_url=True,
        timeout=2,
    )
    assert not result.ok
    assert result.tests[0].status == "failed"
    error = result.tests[0].error or ""
    assert "Blocked" in error
    assert "127.0.0.1" in error
    assert http_server.requests == []
    assert sent == ["http://example.com/start"]
    assert all("127.0.0.1" not in url for url in sent)


def test_safe_url_blocks_oauth_token_redirect_to_loopback(http_server, monkeypatch):
    http_server.on("POST", "/oauth/token", json={"access_token": "leaked"})
    http_server.on("GET", "/oauth/token", json={"access_token": "leaked"})
    sent = stub_public_host_http(
        monkeypatch,
        {"/token": (302, {"Location": f"{http_server.base_url}/oauth/token"}, b"")},
    )
    result, _, _ = run_dsl(
        """
SUITE: Safe
URL: http://example.com
TEST: TokenRedir
  GET: /public
  AUTH: oauth2 token_url=http://example.com/token client_id=id client_secret=s
  EXPECT: status == 200
""",
        safe_url=True,
        timeout=2,
    )
    assert not result.ok
    assert result.tests[0].status == "failed"
    error = result.tests[0].error or ""
    assert "Blocked" in error
    assert "127.0.0.1" in error
    assert http_server.requests == []
    assert sent == ["http://example.com/token"]
    assert all("127.0.0.1" not in url for url in sent)


def test_safe_url_allows_redirect_between_public_urls(monkeypatch):
    stub_public_host_http(
        monkeypatch,
        {
            "/from": (302, {"Location": "http://example.com/to"}, b""),
            "/to": (200, {"Content-Type": "application/json"}, b'{"ok": true}'),
        },
    )
    result, _, _ = run_dsl(
        """
SUITE: Safe
URL: http://example.com
TEST: Redir
  GET: /from
  EXPECT: status == 200
""",
        safe_url=True,
        timeout=2,
    )
    assert result.ok
    assert result.tests[0].status == "passed"


def test_safe_url_still_blocks_metadata_host():
    result, _, _ = run_dsl(
        """
SUITE: Unsafe
URL: http://169.254.169.254
TEST: Meta
  GET: /latest
  EXPECT: status == 200
""",
        safe_url=True,
        timeout=2,
    )
    assert not result.ok
    assert result.tests[0].status == "failed"
    assert "Blocked" in (result.tests[0].error or "")


def test_safe_url_blocks_dns_rebinding_engine(http_server, monkeypatch):
    http_server.on("GET", "/secret", json={"pwned": True})
    host = "rebinder.test"
    stub_dns_rebinding(monkeypatch, host, connect_ip="127.0.0.1")
    result, _, _ = run_dsl(
        f"""
SUITE: Safe
URL: http://{host}:{http_server.port}
TEST: Rebind
  GET: /secret
  EXPECT: status == 200
""",
        safe_url=True,
        timeout=2,
    )
    assert not result.ok
    assert result.tests[0].status == "failed"
    assert "Blocked" in (result.tests[0].error or "")
    assert http_server.requests == []


def test_safe_url_blocks_oauth_dns_rebinding_secret(http_server, monkeypatch):
    http_server.on("POST", "/oauth/token", json={"access_token": "leaked"})
    host = "rebinder.test"
    stub_dns_rebinding(monkeypatch, host, connect_ip="127.0.0.1")
    result, _, _ = run_dsl(
        f"""
SUITE: Safe
URL: http://example.com
TEST: Token
  GET: /public
  AUTH: oauth2 token_url=http://{host}:{http_server.port}/oauth/token client_id=id client_secret=SUPERSECRET
  EXPECT: status == 200
""",
        safe_url=True,
        timeout=2,
    )
    assert not result.ok
    assert result.tests[0].status == "failed"
    assert "Blocked" in (result.tests[0].error or "")
    assert http_server.requests == []
    assert all("SUPERSECRET" not in (item.get("body") or "") for item in http_server.requests)


def test_safe_url_blocks_env_http_proxy_engine(http_server, monkeypatch):
    http_server.on("GET", "/start", json={"via-proxy": True})
    monkeypatch.setenv("HTTP_PROXY", http_server.base_url)
    monkeypatch.setenv("http_proxy", http_server.base_url)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    stub_public_host_http(monkeypatch, {"/start": (200, {"Content-Type": "application/json"}, b'{"ok": true}')})
    result, _, _ = run_dsl(
        """
SUITE: Safe
URL: http://example.com
TEST: Direct
  GET: /start
  EXPECT: status == 200
""",
        safe_url=True,
        timeout=2,
    )
    assert result.ok
    assert http_server.requests == [], "env HTTP_PROXY must not receive traffic under safe_url"


def test_safe_url_blocks_explicit_loopback_proxy_engine(http_server):
    http_server.on("GET", "/start", json={"via-proxy": True})
    result, _, _ = run_dsl(
        """
SUITE: Safe
URL: http://example.com
TEST: Proxied
  GET: /start
  EXPECT: status == 200
""",
        safe_url=True,
        timeout=2,
        proxies={"http": http_server.base_url, "https": http_server.base_url},
    )
    assert not result.ok
    assert result.tests[0].status == "failed"
    assert "Blocked" in (result.tests[0].error or "")
    assert http_server.requests == []


def test_safe_url_blocks_cgnat_engine(monkeypatch):
    def fail_send(*args, **kwargs):
        raise AssertionError("HTTP send must not run for CGNAT destinations")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fail_send)
    result, _, _ = run_dsl(
        """
SUITE: Safe
URL: http://100.100.100.200
TEST: Cgnat
  GET: /
  EXPECT: status == 200
""",
        safe_url=True,
        timeout=2,
    )
    assert not result.ok
    assert result.tests[0].status == "failed"
    assert "Blocked" in (result.tests[0].error or "")

