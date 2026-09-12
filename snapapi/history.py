from __future__ import annotations

import json
import time
from pathlib import Path


def last_run_path(cwd=None):
    return Path(cwd or Path.cwd()) / ".snapapi" / "last-run.json"


def history_path(cwd=None):
    return Path(cwd or Path.cwd()) / ".snapapi" / "history.jsonl"


def write_last_run(suite_results, cwd=None):
    failed = []
    for suite in suite_results:
        for test in suite.tests:
            if test.status == "failed":
                failed.append(test.name)
    payload = {"failed": failed, "time": time.time()}
    path = last_run_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def read_last_failed(cwd=None):
    path = last_run_path(cwd)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return list(payload.get("failed") or [])


def append_history(suite_results, cwd=None):
    path = history_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    stamp = time.time()
    for suite in suite_results:
        for test in suite.tests:
            rows.append(
                {
                    "time": stamp,
                    "suite": suite.name,
                    "name": test.name,
                    "status": test.status,
                    "duration_ms": test.duration_ms,
                }
            )
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
