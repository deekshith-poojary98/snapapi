"""I2 — FILE uploads must participate in VCR cassette identity when body is matched."""

import io
import os
from pathlib import Path

from snapapi.cassette import cassette_key
from snapapi.cli import main
from tests.helpers import run_dsl
from tests.test_engine import _suite


def _files(parts):
    """Build a requests-style files mapping: field -> (filename, fileobj)."""
    opened = {}
    for field, filename, data in parts:
        opened[field] = (filename, io.BytesIO(data))
    return opened


def test_cassette_key_same_endpoint_same_bytes_match():
    url = "http://example.test/upload"
    a = _files([("file", "a.txt", b"hello")])
    b = _files([("file", "a.txt", b"hello")])
    assert cassette_key("POST", url, files=a) == cassette_key("POST", url, files=b)


def test_cassette_key_different_bytes_differ():
    url = "http://example.test/upload"
    hello = _files([("file", "a.txt", b"hello")])
    goodbye = _files([("file", "b.txt", b"goodbye")])
    assert cassette_key("POST", url, files=hello) != cassette_key("POST", url, files=goodbye)


def test_cassette_key_same_filename_different_contents_differ():
    url = "http://example.test/upload"
    one = _files([("file", "photo.jpg", b"aaa")])
    two = _files([("file", "photo.jpg", b"bbb")])
    assert cassette_key("POST", url, files=one) != cassette_key("POST", url, files=two)


def test_cassette_key_different_filename_same_contents_differ():
    """Filename is on the wire (Content-Disposition); identity includes it."""
    url = "http://example.test/upload"
    a = _files([("file", "a.txt", b"same")])
    b = _files([("file", "b.txt", b"same")])
    assert cassette_key("POST", url, files=a) != cassette_key("POST", url, files=b)


def test_cassette_key_multiple_files_and_order():
    url = "http://example.test/upload"
    both = _files([("avatar", "a.bin", b"\x00\x01"), ("banner", "b.bin", b"\x02\x03")])
    swapped = {}
    swapped["banner"] = ("b.bin", io.BytesIO(b"\x02\x03"))
    swapped["avatar"] = ("a.bin", io.BytesIO(b"\x00\x01"))
    # Distinct field sets / declaration order → different multipart identity
    only_avatar = _files([("avatar", "a.bin", b"\x00\x01")])
    assert cassette_key("POST", url, files=both) != cassette_key("POST", url, files=only_avatar)
    assert cassette_key("POST", url, files=both) != cassette_key("POST", url, files=swapped)


def test_cassette_key_empty_and_binary_files():
    url = "http://example.test/upload"
    empty = _files([("file", "empty.bin", b"")])
    binary = _files([("file", "empty.bin", b"\x00\xff")])
    assert cassette_key("POST", url, files=empty) != cassette_key("POST", url, files=binary)
    assert cassette_key("POST", url, files=empty) == cassette_key(
        "POST", url, files=_files([("file", "empty.bin", b"")])
    )


def test_cassette_key_files_ignored_when_body_not_matched():
    url = "http://example.test/upload"
    a = _files([("file", "a.txt", b"hello")])
    b = _files([("file", "a.txt", b"goodbye")])
    match = ["query", "content-type", "accept"]
    assert cassette_key("POST", url, files=a, match=match) == cassette_key(
        "POST", url, files=b, match=match
    )


def test_cassette_key_rewinds_file_handles():
    handle = io.BytesIO(b"payload")
    files = {"file": ("x.bin", handle)}
    cassette_key("POST", "http://example.test/upload", files=files)
    assert handle.tell() == 0
    assert handle.read() == b"payload"


def test_record_replay_distinguishes_file_bytes(http_server, tmp_path):
    state = {"bodies": []}

    def handler(record):
        raw = record.get("raw") or b""
        state["bodies"].append(raw)
        if b"hello" in raw:
            return 200, {"Content-Type": "application/json"}, {"echo": "hello"}
        if b"goodbye" in raw:
            return 200, {"Content-Type": "application/json"}, {"echo": "goodbye"}
        return 400, {"Content-Type": "application/json"}, {"echo": "unknown"}

    http_server.on("POST", "/upload", handler=handler)
    cassette_dir = tmp_path / "cassettes"
    hello = tmp_path / "hello.txt"
    goodbye = tmp_path / "goodbye.txt"
    hello.write_bytes(b"hello")
    goodbye.write_bytes(b"goodbye")

    result, _, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: Hello
  POST: /upload
  FILE: doc FROM {hello}
  EXPECT: status == 200
  EXPECT: json $.echo == "hello"

TEST: Goodbye
  POST: /upload
  FILE: doc FROM {goodbye}
  EXPECT: status == 200
  EXPECT: json $.echo == "goodbye"
""",
        ),
        mode="record",
        cassette_dir=str(cassette_dir),
    )
    assert result.ok
    assert len(list(cassette_dir.glob("*.json"))) == 2
    assert len(http_server.requests) == 2

    http_server.requests.clear()
    result, _, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: Hello
  POST: /upload
  FILE: doc FROM {hello}
  EXPECT: status == 200
  EXPECT: json $.echo == "hello"

TEST: Goodbye
  POST: /upload
  FILE: doc FROM {goodbye}
  EXPECT: status == 200
  EXPECT: json $.echo == "goodbye"
""",
        ),
        mode="replay",
        cassette_dir=str(cassette_dir),
    )
    assert result.ok
    assert http_server.requests == []


