"""I1 — SAVE batch atomicity: extract-all-then-commit per successful attempt."""

from tests.helpers import run_dsl
from tests.test_engine import _suite


def test_partial_save_failure_commits_nothing(http_server):
    http_server.on("GET", "/item", json={"id": 7, "name": "widget"})
    result, engine, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Partial
  GET: /item
  EXPECT: status == 200
  SAVE: A FROM $.id
  SAVE: B FROM $.missing
""",
        )
    )
    assert not result.ok
    assert "A" not in engine.variables
    assert "B" not in engine.variables


def test_all_saves_commit_together(http_server):
    http_server.on("GET", "/item", json={"id": 7, "name": "widget"})
    result, engine, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Both
  GET: /item
  EXPECT: status == 200
  SAVE: A FROM $.id
  SAVE: B FROM $.name
""",
        )
    )
    assert result.ok
    assert engine.variables["A"] == 7
    assert engine.variables["B"] == "widget"


def test_failed_save_does_not_leak_into_later_test(http_server):
    """If SAVE A committed before SAVE B failed, Test 2 would resolve ${leaked}."""
    http_server.on("GET", "/item", json={"id": 99})
    http_server.on("GET", "/probe", json={"ok": True})
    result, engine, output = run_dsl(
        _suite(
            http_server,
            """
TEST: Leak attempt
  GET: /item
  EXPECT: status == 200
  SAVE: leaked FROM $.id
  SAVE: broken FROM $.missing

TEST: Must not see leaked
  GET: /probe
  HEADER X-Leaked: ${leaked}
  EXPECT: status == 200
""",
        ),
        stop_on_failure=False,
    )
    assert not result.ok
    assert result.failed == 2
    assert "leaked" not in engine.variables
    assert "broken" not in engine.variables
    assert "Undefined variable ${leaked}" in output


def test_mixed_batch_failure_commits_nothing(http_server):
    http_server.on(
        "GET",
        "/item",
        json={"id": 1, "name": "n", "nested": {"x": 2}},
    )
    result, engine, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Mixed
  GET: /item
  EXPECT: status == 200
  SAVE: A FROM $.id
  SAVE: B FROM $.name
  SAVE: C FROM $.nested.x
  SAVE: D FROM $.absent.path
""",
        )
    )
    assert not result.ok
    for name in ("A", "B", "C", "D"):
        assert name not in engine.variables


def test_duplicate_save_names_last_wins(http_server):
    """Duplicate SAVE names in one batch: declaration order, last write wins."""
    http_server.on("GET", "/item", json={"id": 1, "name": "second"})
    result, engine, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Dup
  GET: /item
  EXPECT: status == 200
  SAVE: A FROM $.id
  SAVE: A FROM $.name
""",
        )
    )
    assert result.ok
    assert engine.variables["A"] == "second"


def test_nested_jsonpath_save(http_server):
    http_server.on("GET", "/item", json={"data": {"user": {"id": "u-9"}}})
    result, engine, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Nested
  GET: /item
  EXPECT: status == 200
  SAVE: userId FROM $.data.user.id
""",
        )
    )
    assert result.ok
    assert engine.variables["userId"] == "u-9"


def test_null_value_save_commits(http_server):
    http_server.on("GET", "/item", json={"id": None, "name": "ok"})
    result, engine, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Null
  GET: /item
  EXPECT: status == 200
  SAVE: A FROM $.id
  SAVE: B FROM $.name
""",
        )
    )
    assert result.ok
    assert "A" in engine.variables
    assert engine.variables["A"] is None
    assert engine.variables["B"] == "ok"


def test_failing_expect_skips_save_commit(http_server):
    http_server.on("GET", "/item", json={"id": 7})
    result, engine, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Expect first
  GET: /item
  SAVE: A FROM $.id
  EXPECT: status == 500
""",
        )
    )
    assert not result.ok
    assert "A" not in engine.variables


def test_retry_failed_attempt_does_not_partially_commit(http_server):
    """First attempt passes checks but SAVE batch fails; must not publish A=leaked."""
    state = {"n": 0}

    def handler(record):
        state["n"] += 1
        if state["n"] == 1:
            return 200, {"Content-Type": "application/json"}, {"id": "leaked"}
        return 200, {"Content-Type": "application/json"}, {"id": "ok", "name": "final"}

    http_server.on("GET", "/item", handler=handler)
    result, engine, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Retry save
  GET: /item
  EXPECT: status == 200 RETRY 3 BACKOFF 0s
  SAVE: A FROM $.id
  SAVE: B FROM $.name
""",
        ),
        retry_backoff=0,
    )
    assert result.ok
    assert state["n"] == 2
    assert engine.variables["A"] == "ok"
    assert engine.variables["B"] == "final"


def test_retry_exhausted_save_failure_commits_nothing(http_server):
    http_server.on("GET", "/item", json={"id": "leaked"})
    result, engine, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Exhausted
  GET: /item
  EXPECT: status == 200 RETRY 2 BACKOFF 0s
  SAVE: A FROM $.id
  SAVE: B FROM $.missing
""",
        ),
        retry_backoff=0,
    )
    assert not result.ok
    assert "A" not in engine.variables
    assert "B" not in engine.variables


def test_wait_then_failed_save_commits_nothing(http_server):
    state = {"n": 0}

    def handler(record):
        state["n"] += 1
        if state["n"] < 2:
            return 200, {"Content-Type": "application/json"}, {"status": "pending"}
        return 200, {"Content-Type": "application/json"}, {"status": "ready", "id": "leaked"}

    http_server.on("GET", "/poll", handler=handler)
    result, engine, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Wait then save
  GET: /poll
  WAIT: json $.status == "ready" TIMEOUT 0.3s BACKOFF 0s
  EXPECT: status == 200
  SAVE: A FROM $.id
  SAVE: B FROM $.missing
""",
        )
    )
    assert not result.ok
    assert state["n"] == 2  # pending → ready, then SAVE fails the attempt (no WAIT re-poll)
    assert "A" not in engine.variables
    assert "B" not in engine.variables


def test_wait_then_successful_save_batch(http_server):
    state = {"n": 0}

    def handler(record):
        state["n"] += 1
        if state["n"] < 2:
            return 200, {"Content-Type": "application/json"}, {"status": "pending"}
        return (
            200,
            {"Content-Type": "application/json"},
            {"status": "ready", "id": 3, "name": "done"},
        )

    http_server.on("GET", "/poll", handler=handler)
    result, engine, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Wait save ok
  GET: /poll
  WAIT: json $.status == "ready" TIMEOUT 2s BACKOFF 0s
  EXPECT: status == 200
  SAVE: A FROM $.id
  SAVE: B FROM $.name
""",
        )
    )
    assert result.ok
    assert engine.variables["A"] == 3
    assert engine.variables["B"] == "done"
