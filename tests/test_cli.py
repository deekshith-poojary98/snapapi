import json

from snapapi.cli import main
from tests.helpers import parse_dsl


def _write_suite(path, server, body):
    path.write_text(
        f"""
SUITE: CLI
URL: {server.base_url}
{body}
""",
        encoding="utf-8",
    )


def test_cli_exit_zero_on_pass(http_server, tmp_path):
    http_server.on("GET", "/ok", json={"ok": True})
    suite = tmp_path / "ok.snaptest"
    _write_suite(suite, http_server, """
TEST: Ok
  REQUEST: GET /ok
  EXPECT: STATUS 200
""")
    assert main([str(suite)]) == 0


def test_cli_exit_nonzero_on_failure(http_server, tmp_path):
    http_server.on("GET", "/ok", status=500, json={"ok": False})
    suite = tmp_path / "bad.snaptest"
    _write_suite(suite, http_server, """
TEST: Bad
  REQUEST: GET /ok
  EXPECT: STATUS 200
""")
    assert main([str(suite)]) == 1


def test_cli_parse_error_exit_two(tmp_path):
    suite = tmp_path / "bad.snaptest"
    suite.write_text("SUITE: X\nNOTAKEYWORD: nope\n", encoding="utf-8")
    assert main([str(suite)]) == 2


def test_cli_name_filter(http_server, tmp_path):
    http_server.on("GET", "/users", json={"ok": True})
    http_server.on("GET", "/health", json={"ok": True})
    suite = tmp_path / "names.snaptest"
    _write_suite(suite, http_server, """
TEST: Users
  REQUEST: GET /users
  EXPECT: STATUS 200
TEST: Health
  REQUEST: GET /health
  EXPECT: STATUS 200
""")
    assert main([str(suite), "--name", "Users"]) == 0
    assert [item["path"] for item in http_server.requests] == ["/users"]


def test_cli_name_filter_unknown(tmp_path):
    suite = tmp_path / "names.snaptest"
    suite.write_text(
        """
SUITE: CLI
TEST: Users
  REQUEST: GET /users
  EXPECT: STATUS 200
""",
        encoding="utf-8",
    )
    assert main([str(suite), "--name", "Missing"]) == 2


def test_cli_tag_filter(http_server, tmp_path):
    http_server.on("GET", "/users", json={"ok": True})
    http_server.on("GET", "/health", json={"ok": True})
    suite = tmp_path / "tags.snaptest"
    _write_suite(suite, http_server, """
TEST: Users
TAG: user
  REQUEST: GET /users
  EXPECT: STATUS 200
TEST: Health
TAG: health
  REQUEST: GET /health
  EXPECT: STATUS 200
""")
    assert main([str(suite), "--tag", "user"]) == 0
    assert [item["path"] for item in http_server.requests] == ["/users"]


def test_cli_env_interpolation(http_server, tmp_path):
    http_server.on("GET", "/secure", json={"ok": True})
    env = tmp_path / "test.env"
    env.write_text("TOKEN=abc123\n", encoding="utf-8")
    suite = tmp_path / "env.snaptest"
    _write_suite(suite, http_server, """
TEST: Auth
  REQUEST: GET /secure
  HEADERS: {"Authorization": "Bearer ${TOKEN}"}
  EXPECT: STATUS 200
""")
    assert main([str(suite), "--env", str(env)]) == 0
    assert http_server.requests[0]["headers"].get("Authorization") == "Bearer abc123"


def test_cli_discovers_sibling_env(http_server, tmp_path):
    http_server.on("GET", "/secure", json={"ok": True})
    suite = tmp_path / "auth.sapi"
    _write_suite(
        suite,
        http_server,
        """
TEST: Auth
  REQUEST: GET /secure
  HEADERS: {"Authorization": "Bearer ${TOKEN}"}
  EXPECT: STATUS 200
""",
    )
    (tmp_path / "auth.env").write_text("TOKEN=from-sibling\n", encoding="utf-8")
    assert main([str(suite)]) == 0
    assert http_server.requests[0]["headers"].get("Authorization") == "Bearer from-sibling"