def test_replay_miss_when_local_file_contents_change(http_server, tmp_path):
    http_server.on("POST", "/upload", json={"ok": True})
    cassette_dir = tmp_path / "cassettes"
    path = tmp_path / "doc.txt"
    path.write_bytes(b"original")

    result, _, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: Upload
  POST: /upload
  FILE: doc FROM {path}
  EXPECT: status == 200
""",
        ),
        mode="record",
        cassette_dir=str(cassette_dir),
    )
    assert result.ok

    path.write_bytes(b"changed")
    result, _, output = run_dsl(
        _suite(
            http_server,
            f"""
TEST: Upload
  POST: /upload
  FILE: doc FROM {path}
  EXPECT: status == 200
""",
        ),
        mode="replay",
        cassette_dir=str(cassette_dir),
    )
    assert not result.ok
    assert "No cassette" in (result.tests[0].error or output)


def test_retry_file_upload_stable_cassette_key(http_server, tmp_path):
    photo = tmp_path / "photo.bin"
    photo.write_bytes(b"RETRY-FILE-VCR-PAYLOAD")
    cassette_dir = tmp_path / "cassettes"
    http_server.on("POST", "/upload", json={"ok": True}, fail_times=1)

    result, _, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: Upload
  POST: /upload
  FILE: avatar FROM {photo}
  EXPECT: status == 200 RETRY 3 ON 5xx BACKOFF 0s
""",
        ),
        mode="record",
        cassette_dir=str(cassette_dir),
        retry_backoff=0,
    )
    assert result.ok
    assert len(http_server.requests) == 2
    assert len(list(cassette_dir.glob("*.json"))) == 1

    http_server.requests.clear()
    result, _, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: Upload
  POST: /upload
  FILE: avatar FROM {photo}
  EXPECT: status == 200
""",
        ),
        mode="replay",
        cassette_dir=str(cassette_dir),
    )
    assert result.ok
    assert http_server.requests == []


def test_wait_file_upload_records_final_attempt_key(http_server, tmp_path):
    photo = tmp_path / "photo.bin"
    photo.write_bytes(b"WAIT-FILE-VCR-PAYLOAD")
    cassette_dir = tmp_path / "cassettes"
    state = {"n": 0}

    def handler(record):
        state["n"] += 1
        if state["n"] < 2:
            return 200, {"Content-Type": "application/json"}, {"status": "pending"}
        return 200, {"Content-Type": "application/json"}, {"status": "ready"}

    http_server.on("POST", "/upload", handler=handler)
    result, _, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: Poll
  POST: /upload
  FILE: avatar FROM {photo}
  WAIT: json $.status == "ready" TIMEOUT 2s BACKOFF 0s
  EXPECT: status == 200
""",
        ),
        mode="record",
        cassette_dir=str(cassette_dir),
    )
    assert result.ok
    # Same FILE bytes each attempt → one cassette key (last successful write wins)
    assert len(list(cassette_dir.glob("*.json"))) == 1

    http_server.requests.clear()
    result, _, _ = run_dsl(
        _suite(
            http_server,
            f"""
TEST: Poll
  POST: /upload
  FILE: avatar FROM {photo}
  WAIT: json $.status == "ready" TIMEOUT 2s BACKOFF 0s
  EXPECT: status == 200
""",
        ),
        mode="replay",
        cassette_dir=str(cassette_dir),
    )
    assert result.ok
    assert http_server.requests == []


def test_cli_record_replay_file_vcr(http_server, tmp_path):
    def handler(record):
        raw = record.get("raw") or b""
        label = "hello" if b"hello" in raw else "goodbye"
        return 200, {"Content-Type": "application/json"}, {"echo": label}

    http_server.on("POST", "/upload", handler=handler)
    cassette_dir = tmp_path / "cassettes"
    hello = tmp_path / "a.txt"
    goodbye = tmp_path / "b.txt"
    hello.write_text("hello", encoding="utf-8")
    goodbye.write_text("goodbye", encoding="utf-8")
    suite = tmp_path / "file-vcr.snaptest"
    suite.write_text(
        f"""
SUITE: FileVCR
OPTIONS: {{"CASSETTE_DIR": "{cassette_dir.as_posix()}"}}
URL: {http_server.base_url}
TEST: Hello
  POST: /upload
  FILE: doc FROM {hello.as_posix()}
  EXPECT: json $.echo == "hello"
TEST: Goodbye
  POST: /upload
  FILE: doc FROM {goodbye.as_posix()}
  EXPECT: json $.echo == "goodbye"
""",
        encoding="utf-8",
    )
    old = Path.cwd()
    try:
        os.chdir(tmp_path)
        assert main([str(suite), "--mode", "record"]) == 0
        assert len(list(cassette_dir.glob("*.json"))) == 2
        http_server.requests.clear()
        assert main([str(suite), "--mode", "replay"]) == 0
        assert http_server.requests == []
    finally:
        os.chdir(old)
