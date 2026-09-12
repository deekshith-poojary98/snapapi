from __future__ import annotations

import hashlib
import json
from pathlib import Path


def cassette_key(method, url, body=None):
    raw = f"{method.upper()}\n{url}\n{_body_text(body)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_cassettes(directory):
    path = Path(directory)
    if not path.is_dir():
        return {}
    store = {}
    for item in path.glob("*.json"):
        try:
            payload = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        key = payload.get("key") or item.stem
        store[key] = payload
    return store


def save_cassette(directory, key, payload):
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    record = dict(payload)
    record["key"] = key
    (path / f"{key}.json").write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")


def _body_text(body):
    if body is None:
        return ""
    if isinstance(body, (dict, list)):
        return json.dumps(body, sort_keys=True, default=str)
    if isinstance(body, bytes):
        return hashlib.sha256(body).hexdigest()
    return str(body)
