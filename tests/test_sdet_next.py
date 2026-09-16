import json
import time

import pytest

from snapapi.cassette import cassette_key
from snapapi.cli import main
from snapapi.mock import MockServer, load_mock_routes
from tests.helpers import parse_dsl, run_dsl


def _suite(server, body, extra=""):
    return f"""
SUITE: Local
URL: {server.base_url}
{extra}
{body}
"""


def test_jsonpath_filters_and_collection_asserts(http_server):
    http_server.on(
        "GET",
        "/items",
        json={
            "items": [
                {"id": 1, "status": "open"},
                {"id": 2, "status": "closed"},
                {"id": 3, "status": "open"},
            ],
            "tags": ["a", "b", "c"],
            "members": [{"status": "active"}, {"status": "active"}],
        },
    )
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Collections
  GET: /items
  EXPECT: status == 200
  EXPECT: json $.items[?(@.status=="open")].id contains 3
  EXPECT: json $.items[?(@.id==1)] length == 1
  EXPECT: json $.tags contains-all ["a","b"]
  EXPECT: json $.tags contains all ["b","c"]
  EXPECT: json $.members each $.status == "active"
""",
        )
    )
    assert result.ok


def test_jsonpath_contains_all_failure(http_server):
    http_server.on("GET", "/tags", json={"tags": ["a"]})
    result, _, output = run_dsl(
        _suite(
            http_server,
            """
TEST: Missing
  GET: /tags
  EXPECT: json $.tags contains-all ["a","b"]
""",
        )
    )
    assert not result.ok
    assert "does not contain all" in output


def test_openapi_strict_unmatched_path_fails(http_server, tmp_path):
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
                                            "schema": {"type": "object", "required": ["ok"]}
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
    http_server.on("GET", "/missing", json={"ok": True})
    result, _, output = run_dsl(
        f"""
SUITE: Strict
OPTIONS: {{"OPENAPI": "{spec.as_posix()}", "OPENAPI-STRICT": true}}
URL: {http_server.base_url}
TEST: Missing
  GET: /missing
  EXPECT: status == 200
"""
    )
    assert not result.ok
    assert "unmatched path/method" in output

    result, _, _ = run_dsl(
        f"""
SUITE: Compat
OPTIONS: {{"OPENAPI": "{spec.as_posix()}"}}
URL: {http_server.base_url}
TEST: Missing
  GET: /missing
  EXPECT: status == 200
"""
    )
    assert result.ok

    result, _, output = run_dsl(
        f"""
SUITE: Expect strict
URL: {http_server.base_url}
TEST: Missing
  GET: /missing
  EXPECT: openapi {spec.as_posix()} strict
"""
    )
    assert not result.ok
    assert "unmatched path/method" in output


def test_cli_contract_strict(http_server, tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"paths": {"/ping": {"get": {"responses": {"200": {}}}}}}), encoding="utf-8")
    suite = tmp_path / "contract.snaptest"
    suite.write_text(
        f"""
SUITE: CLI strict
OPTIONS: {{"OPENAPI": "{spec.as_posix()}"}}
URL: {http_server.base_url}
TEST: Missing
  GET: /nope
  EXPECT: status == 200
