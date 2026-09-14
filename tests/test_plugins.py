import pytest

from snapapi.cli import main, run_suites
from snapapi.exceptions import SnapAPIError
from snapapi.parser import TestParser
from snapapi.plugins import load_plugin
from snapapi.variables import interpolate
from tests.helpers import run_dsl


def _hmac_plugin(tmp_path):
    path = tmp_path / "hmac.py"
    path.write_text(
        "def hmac(payload, secret):\n"
        "    return 'sig-' + str(secret) + '-' + str(payload)\n",
        encoding="utf-8",
    )
    return path


def test_call_assigns_return_value(http_server, tmp_path):
    http_server.on("GET", "/ok", json={"ok": True})
    plugin = _hmac_plugin(tmp_path)
    result, engine, _ = run_dsl(
        f"""
SUITE: Signed
URL: {http_server.base_url}
SET: PAYLOAD body
TEST: Ping
  CALL: signature = hmac(${{PAYLOAD}}, ${{HMAC_SECRET}})
  GET: /ok
  HEADER: X-Signature: ${{signature}}
  EXPECT: status == 200
""",
        variables={"HMAC_SECRET": "dev"},
        plugins=load_plugin(f"{plugin}:hmac"),
    )
    assert result.ok
    assert engine.variables["signature"] == "sig-dev-body"
    assert http_server.requests[-1]["headers"].get("X-Signature") == "sig-dev-body"


def test_call_namespace_and_collision(http_server, tmp_path):
    http_server.on("GET", "/ok", json={"ok": True})
    ext = tmp_path / "extensions"
    ext.mkdir()
    (ext / "crypto.py").write_text(
        "def generate_token():\n    return 'crypto-token'\n",
        encoding="utf-8",
    )
    (ext / "auth.py").write_text(
        "def generate_token():\n    return 'auth-token'\n",
        encoding="utf-8",
    )
    suite = tmp_path / "one.sapi"
    suite.write_text(
        f"SUITE: One\nURL: {http_server.base_url}\n"
        "TEST: Ok\n"
        "  CALL: token = auth.generate_token()\n"
        "  GET: /ok\n  HEADER: X-Token: ${token}\n"
        "  EXPECT: status == 200\n",
        encoding="utf-8",
    )
    results = run_suites([suite])
    assert results[0].ok
    assert http_server.requests[-1]["headers"].get("X-Token") == "auth-token"

    suite.write_text(
        f"SUITE: One\nURL: {http_server.base_url}\n"
        "TEST: Ok\n"
        "  CALL: token = generate_token()\n"
        "  GET: /ok\n  EXPECT: status == 200\n",
        encoding="utf-8",
    )
    results = run_suites([suite])
    assert not results[0].ok
    assert "more than one extension" in (results[0].tests[0].error or results[0].error or "")


def test_call_object_body(http_server, tmp_path):
    http_server.on("POST", "/users", json={"ok": True})
    ext = tmp_path / "extensions"
    ext.mkdir()
    (ext / "testdata.py").write_text(
        "def generate_user():\n"
        "    return {'name': 'Jane', 'email': 'jane@example.com'}\n",
        encoding="utf-8",
    )
    suite = tmp_path / "one.sapi"
    suite.write_text(
        f"SUITE: Users\nURL: {http_server.base_url}\n"
        "TEST: Create\n"
        "  CALL: user = testdata.generate_user()\n"
        "  POST: /users\n"
        "  BODY: ${user}\n"
        "  EXPECT: status == 200\n",
        encoding="utf-8",
    )
    assert run_suites([suite])[0].ok
    assert http_server.requests[-1]["json"] == {"name": "Jane", "email": "jane@example.com"}


