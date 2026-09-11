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


def test_cli_reports(http_server, tmp_path):
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
OPTIONS: {"STOP-ON-FAILURE": false}
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


def test_cli_missing_path(tmp_path):
    assert main([str(tmp_path / "nope.snaptest")]) == 2


def test_cli_stop_on_failure_flag(http_server, tmp_path):
    http_server.on("GET", "/fail", status=500, json={})
    http_server.on("GET", "/ok", json={"ok": True})
    suite = tmp_path / "suite.snaptest"
    _write_suite(suite, http_server, """
OPTIONS: {"STOP-ON-FAILURE": false}
TEST: First
  REQUEST: GET /fail
  EXPECT: STATUS 200
TEST: Second
  REQUEST: GET /ok
  EXPECT: STATUS 200
""")
    assert main([str(suite), "--stop-on-failure"]) == 1
    assert [item["path"] for item in http_server.requests] == ["/fail"]


def test_parse_dsl_helper_still_works():
    suite = parse_dsl("SUITE: X\nTEST: T\n  REQUEST: GET /z\n  EXPECT: STATUS 200\n")
    assert suite["name"] == "X"