""",
        encoding="utf-8",
    )
    http_server.on("GET", "/nope", json={"ok": True})
    assert main([str(suite)]) == 0
    assert main([str(suite), "--contract-strict"]) == 1


def test_mock_templates_match_delay_and_exact_path(tmp_path):
    import requests as http

    spec = tmp_path / "mock.json"
    spec.write_text(
        json.dumps(
            {
                "routes": [
                    {"method": "GET", "path": "/users/me", "status": 200, "json": {"who": "exact"}},
                    {"method": "GET", "path": "/users/{id}", "status": 200, "json": {"who": "template"}},
                    {
                        "method": "POST",
                        "path": "/users",
                        "status": 201,
                        "json": {"ok": True},
                        "match": {"body": {"name": "Jane"}},
                    },
                    {
                        "method": "GET",
                        "path": "/search",
                        "status": 200,
                        "json": {"hit": True},
                        "match": {"query": {"q": "jane"}},
                    },
                    {"method": "GET", "path": "/slow", "status": 200, "json": {"ok": True}, "delay_ms": 40},
                    {"method": "HEAD", "path": "/x", "status": 204},
                    {
                        "method": "OPTIONS",
                        "path": "/cors",
                        "status": 204,
                        "headers": {"Access-Control-Allow-Origin": "*"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    with MockServer(load_mock_routes(spec), port=0) as server:
        assert http.get(f"{server.url}/users/me", timeout=2).json() == {"who": "exact"}
        assert http.get(f"{server.url}/users/7", timeout=2).json() == {"who": "template"}
        created = http.post(f"{server.url}/users", json={"name": "Jane"}, timeout=2)
        assert created.status_code == 201
        missed = http.post(f"{server.url}/users", json={"name": "Bob"}, timeout=2)
        assert missed.status_code == 404
        assert http.get(f"{server.url}/search", params={"q": "jane"}, timeout=2).json() == {"hit": True}
        assert http.get(f"{server.url}/search", params={"q": "nope"}, timeout=2).status_code == 404
        started = time.perf_counter()
        assert http.get(f"{server.url}/slow", timeout=2).json() == {"ok": True}
        assert time.perf_counter() - started >= 0.03
        head = http.head(f"{server.url}/x", timeout=2)
        assert head.status_code == 204
        options = http.options(f"{server.url}/cors", timeout=2)
        assert options.status_code == 204
        assert options.headers.get("Access-Control-Allow-Origin") == "*"


def test_head_and_request_options_do_not_break_suite_options(http_server):
    http_server.on("HEAD", "/x", status=204, text="")
    http_server.on("OPTIONS", "/cors", status=204, text="")
    suite = parse_dsl(
        """
SUITE: Verbs
OPTIONS: {"TIMEOUT": 1}
TEST: Verbs
  HEAD: /x
  REQUEST: OPTIONS /cors
  EXPECT: status == 204
"""
    )
    assert suite["options"]["TIMEOUT"] == 1
    assert suite["tests"][0]["steps"][0]["action"] == "HEAD"
    assert suite["tests"][0]["steps"][1]["action"] == "OPTIONS"
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Verbs
  HEAD: /x
  EXPECT: status == 204
TEST: Cors
  REQUEST: OPTIONS /cors
  EXPECT: status == 204
""",
            extra='OPTIONS: {"TIMEOUT": 2}',
        )
    )
    assert result.ok
    assert [item["method"] for item in http_server.requests] == ["HEAD", "OPTIONS"]


def test_snapapi_pytest_plugin(http_server, tmp_path, snapapi_run):
    http_server.on("GET", "/ping", json={"ok": True})
    suite = tmp_path / "plugin.snaptest"
    suite.write_text(
        f"""
SUITE: Plugin
URL: {http_server.base_url}
TEST: Ping
  GET: /ping
  EXPECT: status == 200
""",
        encoding="utf-8",
    )
    result = snapapi_run(suite)
    assert result.ok
    assert result.passed == 1


