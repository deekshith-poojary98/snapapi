"""I3 — WAIT nests inside each RETRY attempt (Model A).

Contract:
  RETRY N / ON / BACKOFF  → outer attempt budget
  WAIT TIMEOUT / BACKOFF  → inner poll within one attempt
"""

import time

from tests.helpers import run_dsl
from tests.test_engine import _suite


def test_wait_succeeds_within_single_retry_attempt(http_server):
    state = {"n": 0}

    def handler(record):
        state["n"] += 1
        if state["n"] < 3:
            return 200, {"Content-Type": "application/json"}, {"status": "pending"}
        return 200, {"Content-Type": "application/json"}, {"status": "ready"}

    http_server.on("GET", "/job", handler=handler)
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Ready
  GET: /job
  WAIT: json $.status == "ready" TIMEOUT 2s BACKOFF 0s
  EXPECT: status == 200 RETRY 3 BACKOFF 0s
""",
        ),
        retry_backoff=0,
    )
    assert result.ok
    assert state["n"] == 3


def test_wait_exhaust_then_retry_succeeds(http_server):
    """Each failed WAIT (TIMEOUT 0) consumes one RETRY; later attempt sees ready."""
    state = {"n": 0}

    def handler(record):
        state["n"] += 1
        if state["n"] < 3:
            return 200, {"Content-Type": "application/json"}, {"status": "pending"}
        return 200, {"Content-Type": "application/json"}, {"status": "ready"}

    http_server.on("GET", "/job", handler=handler)
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Eventually
  GET: /job
  WAIT: json $.status == "ready" TIMEOUT 0s BACKOFF 0s
  EXPECT: status == 200 RETRY 3 BACKOFF 0s
""",
        ),
        retry_backoff=0,
    )
    assert result.ok
    assert state["n"] == 3


def test_wait_exhaust_all_retries_fail(http_server):
    http_server.on("GET", "/job", json={"status": "pending"})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Never
  GET: /job
  WAIT: json $.status == "ready" TIMEOUT 0s BACKOFF 0s
  EXPECT: status == 200 RETRY 2 BACKOFF 0s
""",
        ),
        retry_backoff=0,
    )
    assert not result.ok
    assert len(http_server.requests) == 2


def test_retry_on_5xx_skips_retry_after_wait_timeout_on_200(http_server):
    """WAIT fails on HTTP 200 → ON 5xx must not start another RETRY attempt."""
    state = {"n": 0}

    def handler(record):
        state["n"] += 1
        return 200, {"Content-Type": "application/json"}, {"status": "pending"}

    http_server.on("GET", "/job", handler=handler)
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: NoRetry
  GET: /job
  WAIT: json $.status == "ready" TIMEOUT 0s BACKOFF 0s
  EXPECT: status == 200 RETRY 5 ON 5xx BACKOFF 0s
""",
        ),
        retry_backoff=0,
    )
    assert not result.ok
    assert state["n"] == 1


def test_retry_on_5xx_retries_when_wait_sees_5xx(http_server):
    state = {"n": 0}

    def handler(record):
        state["n"] += 1
        if state["n"] == 1:
            return 503, {"Content-Type": "application/json"}, {"status": "pending"}
        return 200, {"Content-Type": "application/json"}, {"status": "ready"}

    http_server.on("GET", "/job", handler=handler)
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: ServiceUp
  GET: /job
  WAIT: json $.status == "ready" TIMEOUT 0s BACKOFF 0s
  EXPECT: status == 200 RETRY 3 ON 5xx BACKOFF 0s
""",
        ),
        retry_backoff=0,
    )
    assert result.ok
    assert state["n"] == 2


def test_retry_backoff_used_between_attempts_not_wait_backoff(http_server, monkeypatch):
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("snapapi.engine.time.sleep", fake_sleep)
    http_server.on("GET", "/job", json={"status": "pending"})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Backoff
  GET: /job
  WAIT: json $.status == "ready" TIMEOUT 0s BACKOFF 0s
  EXPECT: status == 200 RETRY 3 BACKOFF 0.25s
""",
        ),
        retry_backoff=0,
    )
    assert not result.ok
    retry_delays = [s for s in sleeps if abs(s - 0.25) < 1e-9]
    assert len(retry_delays) == 2


def test_expect_fail_after_wait_uses_retry(http_server):
    """WAIT passes, EXPECT fails → outer RETRY; second attempt passes."""
    state = {"n": 0}

    def handler(record):
        state["n"] += 1
        if state["n"] == 1:
            return 200, {"Content-Type": "application/json"}, {"status": "ready", "ok": False}
        return 200, {"Content-Type": "application/json"}, {"status": "ready", "ok": True}

    http_server.on("GET", "/job", handler=handler)
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: AfterWait
  GET: /job
  WAIT: json $.status == "ready" TIMEOUT 2s BACKOFF 0s
  EXPECT: json $.ok == true RETRY 3 BACKOFF 0s
""",
        ),
        retry_backoff=0,
    )
    assert result.ok
    assert state["n"] == 2


def test_wait_retry_save_only_on_success(http_server):
    state = {"n": 0}

    def handler(record):
        state["n"] += 1
        if state["n"] == 1:
            return 200, {"Content-Type": "application/json"}, {"status": "ready", "id": "leaked"}
        return 200, {"Content-Type": "application/json"}, {"status": "ready", "id": "ok", "name": "x"}

    http_server.on("GET", "/job", handler=handler)
    result, engine, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Save
  GET: /job
  WAIT: json $.status == "ready" TIMEOUT 2s BACKOFF 0s
  EXPECT: status == 200 RETRY 3 BACKOFF 0s
  SAVE: A FROM $.id
  SAVE: B FROM $.name
""",
        ),
        retry_backoff=0,
    )
    assert result.ok
    assert engine.variables["A"] == "ok"
    assert engine.variables["B"] == "x"


def test_wait_only_unchanged_without_retry(http_server):
    state = {"n": 0}

    def handler(record):
        state["n"] += 1
        if state["n"] < 3:
            return 200, {"Content-Type": "application/json"}, {"status": "pending"}
        return 200, {"Content-Type": "application/json"}, {"status": "ready"}

    http_server.on("GET", "/job", handler=handler)
    result, _, _ = run_dsl(
        _suite(
            http_server,
            """
TEST: Poll
  GET: /job
  WAIT: json $.status == "ready" TIMEOUT 2s BACKOFF 0s
  EXPECT: status == 200
""",
        )
    )
    assert result.ok
    assert state["n"] == 3
