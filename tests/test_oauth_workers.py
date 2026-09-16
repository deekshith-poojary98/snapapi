"""I6 — OAuth cache × workers: single-flight token acquisition."""

import io
import threading
import time

from snapapi.engine import Engine
from tests.helpers import parse_dsl, run_dsl
from tests.test_engine import _suite


def test_workers_single_flight_token_request(http_server):
    """N workers, same cache key, empty cache → exactly one token hit."""
    token_hits = {"n": 0}
    lock = threading.Lock()

    def token_handler(record):
        with lock:
            token_hits["n"] += 1
        time.sleep(0.05)  # widen the race window
        return 200, {"Content-Type": "application/json"}, {"access_token": "shared-tok"}

    def api_handler(record):
        assert record["headers"].get("Authorization") == "Bearer shared-tok"
        return 200, {"Content-Type": "application/json"}, {"ok": True}

    http_server.on("POST", "/oauth/token", handler=token_handler)
    http_server.on("GET", "/secure", handler=api_handler)
    auth = (
        f"AUTH: oauth2 token_url={http_server.base_url}/oauth/token "
        f"client_id=id client_secret=s"
    )
    result, _, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: A
  GET: /secure
  {auth}
  EXPECT: status == 200
TEST: B
  GET: /secure
  {auth}
  EXPECT: status == 200
TEST: C
  GET: /secure
  {auth}
  EXPECT: status == 200
TEST: D
  GET: /secure
  {auth}
  EXPECT: status == 200
""",
        ),
        workers=4,
    )
    assert result.ok
    assert token_hits["n"] == 1
    assert len([r for r in http_server.requests if r["path"] == "/secure"]) == 4


def test_oauth_cache_key_separates_client_ids(http_server):
    token_hits = {"n": 0}
    lock = threading.Lock()

    def token_handler(record):
        with lock:
            token_hits["n"] += 1
        body = record.get("body") or ""
        if "client_id=a" in body:
            return 200, {"Content-Type": "application/json"}, {"access_token": "tok-a"}
        return 200, {"Content-Type": "application/json"}, {"access_token": "tok-b"}

    http_server.on("POST", "/oauth/token", handler=token_handler)
    http_server.on("GET", "/a", json={"ok": True})
    http_server.on("GET", "/b", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: ClientA
  GET: /a
  AUTH: oauth2 token_url={http_server.base_url}/oauth/token client_id=a client_secret=s
  EXPECT: status == 200
TEST: ClientB
  GET: /b
  AUTH: oauth2 token_url={http_server.base_url}/oauth/token client_id=b client_secret=s
  EXPECT: status == 200
""",
        ),
        workers=2,
    )
    assert result.ok
    assert token_hits["n"] == 2
    auths = {
        r["path"]: r["headers"].get("Authorization")
        for r in http_server.requests
        if r["path"] in ("/a", "/b")
    }
    assert auths["/a"] == "Bearer tok-a"
    assert auths["/b"] == "Bearer tok-b"


def test_oauth_cache_key_separates_token_urls(http_server):
    hits = {"t1": 0, "t2": 0}

    def t1(record):
        hits["t1"] += 1
        return 200, {"Content-Type": "application/json"}, {"access_token": "one"}

    def t2(record):
        hits["t2"] += 1
        return 200, {"Content-Type": "application/json"}, {"access_token": "two"}

    http_server.on("POST", "/oauth/t1", handler=t1)
    http_server.on("POST", "/oauth/t2", handler=t2)
    http_server.on("GET", "/x", json={"ok": True})
    result, _, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: One
  GET: /x
  AUTH: oauth2 token_url={http_server.base_url}/oauth/t1 client_id=id client_secret=s
  EXPECT: status == 200
TEST: Two
  GET: /x
  AUTH: oauth2 token_url={http_server.base_url}/oauth/t2 client_id=id client_secret=s
  EXPECT: status == 200
""",
        )
    )
    assert result.ok
    assert hits == {"t1": 1, "t2": 1}


def test_oauth_valid_cache_reused_without_second_hit(http_server):
    hits = {"n": 0}

    def token_handler(record):
        hits["n"] += 1
        return 200, {"Content-Type": "application/json"}, {"access_token": "tok", "expires_in": 3600}

    http_server.on("POST", "/oauth/token", handler=token_handler)
    http_server.on("GET", "/secure", json={"ok": True})
    auth = (
        f"AUTH: oauth2 token_url={http_server.base_url}/oauth/token "
        f"client_id=id client_secret=s"
    )
    result, _, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: First
  GET: /secure
  {auth}
  EXPECT: status == 200
TEST: Second
  GET: /secure
  {auth}
  EXPECT: status == 200
""",
        )
    )
    assert result.ok
    assert hits["n"] == 1