def test_oauth_pkce_s256_rfc7636_vector():
    from snapapi.engine import pkce_challenge_s256

    # RFC 7636 Appendix B
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce_challenge_s256(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_oauth_pkce_token_request_sends_verifier_not_challenge(http_server):
    from snapapi.engine import pkce_challenge_s256
    from urllib.parse import parse_qs

    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    expected_challenge = pkce_challenge_s256(verifier)
    bound = {"challenge": expected_challenge}

    def token_handler(record):
        form = parse_qs(record.get("body") or "")
        got_verifier = (form.get("code_verifier") or [None])[0]
        if "code_challenge" in form or "code_challenge_method" in form:
            return 400, {"Content-Type": "application/json"}, {"error": "challenge_on_token"}
        if not got_verifier:
            return 400, {"Content-Type": "application/json"}, {"error": "missing_verifier"}
        if pkce_challenge_s256(got_verifier) != bound["challenge"]:
            return 400, {"Content-Type": "application/json"}, {"error": "invalid_grant"}
        return 200, {"Content-Type": "application/json"}, {"access_token": "tok-pkce"}

    http_server.on("POST", "/oauth/token", handler=token_handler)
    http_server.on("GET", "/me", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: Pkce
  AUTH: oauth2 grant=authorization_code token_url={http_server.base_url}/oauth/token client_id=id redirect_uri=http://localhost/cb code=abc pkce=true code_verifier={verifier}
  GET: /me
  EXPECT: status == 200
""",
        )
    )
    assert result.ok
    form = parse_qs(http_server.requests[0]["body"])
    assert form.get("code_verifier") == [verifier]
    assert "code_challenge" not in form
    assert "code_challenge_method" not in form
    assert form.get("grant_type") == ["authorization_code"]
    # Same verifier as authorize-time challenge (cryptographic link)
    assert pkce_challenge_s256(form["code_verifier"][0]) == expected_challenge
    assert http_server.requests[1]["headers"].get("Authorization") == "Bearer tok-pkce"


def test_oauth_pkce_wrong_verifier_fails_token_exchange(http_server):
    from snapapi.engine import pkce_challenge_s256
    from urllib.parse import parse_qs

    authorize_verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    bound_challenge = pkce_challenge_s256(authorize_verifier)
    wrong_verifier = "a" * 43

    def token_handler(record):
        form = parse_qs(record.get("body") or "")
        got = (form.get("code_verifier") or [None])[0]
        if not got or pkce_challenge_s256(got) != bound_challenge:
            return 400, {"Content-Type": "application/json"}, {"error": "invalid_grant"}
        return 200, {"Content-Type": "application/json"}, {"access_token": "should-not-issue"}

    http_server.on("POST", "/oauth/token", handler=token_handler)
    http_server.on("GET", "/me", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: WrongVerifier
  AUTH: oauth2 grant=authorization_code token_url={http_server.base_url}/oauth/token client_id=id redirect_uri=http://localhost/cb code=abc pkce=true code_verifier={wrong_verifier}
  GET: /me
  EXPECT: status == 200
""",
        )
    )
    assert not result.ok
    assert "OAuth2 token request failed" in (result.tests[0].error or "")
    assert http_server.requests[0]["path"] == "/oauth/token"
    assert all(item["path"] != "/me" for item in http_server.requests)


def test_oauth_pkce_requires_code_verifier():
    from snapapi.exceptions import ParseError

    with pytest.raises(ParseError, match="code_verifier"):
        parse_dsl(
            """
SUITE: Pkce
URL: http://example.com
TEST: Missing verifier
  AUTH: oauth2 grant=authorization_code token_url=http://example.com/token client_id=id redirect_uri=http://localhost code=abc pkce=true
  GET: /me
  EXPECT: status == 200
"""
        )


def test_oauth_pkce_requires_code():
    import io

    from snapapi.engine import Engine

    suite = parse_dsl(
        """
SUITE: Pkce
URL: http://example.com
TEST: No code
  AUTH: oauth2 grant=authorization_code token_url=http://example.com/token client_id=id redirect_uri=http://localhost code_verifier=abc123pkceverifierVALUE00000000001 pkce=true
  GET: /me
  EXPECT: status == 200
"""
    )
    engine = Engine(suite, stream=io.StringIO(), timeout=1)
    result = engine.run()
    assert not result.ok
    assert "AUTH_CODE" in (result.tests[0].error or "")