def test_cli_reports(http_server, tmp_path, capsys):
    http_server.on("GET", "/ok", json={"ok": True})
    suite = tmp_path / "ok.snaptest"
    _write_suite(suite, http_server, """
TEST: Ok
TAG: smoke
  REQUEST: GET /ok
  EXPECT: STATUS 200
""")
    json_path = tmp_path / "report.json"
    junit_path = tmp_path / "report.xml"
    code = main([
        str(suite),
        "--report", f"json:{json_path}",
        "--report", f"junit:{junit_path}",
    ])
    assert code == 0
    printed = capsys.readouterr().out
    assert f"Report (json): {json_path.resolve()}" in printed
    assert f"Report (junit): {junit_path.resolve()}" in printed
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["passed"] == 1
    assert payload["suites"][0]["tests"][0]["name"] == "Ok"
    xml = junit_path.read_text(encoding="utf-8")
    assert "<testsuite" in xml
    assert 'name="Ok"' in xml
    assert "<failure" not in xml


def test_cli_reports_include_failures(http_server, tmp_path):
    http_server.on("GET", "/ok", status=404, json={})
    suite = tmp_path / "bad.snaptest"
    _write_suite(suite, http_server, """
TEST: Missing
  REQUEST: GET /ok
  EXPECT: STATUS 200
""")
    json_path = tmp_path / "report.json"
    junit_path = tmp_path / "out" / "report.xml"
    assert main([
        str(suite),
        "--report", f"json:{json_path}",
        "--report", f"junit:{junit_path}",
    ]) == 1
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["failed"] == 1
    xml = junit_path.read_text(encoding="utf-8")
    assert "<failure" in xml


def test_cli_multi_file_and_directory(http_server, tmp_path):
    http_server.on("GET", "/a", json={"ok": True})
    http_server.on("GET", "/b", json={"ok": True})
    folder = tmp_path / "suites"
    folder.mkdir()
    _write_suite(folder / "a.snaptest", http_server, """
TEST: A
  REQUEST: GET /a
  EXPECT: STATUS 200
""")
    other = tmp_path / "b.snaptest"
    _write_suite(other, http_server, """
TEST: B
  REQUEST: GET /b
  EXPECT: STATUS 200
""")
    assert main([str(folder), str(other)]) == 0
    assert sorted(item["path"] for item in http_server.requests) == ["/a", "/b"]


def test_cli_collects_sapi_extension(http_server, tmp_path):
    from snapapi.cli import collect_files

    http_server.on("GET", "/a", json={"ok": True})
    folder = tmp_path / "suites"
    folder.mkdir()
    _write_suite(folder / "a.sapi", http_server, """
TEST: A
  REQUEST: GET /a
  EXPECT: STATUS 200
""")
    files = collect_files([str(folder)])
    assert [path.name for path in files] == ["a.sapi"]
    assert main([str(folder)]) == 0


def test_cli_missing_path(tmp_path):
    assert main([str(tmp_path / "nope.snaptest")]) == 2


def test_cli_stop_on_failure_flag(http_server, tmp_path):
    http_server.on("GET", "/fail", status=500, json={})
    http_server.on("GET", "/ok", json={"ok": True})
    suite = tmp_path / "suite.snaptest"
    _write_suite(suite, http_server, """
TEST: First
  REQUEST: GET /fail
  EXPECT: STATUS 200
TEST: Second
  REQUEST: GET /ok
  EXPECT: STATUS 200
""")
    assert main([str(suite), "--stop-on-failure"]) == 1
    assert [item["path"] for item in http_server.requests] == ["/fail"]

    http_server.requests.clear()
    assert main([str(suite)]) == 1
    assert [item["path"] for item in http_server.requests] == ["/fail", "/ok"]