def test_yaml_extensions_list(http_server, tmp_path):
    http_server.on("GET", "/ok", json={"ok": True})
    ext = tmp_path / "extensions"
    ext.mkdir()
    (ext / "crypto.py").write_text(
        "def generate_signature(payload):\n    return 'sig-' + payload\n",
        encoding="utf-8",
    )
    (tmp_path / "snapapi.yaml").write_text(
        "extensions:\n  - extensions.crypto\n",
        encoding="utf-8",
    )
    suite = tmp_path / "one.sapi"
    suite.write_text(
        f"SUITE: One\nURL: {http_server.base_url}\n"
        "SET: PAYLOAD hi\n"
        "TEST: Ok\n"
        "  CALL: signature = crypto.generate_signature(${PAYLOAD})\n"
        "  GET: /ok\n  HEADER: X-Signature: ${signature}\n"
        "  EXPECT: status == 200\n",
        encoding="utf-8",
    )
    assert run_suites([suite])[0].ok
    assert http_server.requests[-1]["headers"].get("X-Signature") == "sig-hi"


def test_call_parse_shape():
    suite = TestParser().parse_text(
        "SUITE: X\nTEST: A\n  CALL: signature = crypto.generate_signature(${PAYLOAD}, ${TOKEN})\n  GET: /x\n",
        filename="x.sapi",
    )
    item = suite["tests"][0]["preps"][0]
    assert item["kind"] == "call"
    assert item["name"] == "signature"
    assert item["func"] == "crypto.generate_signature"
    assert item["args"] == ["${PAYLOAD}", "${TOKEN}"]


def test_unknown_helper_mentions_extensions():
    with pytest.raises(SnapAPIError, match="extensions/"):
        interpolate("${hmac()}", {})


def test_plugin_shadows_builtin():
    with pytest.raises(SnapAPIError, match="shadows a built-in"):
        from snapapi.plugins import ExtensionRegistry

        registry = ExtensionRegistry()
        registry.add(None, "uuid", lambda: "nope")


def test_run_suites_and_cli_plugin(http_server, tmp_path):
    http_server.on("GET", "/ok", json={"ok": True})
    suite = tmp_path / "one.sapi"
    suite.write_text(
        f"SUITE: One\nURL: {http_server.base_url}\n"
        "TEST: Ok\n  CALL: signature = hmac(n, ${HMAC_SECRET})\n"
        "  GET: /ok\n  HEADER: X-Signature: ${signature}\n"
        "  EXPECT: status == 200\n",
        encoding="utf-8",
    )
    plugin = _hmac_plugin(tmp_path)
    results = run_suites(
        [suite],
        extra_vars={"HMAC_SECRET": "cli"},
        plugins=load_plugin(f"{plugin}:hmac"),
    )
    assert results[0].ok
    assert main([str(suite), "--plugin", f"{plugin}:hmac", "-D", "HMAC_SECRET=cli"]) == 0
    assert http_server.requests[-1]["headers"].get("X-Signature") == "sig-cli-n"


def test_lint_unknown_and_loaded_helper(tmp_path):
    suite = tmp_path / "signed.sapi"
    suite.write_text(
        "SUITE: One\nURL: https://example.com\nTEST: Ok\n  GET: /x\n"
        "  HEADER: X-Signature: ${hmac()}\n  EXPECT: status == 200\n",
        encoding="utf-8",
    )
    plugin = _hmac_plugin(tmp_path)
    assert main(["lint", str(suite)]) == 2
    assert main(["lint", str(suite), "--plugin", f"{plugin}:hmac"]) == 0


def test_zero_arg_plugin():
    def stamp():
        return "ok"

    assert interpolate("${stamp()}", {}, plugins={"stamp": stamp}) == "ok"


def test_invoke_rejects_none_and_async():
    from snapapi.exceptions import CallError
    from snapapi.plugins import invoke_extension

    def nothing():
        return None

    with pytest.raises(CallError, match="returned None"):
        invoke_extension(nothing, "nothing", [])

    async def later():
        return "nope"

    with pytest.raises(CallError, match="async functions are not supported"):
        invoke_extension(later, "later", [])


def test_invoke_translates_exception_without_traceback():
    from snapapi.exceptions import CallError
    from snapapi.plugins import invoke_extension

    def boom(secret):
        raise ValueError("Invalid secret")

    with pytest.raises(CallError) as caught:
        invoke_extension(boom, "crypto.generate_signature", ["x"], test="Create Payment")
    text = str(caught.value)
    assert "CALL FAILED" in text
    assert "Function: crypto.generate_signature" in text
    assert "Test: Create Payment" in text
    assert "Error: Invalid secret" in text
    assert "Traceback" not in text
    assert caught.value.__cause__ is None