def test_oauth_authorization_code_without_pkce_omits_verifier(http_server):
    from urllib.parse import parse_qs

    http_server.on("POST", "/oauth/token", json={"access_token": "tok-plain"})
    http_server.on("GET", "/me", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: NoPkce
  AUTH: oauth2 grant=authorization_code token_url={http_server.base_url}/oauth/token client_id=id redirect_uri=http://localhost/cb code=abc
  GET: /me
  EXPECT: status == 200
""",
        )
    )
    assert result.ok
    form = parse_qs(http_server.requests[0]["body"])
    assert "code_verifier" not in form
    assert "code_challenge" not in form


def test_code_verifier_is_redacted():
    from snapapi.redact import redact_body, redact_saved

    assert redact_saved("code_verifier", "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "***"
    body = redact_body("grant_type=authorization_code&code_verifier=supersecretverifiervalue")
    assert "supersecretverifiervalue" not in body
    assert "***" in body


def test_watch_detects_new_files(tmp_path):
    from snapapi.watch import snapshot_mtimes

    first = tmp_path / "a.snaptest"
    first.write_text("SUITE: A\n", encoding="utf-8")
    previous, changed = snapshot_mtimes([first])
    assert changed == []
    second = tmp_path / "b.snaptest"
    second.write_text("SUITE: B\n", encoding="utf-8")
    _, changed = snapshot_mtimes([first, second], previous)
    assert any(path.name == "b.snaptest" for path in changed)


def test_vcr_match_authorization(http_server, tmp_path):
    http_server.on("GET", "/secret", json={"ok": True})
    url = f"{http_server.base_url}/secret"
    default_one = cassette_key("GET", url, headers={"Authorization": "Bearer a", "Accept": "application/json"})
    default_two = cassette_key("GET", url, headers={"Authorization": "Bearer b", "Accept": "application/json"})
    assert default_one == default_two
    auth_one = cassette_key(
        "GET",
        url,
        headers={"Authorization": "Bearer a", "Accept": "application/json"},
        match=["query", "body", "accept", "authorization"],
    )
    auth_two = cassette_key(
        "GET",
        url,
        headers={"Authorization": "Bearer b", "Accept": "application/json"},
        match=["query", "body", "accept", "authorization"],
    )
    assert auth_one != auth_two
    cassette_dir = tmp_path / "cassettes"
    suite = tmp_path / "vcr.snaptest"
    suite.write_text(
        f"""
SUITE: VCR match
OPTIONS: {{"CASSETTE_DIR": "{cassette_dir.as_posix()}", "MODE": "record", "VCR-MATCH": ["query","body","accept","authorization"]}}
URL: {http_server.base_url}
TEST: One
  GET: /secret
  HEADER Authorization: Bearer one
  EXPECT: status == 200
""",
        encoding="utf-8",
    )
    assert main([str(suite)]) == 0
    assert list(cassette_dir.glob("*.json"))


def test_openapi_request_validation(http_server, tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "paths": {
                    "/users": {
                        "post": {
                            "parameters": [{"name": "verbose", "in": "query", "required": True}],
                            "requestBody": {
                                "required": True,
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "required": ["name"],
                                            "properties": {"name": {"type": "string"}},
                                        }
                                    }
                                },
                            },
                            "responses": {
                                "201": {
                                    "description": "created",
                                    "content": {
                                        "application/json": {
                                            "schema": {"type": "object"}
                                        }
                                    },
                                }
                            },
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    http_server.on("POST", "/users", status=201, json={"ok": True})
    result, _, output = run_dsl(
        f"""
SUITE: Request contract
OPTIONS: {{"OPENAPI": "{spec.as_posix()}"}}
URL: {http_server.base_url}
TEST: Missing field
  POST: /users
  QUERY: verbose=1
  BODY: {{"email": "a@b.c"}}
  EXPECT: status == 201
"""
    )
    assert result.ok
    assert "request schema validation failed" in output

    result, _, output = run_dsl(
        f"""
SUITE: Request contract
OPTIONS: {{"OPENAPI": "{spec.as_posix()}", "OPENAPI-STRICT": true}}
URL: {http_server.base_url}
TEST: Missing field
  POST: /users
  QUERY: verbose=1
  BODY: {{"email": "a@b.c"}}
  EXPECT: status == 201
"""
    )
    assert not result.ok
    assert "request schema validation failed" in output

    result, _, _ = run_dsl(
        f"""
SUITE: Request contract
OPTIONS: {{"OPENAPI": "{spec.as_posix()}", "OPENAPI-STRICT": true}}
URL: {http_server.base_url}
TEST: Valid
  POST: /users
  QUERY: verbose=1
  BODY: {{"name": "Jane"}}
  EXPECT: status == 201
"""
    )
    assert result.ok


def test_reruns_flaky_test(http_server):
    http_server.on("GET", "/flaky", json={"ok": True}, fail_times=1)
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Flaky
  GET: /flaky
  EXPECT: status == 200
""",
        )
    )
    assert not result.ok
    http_server.requests.clear()
    http_server.on("GET", "/flaky", json={"ok": True}, fail_times=1)
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Flaky
  GET: /flaky
  EXPECT: status == 200