def test_oauth_expired_token_refetches(http_server):
    hits = {"n": 0}

    def token_handler(record):
        hits["n"] += 1
        return (
            200,
            {"Content-Type": "application/json"},
            {"access_token": f"tok-{hits['n']}", "expires_in": 0},
        )

    http_server.on("POST", "/oauth/token", handler=token_handler)
    http_server.on("GET", "/secure", json={"ok": True})
    auth = (
        f"AUTH: oauth2 token_url={http_server.base_url}/oauth/token "
        f"client_id=id client_secret=s"
    )
    result, _, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: A
  GET: /secure
  {auth}
  EXPECT: status == 200
TEST: B
  GET: /secure
  {auth}
  EXPECT: status == 200
""",
        )
    )
    assert result.ok
    assert hits["n"] == 2  # expires_in=0 → each acquisition is a miss


def test_oauth_token_failure_does_not_poison_cache(http_server):
    state = {"n": 0}

    def token_handler(record):
        state["n"] += 1
        if state["n"] == 1:
            return 500, {"Content-Type": "application/json"}, {"error": "boom"}
        return 200, {"Content-Type": "application/json"}, {"access_token": "recovered"}

    http_server.on("POST", "/oauth/token", handler=token_handler)
    http_server.on("GET", "/secure", json={"ok": True})
    auth = (
        f"AUTH: oauth2 token_url={http_server.base_url}/oauth/token "
        f"client_id=id client_secret=s"
    )
    result, _, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: Fail
  GET: /secure
  {auth}
  EXPECT: status == 200
TEST: Recover
  GET: /secure
  {auth}
  EXPECT: status == 200
""",
        ),
        stop_on_failure=False,
    )
    assert result.failed == 1
    assert result.passed == 1
    assert state["n"] == 2


def test_workers_do_not_mutate_shared_test_headers(http_server):
    """OAuth bearer must not be written onto the shared suite test dict."""
    http_server.on("POST", "/oauth/token", json={"access_token": "tok"})
    http_server.on("GET", "/secure", json={"ok": True})
    result, engine, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: A
  GET: /secure
  AUTH: oauth2 token_url={http_server.base_url}/oauth/token client_id=id client_secret=s
  EXPECT: status == 200
TEST: B
  GET: /secure
  AUTH: oauth2 token_url={http_server.base_url}/oauth/token client_id=id client_secret=s
  EXPECT: status == 200
""",
        ),
        workers=2,
    )
    assert result.ok
    for test in engine.suite["tests"]:
        assert "Authorization" not in (test.get("headers") or {})


def test_oauth_double_check_under_lock(http_server):
    """Waiters that lose the race must reuse the cached token (second check)."""
    entered = threading.Event()
    release = threading.Event()
    hits = {"n": 0}

    def token_handler(record):
        hits["n"] += 1
        entered.set()
        assert release.wait(timeout=2)
        return 200, {"Content-Type": "application/json"}, {"access_token": "only-one"}

    http_server.on("POST", "/oauth/token", handler=token_handler)
    suite = parse_dsl(
        _suite(
            http_server,
            f"""
TEST: Only
  GET: /secure
  AUTH: oauth2 token_url={http_server.base_url}/oauth/token client_id=id client_secret=s
  EXPECT: status == 200
""",
        )
    )
    engine = Engine(suite, workers=1, stream=io.StringIO())
    spec = suite["tests"][0]["steps"][0]["oauth2"]
    results = []

    def acquire():
        results.append(engine._oauth_token(spec))

    t1 = threading.Thread(target=acquire)
    t2 = threading.Thread(target=acquire)
    t1.start()
    assert entered.wait(timeout=2)
    t2.start()
    time.sleep(0.05)  # ensure t2 is blocked on the lock
    release.set()
    t1.join(timeout=2)
    t2.join(timeout=2)
    assert sorted(results) == ["only-one", "only-one"]
    assert hits["n"] == 1