def test_invoke_copies_object_arguments():
    from snapapi.plugins import invoke_extension

    def bump(user):
        user["n"] += 1
        return user

    original = {"n": 1}
    out = invoke_extension(bump, "bump", [original])
    assert out == {"n": 2}
    assert original == {"n": 1}


def test_call_failure_prints_call_failed(http_server, tmp_path, capsys):
    http_server.on("GET", "/ok", json={"ok": True})
    ext = tmp_path / "extensions"
    ext.mkdir()
    (ext / "crypto.py").write_text(
        "def generate_signature(payload, secret):\n"
        "    raise ValueError('Invalid secret')\n",
        encoding="utf-8",
    )
    suite = tmp_path / "one.sapi"
    suite.write_text(
        f"SUITE: Pay\nURL: {http_server.base_url}\n"
        "TEST: Create Payment\n"
        "  CALL: signature = crypto.generate_signature(body, bad)\n"
        "  GET: /ok\n  EXPECT: status == 200\n",
        encoding="utf-8",
    )
    results = run_suites([suite])
    output = capsys.readouterr().out
    error = results[0].tests[0].error or ""
    assert not results[0].ok
    assert "CALL FAILED" in error
    assert "Function: crypto.generate_signature" in error
    assert "Test: Create Payment" in error
    assert "Error: Invalid secret" in error
    assert "CALL FAILED" in output
    assert "Create Payment" in output
    assert "Traceback" not in output
    assert "File " not in error
    assert "Traceback" not in error


def test_call_none_fails_the_test(http_server, tmp_path):
    http_server.on("GET", "/ok", json={"ok": True})
    ext = tmp_path / "extensions"
    ext.mkdir()
    (ext / "testdata.py").write_text(
        "def generate_user():\n    return None\n",
        encoding="utf-8",
    )
    suite = tmp_path / "one.sapi"
    suite.write_text(
        f"SUITE: Users\nURL: {http_server.base_url}\n"
        "TEST: Create\n"
        "  CALL: user = generate_user()\n"
        "  GET: /ok\n  EXPECT: status == 200\n",
        encoding="utf-8",
    )
    results = run_suites([suite])
    error = results[0].tests[0].error or ""
    assert not results[0].ok
    assert "CALL FAILED" in error
    assert "returned None" in error


def test_call_does_not_mutate_snapapi_variables(http_server):
    http_server.on("POST", "/users", json={"ok": True})

    def generate_user():
        return {"n": 1}

    def bump(user):
        user["n"] += 1
        return user

    result, engine, _ = run_dsl(
        f"""
SUITE: Users
URL: {http_server.base_url}
TEST: Create
  CALL: user = generate_user()
  CALL: nxt = bump(${{user}})
  POST: /users
  BODY: ${{user}}
  EXPECT: status == 200
""",
        plugins={"generate_user": generate_user, "bump": bump},
    )
    assert result.ok
    assert engine.variables["user"] == {"n": 1}
    assert engine.variables["nxt"] == {"n": 2}
    assert http_server.requests[-1]["json"] == {"n": 1}


def test_suite_call_failure_stops_run(http_server, tmp_path):
    http_server.on("GET", "/ok", json={"ok": True})
    ext = tmp_path / "extensions"
    ext.mkdir()
    (ext / "crypto.py").write_text(
        "def generate_signature():\n    raise ValueError('Invalid secret')\n",
        encoding="utf-8",
    )
    suite = tmp_path / "one.sapi"
    suite.write_text(
        f"SUITE: Pay\nURL: {http_server.base_url}\n"
        "CALL: signature = crypto.generate_signature()\n"
        "TEST: Create Payment\n"
        "  GET: /ok\n  EXPECT: status == 200\n",
        encoding="utf-8",
    )
    results = run_suites([suite])
    assert not results[0].ok
    assert results[0].tests == []
    error = results[0].error or ""
    assert "CALL FAILED" in error
    assert "Function: crypto.generate_signature" in error
    assert "Error: Invalid secret" in error
    assert "Test:" not in error
