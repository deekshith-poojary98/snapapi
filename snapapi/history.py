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
                failed.append(
                    {
                        "file": suite.source,
                        "suite": suite.name,
                        "name": test.name,
                    }
                )
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
    return normalize_last_failed(payload.get("failed") or [])


def normalize_last_failed(failed):
    rows = []
    for item in failed or []:
        if isinstance(item, str):
            rows.append({"file": None, "suite": None, "name": item})
        elif isinstance(item, dict) and item.get("name"):
            rows.append(
                {
                    "file": item.get("file"),
                    "suite": item.get("suite"),
                    "name": item.get("name"),
                }
            )
    return rows


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


def read_history(cwd=None):
    path = history_path(cwd)
    if not path.is_file():
        return []
    rows = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def parse_since(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if text and text[-1] in multipliers:
        try:
            amount = float(text[:-1])
        except ValueError as exc:
            raise ValueError(f"Invalid --since value {value!r} (use 7d, 24h, 30m)") from exc
        return time.time() - amount * multipliers[text[-1]]
    raise ValueError(f"Invalid --since value {value!r} (use 7d, 24h, 30m)")


def format_history(rows, failed=False, since=None):
    if failed:
        rows = [row for row in rows if row.get("status") == "failed"]
    if since is not None:
        cutoff = parse_since(since)
        rows = [row for row in rows if float(row.get("time") or 0) >= cutoff]
    if not rows:
        return "(no history)"
    headers = ("TIME", "SUITE", "NAME", "STATUS", "DURATION")
    table = []
    for row in rows:
        stamp = row.get("time")
        try:
            rendered = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(stamp)))
        except (TypeError, ValueError, OSError):
            rendered = str(stamp or "")
        duration = row.get("duration_ms")
        try:
            duration_text = f"{float(duration):.0f}ms"
        except (TypeError, ValueError):
            duration_text = ""
        table.append(
            (
                rendered,
                str(row.get("suite") or ""),
                str(row.get("name") or ""),
                str(row.get("status") or ""),
                duration_text,
            )
        )
    widths = [len(header) for header in headers]
    for item in table:
        for index, cell in enumerate(item):
            widths[index] = max(widths[index], len(cell))
    lines = ["  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))]
    for item in table:
        lines.append("  ".join(item[index].ljust(widths[index]) for index in range(len(headers))))
    return "\n".join(lines)
