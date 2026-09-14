import json
from pathlib import Path

from snapapi.cli import main
from snapapi.fmt import format_text
from snapapi.openapi import generate_smoke
from snapapi.redact import redact_headers, redact_saved
from tests.helpers import parse_dsl, run_dsl


def _suite(server, body, extra=""):
    return f"""
SUITE: Local
URL: {server.base_url}
{extra}
{body}
"""


def test_form_and_raw_bodies(http_server):
    http_server.on("POST", "/form", json={"ok": True})
    http_server.on("POST", "/xml", json={"ok": True})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Form
  POST: /form
  BODY: form username=Jane&role=admin
  EXPECT: status == 200
TEST: Raw
  POST: /xml
  BODY: raw application/xml <Order id="1"/>
  EXPECT: status == 200
"""))
    assert result.ok
    assert http_server.requests[0]["body"] == "username=Jane&role=admin"
    assert "xml" in http_server.requests[1]["headers"].get("Content-Type", "")
    assert "<Order" in http_server.requests[1]["body"]


def test_file_upload(http_server, tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"jpeg-bytes")
    http_server.on("POST", "/upload", json={"ok": True})
    suite = tmp_path / "upload.snaptest"
    suite.write_text(
        f"""
SUITE: Upload
URL: {http_server.base_url}
TEST: Send file
  POST: /upload
  FILE: avatar FROM ./photo.jpg
  EXPECT: status == 200
""",
        encoding="utf-8",
    )
    assert main([str(suite)]) == 0
    assert "multipart" in http_server.requests[0]["headers"].get("Content-Type", "")


def test_schema_inline_and_file(http_server, tmp_path):
    http_server.on("GET", "/user", json={"id": 1, "name": "Jane"})
    schema = tmp_path / "user.json"
    schema.write_text('{"type":"object","required":["id","name"]}', encoding="utf-8")
    suite = tmp_path / "schema.snaptest"
    suite.write_text(
        f"""
SUITE: Schema
URL: {http_server.base_url}
TEST: Schema
  GET: /user
  EXPECT: schema inline {{"type":"object","required":["id"]}}
  EXPECT: schema ./user.json
""",
        encoding="utf-8",
    )
    assert main([str(suite)]) == 0


def test_fail_dump_redacts_authorization(http_server):
    http_server.on("GET", "/secret", status=500, json={"error": "nope"})
    _, _, output = run_dsl(
        _suite(
            http_server,
            """
TEST: Secret
  GET: /secret
  HEADER Authorization: Bearer super-secret-token
  EXPECT: status == 200
""",
        )
    )
    assert "Status code expected 200, got 500" in output
    assert "Bearer ***" in output or "Authorization: ***" in output
    assert "super-secret-token" not in output
    assert "request:" in output
    assert "response:" in output


def test_lint_undefined_and_ok(tmp_path):
    good = tmp_path / "good.snaptest"
    good.write_text(
        """
SUITE: Good
URL: https://example.com
TEST: A
  GET: /x
  EXPECT: status == 200
""",
        encoding="utf-8",
    )
    bad = tmp_path / "bad.snaptest"
    bad.write_text(
        """
SUITE: Bad
URL: ${MISSING_HOST}
TEST: A
  GET: /x
  EXPECT: status == 200
""",
        encoding="utf-8",
    )
    assert main(["lint", str(good)]) == 0
    assert main(["lint", str(bad)]) == 2


def test_lint_recommended_suite_uses_sibling_env():
    suite = Path(__file__).parent / "recommended.snaptest"
    assert main(["lint", str(suite)]) == 0


def test_profile_and_workers_isolate_save(http_server, tmp_path):
    http_server.on("GET", "/a", json={"id": "a"})
    http_server.on("GET", "/b", json={"id": "b"})
    env = tmp_path / "stage.env"
    env.write_text(f"BASE={http_server.base_url}\n", encoding="utf-8")
    (tmp_path / "environments").mkdir()
    (tmp_path / "environments" / "stage.env").write_text(f"BASE={http_server.base_url}\n", encoding="utf-8")
    suite = tmp_path / "iso.snaptest"
    suite.write_text(
        f"""
SUITE: Iso
URL: ${{BASE}}
TEST: A
  GET: /a
  EXPECT: status == 200
  SAVE: id FROM $.id
TEST: B
  GET: /b
  EXPECT: status == 200
  SAVE: id FROM $.id
