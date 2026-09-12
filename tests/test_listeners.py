from snapapi.cli import main, run_suites
from snapapi.listeners import load_listener, notify
from tests.helpers import run_dsl


class Recorder:
    def __init__(self):
        self.events = []

    def start_suite(self, suite):
        self.events.append(("start_suite", suite.get("name")))

    def start_test(self, suite, name, tags):
        self.events.append(("start_test", name, list(tags)))

    def end_test(self, suite, test):
        self.events.append(("end_test", test.name, test.status))

    def end_suite(self, result):
        self.events.append(("end_suite", result.name, result.passed, result.failed))

    def report_written(self, kind, path):
        self.events.append(("report_written", kind, path))

    def close(self):
        self.events.append(("close",))


class Boom:
    def end_test(self, suite, test):
        raise RuntimeError("reporting down")


def test_listener_sees_each_test_and_suite(http_server):
    http_server.on("GET", "/ok", json={"ok": True})
    http_server.on("GET", "/nope", status=404, json={})
    recorder = Recorder()
    result, _, output = run_dsl(
        f"""
SUITE: Hooks
URL: {http_server.base_url}
TEST: Ok
  GET: /ok
  EXPECT: status == 200
TEST: Missing
  GET: /nope
  EXPECT: status == 200
""",
        listeners=[recorder],
    )
    assert result.passed == 1
    assert result.failed == 1
    assert recorder.events[0] == ("start_suite", "Hooks")
    assert ("start_test", "Ok", []) in recorder.events
    assert ("end_test", "Ok", "passed") in recorder.events
    assert ("end_test", "Missing", "failed") in recorder.events
    assert recorder.events[-1][:2] == ("end_suite", "Hooks")
    assert "reporting down" not in output


def test_listener_error_does_not_fail_suite(http_server):
    http_server.on("GET", "/ok", json={"ok": True})
    result, _, output = run_dsl(
        f"""
SUITE: Still ok
URL: {http_server.base_url}
TEST: Ok
  GET: /ok
  EXPECT: status == 200
""",
        listeners=[Boom()],
    )
    assert result.ok
    assert "listener Boom.end_test failed" in output


def test_notify_skips_missing_methods():
    events = []

    class OnlyClose:
        def close(self):
            events.append("close")

    notify([OnlyClose()], "end_test", {}, None)
    notify([OnlyClose()], "close")
    assert events == ["close"]


def test_load_listener_class_from_file(tmp_path):
    path = tmp_path / "my_listener.py"
    path.write_text(
        "class MyListener:\n"
        "    def __init__(self):\n"
        "        self.ok = True\n",
        encoding="utf-8",
    )
    loaded = load_listener(f"{path}:MyListener")
    assert loaded.ok is True


def test_run_suites_and_cli_listener(http_server, tmp_path):
    http_server.on("GET", "/ok", json={"ok": True})
    suite = tmp_path / "one.sapi"
    suite.write_text(
        f"SUITE: One\nURL: {http_server.base_url}\nTEST: Ok\n  GET: /ok\n  EXPECT: status == 200\n",
        encoding="utf-8",
    )
    recorder = Recorder()
    results = run_suites([suite], listeners=[recorder], close_listeners=True)
    assert results[0].ok
    assert ("end_test", "Ok", "passed") in recorder.events
    assert recorder.events[-1] == ("close",)

    listener = tmp_path / "rec.py"
    log = tmp_path / "events.txt"
    listener.write_text(
        "class Rec:\n"
        "    def __init__(self):\n"
        f"        self.path = {str(log)!r}\n"
        "    def end_test(self, suite, test):\n"
        "        with open(self.path, 'a', encoding='utf-8') as fh:\n"
        "            fh.write(f'{test.status} {test.name}\\n')\n"
        "    def report_written(self, kind, path):\n"
        "        with open(self.path, 'a', encoding='utf-8') as fh:\n"
        "            fh.write(f'wrote {kind}\\n')\n"
        "    def close(self):\n"
        "        with open(self.path, 'a', encoding='utf-8') as fh:\n"
        "            fh.write('close\\n')\n",
        encoding="utf-8",
    )
    html = tmp_path / "out.html"
    assert (
        main(
            [
                str(suite),
                "--listener",
                f"{listener}:Rec",
                "--report",
                f"html:{html}",
            ]
        )
        == 0
    )
    text = log.read_text(encoding="utf-8")
    assert "passed Ok" in text
    assert "wrote html" in text
    assert text.strip().endswith("close")
