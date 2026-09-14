from __future__ import annotations

from snapapi.cli import main
from snapapi.convert import convert_curl, curl_to_sapi, parse_curl, value_to_snap_vars
from snapapi.exceptions import SnapAPIError
from tests.helpers import parse_dsl


def test_cli_convert_file_and_output(tmp_path, capsys):
    path = tmp_path / "req.sh"
    path.write_text(
        "curl 'https://api.example.com/users?page=1' -H 'Authorization: Bearer abc'\n",
        encoding="utf-8",
    )
    out = tmp_path / "users.sapi"
    code = main(["convert", str(path), "-o", str(out), "--expect", "200", "-q"])
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "EXPECT: status == 200" in text
    assert "AUTH: bearer abc" in text
    assert capsys.readouterr().out == ""


def test_cli_convert_missing_source_fails():
    assert main(["convert"]) == 2


def test_cli_convert_stdout():
    assert (
        main(
            [
                "convert",
                "curl -sS https://api.example.com/health -H 'Authorization: Bearer $TOKEN'",
            ]
        )
        == 0
    )


def test_multiple_curls_one_suite(tmp_path):
    script = tmp_path / "requests.sh"
    script.write_text(
        "curl https://api.example.com/users\n"
        "curl -X POST https://api.example.com/orders -d '{\"id\":1}'\n",
        encoding="utf-8",
    )
    result = convert_curl(str(script))
    assert result.text.count("SUITE:") == 1
    assert result.text.count("TEST:") == 2
    parse_dsl(result.text)


def test_value_to_snap_vars():
    assert value_to_snap_vars("Bearer $TOKEN") == "Bearer ${TOKEN}"
    assert value_to_snap_vars("Bearer ${TOKEN}") == "Bearer ${TOKEN}"


def test_glued_method_flag():
    assert parse_curl("curl -XPOST https://api.example.com/items -d '{}'").method == "POST"


def test_combined_short_flags():
    result = curl_to_sapi("curl -sSL https://api.example.com/go")
    assert result.warnings == []
    assert "FOLLOW-REDIRECTS: true" in result.text


def test_convert_rejects_empty():
    try:
        curl_to_sapi("echo hi")
        assert False, "expected SnapAPIError"
    except SnapAPIError as exc:
        assert "No curl command" in str(exc)


def test_json_flag_sets_headers_and_post():
    result = curl_to_sapi("curl https://api.example.com/users --json '{\"name\":\"Jane\"}'")
    assert "POST: /users" in result.text
    assert "HEADER Content-Type: application/json" in result.text
    assert "HEADER Accept: application/json" in result.text
    assert 'BODY: {"name": "Jane"}' in result.text
    parse_dsl(result.text)