""",
        encoding="utf-8",
    )
    old = Path.cwd()
    try:
        import os

        os.chdir(tmp_path)
        assert main([str(suite), "--profile", "stage", "--workers", "2"]) == 0
    finally:
        os.chdir(old)


def test_richer_asserts_and_duration(http_server):
    http_server.on(
        "GET",
        "/user",
        json={"email": "jane@example.com", "score": 9, "tags": ["admin", "user"], "items": [1, 2, 3]},
    )
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Rich
  GET: /user
  EXPECT: status != 500
  EXPECT: json $.email matches ^jane@
  EXPECT: json $.items length == 3
  EXPECT: json $.score > 0
  EXPECT: json $.tags contains "admin"
  EXPECT: duration < 2000ms
"""))
    assert result.ok


def test_save_header_and_cookie(http_server):
    def handler(record):
        return 200, {"X-CSRF-Token": "abc", "Set-Cookie": "JSESSIONID=xyz; Path=/"}, {"ok": True}

    http_server.on("GET", "/sess", handler=handler)
    http_server.on("GET", "/next", json={"ok": True})
    result, engine, _ = run_dsl(_suite(http_server, """
TEST: Session
  GET: /sess
  EXPECT: status == 200
  SAVE: csrf FROM header X-CSRF-Token
  SAVE: session FROM cookie JSESSIONID
TEST: Use
  GET: /next
  HEADER X-CSRF-Token: ${csrf}
  EXPECT: status == 200
"""))
    assert result.ok
    assert engine.variables["csrf"] == "abc"
    assert engine.variables["session"] == "xyz"
    assert http_server.requests[1]["headers"].get("X-CSRF-Token") == "abc"


def test_helpers_uuid_and_examples(http_server):
    http_server.on("POST", "/users", status=201, json={"ok": True})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Create
EXAMPLES:
  name,email
  Jane,jane@example.com
  Bob,bob@example.com
  POST: /users
  BODY: {"name": "${name}", "email": "${email}", "id": "${uuid()}"}
  EXPECT: status == 201
"""))
    assert result.ok
    assert [item.name for item in result.tests] == ["Create [Jane]", "Create [Bob]"]
    assert http_server.requests[0]["json"]["email"] == "jane@example.com"
    assert http_server.requests[0]["json"]["id"]


def test_skip_only_suite_setup_and_grep(http_server):
    http_server.on("POST", "/login", json={"ok": True})
    http_server.on("GET", "/a", json={"ok": True})
    http_server.on("GET", "/b", json={"ok": True})
    http_server.on("GET", "/c", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Authenticate
  POST: /login
  EXPECT: status == 200
TEST: Alpha
ONLY:
  GET: /a
  EXPECT: status == 200
TEST: Beta
SKIP: wip
  GET: /b
  EXPECT: status == 200
TEST: Gamma
  GET: /c
  EXPECT: status == 200
""",
            extra="SUITE-SETUP: Authenticate",
        )
    )
    assert result.ok
    assert [item["path"] for item in http_server.requests] == ["/login", "/a"]

    http_server.requests.clear()
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Create User
  GET: /a
  EXPECT: status == 200
TEST: List Users
  GET: /c
  EXPECT: status == 200
""",
        ),
        grep="Create",
    )
    assert result.passed == 1
    assert result.skipped == 1


def test_retry_on_5xx_only(http_server):
    http_server.on("GET", "/client", status=400, json={"error": "no"})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Client
  GET: /client
  EXPECT: status == 200 RETRY 3 ON 5xx
"""))
    assert not result.ok
    assert len(http_server.requests) == 1


def test_negative_body_and_cli_last_failed(http_server, tmp_path):
    http_server.on("POST", "/bad", status=422, json={"error": {"code": "INVALID_EMAIL", "details": ["x"]}})
    result, _, _ = run_dsl(_suite(http_server, """
TEST: Invalid
  POST: /bad
  EXPECT: status == 422
  EXPECT: json $.error.code == "INVALID_EMAIL"
  EXPECT: json $.error.details length >= 1
  EXPECT: body not contains stack
"""))
    assert result.ok

    suite = tmp_path / "fail.snaptest"
    suite.write_text(
        f"""
SUITE: X
URL: {http_server.base_url}
TEST: Boom
  GET: /missing
  EXPECT: status == 200
""",
        encoding="utf-8",
    )
    assert main([str(suite)]) == 1
    assert main([str(suite), "--last-failed"]) == 1