""",
        ),
        reruns=2,
    )
    assert result.ok
    assert len(http_server.requests) == 2


def test_cli_reruns(http_server, tmp_path):
    http_server.on("GET", "/flaky", json={"ok": True}, fail_times=1)
    suite = tmp_path / "flaky.snaptest"
    suite.write_text(
        f"""
SUITE: Rerun
URL: {http_server.base_url}
TEST: Flaky
  GET: /flaky
  EXPECT: status == 200
""",
        encoding="utf-8",
    )
    assert main([str(suite), "--reruns", "2"]) == 0


def test_digest_auth_sends_authorization(http_server):
    def handler(record):
        auth = record["headers"].get("Authorization") or ""
        if auth.lower().startswith("digest "):
            return 200, {"Content-Type": "application/json"}, {"ok": True}
        return (
            401,
            {"WWW-Authenticate": 'Digest realm="snap", nonce="abcnonce", qop="auth", algorithm=MD5'},
            {"error": "auth"},
        )

    http_server.on("GET", "/digest", handler=handler)
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Digest
  GET: /digest
  AUTH: digest jane:secret
  EXPECT: status == 200
""",
        )
    )
    assert result.ok
    assert any(
        (item["headers"].get("Authorization") or "").lower().startswith("digest ")
        for item in http_server.requests
    )


def test_xpath_attribute(http_server):
    http_server.on(
        "GET",
        "/order",
        text='<Order id="1"><name>Jane</name></Order>',
        headers={"Content-Type": "application/xml"},
    )
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Xml
  GET: /order
  EXPECT: xpath //Order/@id == "1"
""",
        )
    )
    assert result.ok


def test_parse_collection_and_openapi_strict_and_digest():
    suite = parse_dsl(
        """
SUITE: Parse
OPTIONS: {"OPENAPI": "spec.yaml", "OPENAPI-STRICT": true}
TEST: A
  GET: /items
  AUTH: digest user:pass
  EXPECT: json $.items[?(@.id==1)].status == ["open"]
  EXPECT: json $.tags contains-all ["a","b"]
  EXPECT: json $.items each $.status == "active"
  EXPECT: openapi ./spec.yaml strict
  EXPECT: xpath //Order/@id == "1"
TEST: B
  REQUEST: OPTIONS /cors
  HEAD: /x
  EXPECT: status == 204
"""
    )
    checks = suite["tests"][0]["steps"][0]["checks"]
    assert checks[1]["operator"] == "CONTAINS-ALL"
    assert checks[2]["operator"] == "EACH"
    assert checks[3]["strict"] is True
    assert checks[4]["type"] == "XPATH"
    assert suite["tests"][0]["steps"][0]["digest"]["username"] == "user"
    assert suite["tests"][1]["steps"][0]["action"] == "OPTIONS"
    assert suite["tests"][1]["steps"][1]["action"] == "HEAD"
    assert suite["options"]["OPENAPI-STRICT"] is True