def test_cli_exitfirst_alias(http_server, tmp_path):
    http_server.on("GET", "/fail", status=500, json={})
    http_server.on("GET", "/ok", json={"ok": True})
    suite = tmp_path / "suite.snaptest"
    _write_suite(suite, http_server, """
TEST: First
  GET: /fail
  EXPECT: status == 200
TEST: Second
  GET: /ok
  EXPECT: status == 200
""")
    assert main([str(suite), "-x"]) == 1
    assert [item["path"] for item in http_server.requests] == ["/fail"]


def test_cli_keyword_and_exclude(http_server, tmp_path, capsys):
    http_server.on("GET", "/login", json={"ok": True})
    http_server.on("GET", "/health", json={"ok": True})
    http_server.on("GET", "/slow", json={"ok": True})
    suite = tmp_path / "filters.snaptest"
    _write_suite(suite, http_server, """
TEST: Login
TAG: auth smoke
  GET: /login
  EXPECT: status == 200
TEST: Health
TAG: health
  GET: /health
  EXPECT: status == 200
TEST: Slow path
TAG: smoke slow
  GET: /slow
  EXPECT: status == 200
""")
    assert main([str(suite), "-k", "Login or Health"]) == 0
    assert [item["path"] for item in http_server.requests] == ["/login", "/health"]

    http_server.requests.clear()
    assert main([str(suite), "-m", "smoke and not slow"]) == 0
    assert [item["path"] for item in http_server.requests] == ["/login"]

    http_server.requests.clear()
    assert main([str(suite), "--exclude", "slow"]) == 0
    assert [item["path"] for item in http_server.requests] == ["/login", "/health"]

    capsys.readouterr()
    assert main([str(suite), "--collect-only", "-m", "smoke"]) == 0
    collected = capsys.readouterr().out
    assert "Login" in collected
    assert "Slow path" in collected
    assert "Health" not in collected
    assert "2 tests collected" in collected


def test_cli_variable_and_version(http_server, tmp_path, capsys):
    http_server.on("GET", "/secure", json={"ok": True})
    suite = tmp_path / "vars.snaptest"
    _write_suite(suite, http_server, """
TEST: Auth
  GET: /secure
  HEADER Authorization: Bearer ${TOKEN}
  EXPECT: status == 200
""")
    assert main([str(suite), "-D", "TOKEN=abc123"]) == 0
    assert http_server.requests[0]["headers"].get("Authorization") == "Bearer abc123"

    assert main(["--version"]) == 0
    assert "snapapi " in capsys.readouterr().out


def test_cli_maxfail(http_server, tmp_path):
    http_server.on("GET", "/a", status=500, json={})
    http_server.on("GET", "/b", status=500, json={})
    http_server.on("GET", "/c", json={"ok": True})
    suite = tmp_path / "maxfail.snaptest"
    _write_suite(suite, http_server, """
TEST: A
  GET: /a
  EXPECT: status == 200
TEST: B
  GET: /b
  EXPECT: status == 200
TEST: C
  GET: /c
  EXPECT: status == 200
""")
    assert main([str(suite), "--maxfail", "2"]) == 1
    assert [item["path"] for item in http_server.requests] == ["/a", "/b"]


def test_parse_dsl_helper_still_works():
    suite = parse_dsl("SUITE: X\nTEST: T\n  REQUEST: GET /z\n  EXPECT: STATUS 200\n")
    assert suite["name"] == "X"


def test_cli_new_syntax(http_server, tmp_path):
    http_server.on("GET", "/secure", json={"ok": True})
    env = tmp_path / "test.env"
    env.write_text("TOKEN=abc123\n", encoding="utf-8")
    suite = tmp_path / "new.snaptest"
    _write_suite(
        suite,
        http_server,
        """
TIMEOUT: 5
TEST: Auth
TAG: smoke
  GET: /secure
  AUTH: bearer ${TOKEN}
  QUERY: page=1
  EXPECT: status == 200
  EXPECT: body contains ok
""",
    )
    assert main([str(suite), "--env", str(env)]) == 0
    assert http_server.requests[0]["headers"].get("Authorization") == "Bearer abc123"
    assert "page=1" in http_server.requests[0]["query"]