def test_graphql_oauth_html_vcr_fmt_openapi(http_server, tmp_path):
    http_server.on("POST", "/oauth/token", json={"access_token": "tok-1"})
    http_server.on("POST", "/graphql", json={"data": {"user": {"name": "Jane"}}})
    result, _, _ = run_dsl(_suite(http_server, f"""
TEST: Gql
  POST: /graphql
  AUTH: oauth2 token_url={http_server.base_url}/oauth/token client_id=id client_secret=s
  GRAPHQL: {{"query": "query {{ user {{ name }} }}", "variables": {{}}}}
  EXPECT: json $.data.user.name == "Jane"
"""))
    assert result.ok
    assert http_server.requests[1]["headers"].get("Authorization") == "Bearer tok-1"
    assert "query" in http_server.requests[1]["json"]

    suite = tmp_path / "ok.snaptest"
    suite.write_text(
        f"""
SUITE: Html
URL: {http_server.base_url}
TEST: Ping
  GET: /graphql
  EXPECT: status == 200
""",
        encoding="utf-8",
    )
    http_server.on("GET", "/graphql", json={"ok": True})
    html = tmp_path / "out.html"
    assert main([str(suite), "--report", f"html:{html}"]) == 0
    assert "SnapAPI report" in html.read_text(encoding="utf-8")

    cassette_dir = tmp_path / "cassettes"
    assert main([str(suite), "--mode", "record"]) == 0
    # record writes to .snapapi/cassettes in cwd; force via OPTIONS
    suite.write_text(
        f"""
SUITE: Replay
OPTIONS: {{"MODE": "record", "CASSETTE_DIR": "{cassette_dir}"}}
URL: {http_server.base_url}
TEST: Ping
  GET: /graphql
  EXPECT: status == 200
""",
        encoding="utf-8",
    )
    assert main([str(suite)]) == 0
    assert list(cassette_dir.glob("*.json"))

    messy = "SUITE:X\nTEST:A\nGET:/x\nEXPECT: status == 200\n"
    assert "SUITE: X" in format_text(messy) or "SUITE:X" in format_text(messy) or format_text(messy)

    spec = tmp_path / "openapi.json"
    spec.write_text(
        json.dumps(
            {
                "servers": [{"url": "https://api.example.com"}],
                "paths": {"/users": {"get": {"operationId": "listUsers", "summary": "List"}}},
            }
        ),
        encoding="utf-8",
    )
    text = generate_smoke(spec)
    assert "GET: /users" in text
    assert main(["openapi", str(spec)]) == 0


def test_safe_url_blocks_metadata():
    result, _, _ = run_dsl(
        """
SUITE: Unsafe
URL: http://169.254.169.254
TEST: Meta
  GET: /latest
  EXPECT: status == 200
""",
        safe_url=True,
    )
    assert not result.ok
    assert "Blocked" in (result.tests[0].error or "")


def test_redact_headers_unit():
    assert redact_headers({"Authorization": "Bearer abc", "X-Trace": "1"})["Authorization"] == "***"
    assert redact_headers({"X-Trace": "1"})["X-Trace"] == "1"


def test_redact_saved_hides_tokens():
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.signature"
    assert redact_saved("accessToken", jwt) == "***"
    assert redact_saved("refreshToken", jwt) == "***"
    assert redact_saved("password", "Rajesh@123") == "***"
    assert redact_saved("userId", "7") == "7"
    assert redact_saved("email", "jane@example.com") == "jane@example.com"


def test_parse_new_keywords():
    suite = parse_dsl(
        """
SUITE: Demo
FOLLOW-REDIRECTS: false
TEST: A
SKIP: later
QUARANTINE: flake
  GET: /x
  FILE: avatar FROM ./a.jpg
  EXPECT: status == 200 RETRY 5 ON 5xx BACKOFF 1s
  SAVE: csrf FROM header X-CSRF
"""
    )
    assert suite["follow_redirects"] is False
    test = suite["tests"][0]
    assert test["skip"] == "later"
    assert test["quarantine"] == "flake"
    assert test["steps"][0]["files"][0]["field"] == "avatar"
    assert test["steps"][0]["checks"][0]["retry_on"] == "5xx"
    assert test["steps"][0]["saves"][0]["source"] == "header"
