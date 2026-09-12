from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEFAULT_VCR_MATCH = ("query", "body", "content-type", "accept")
VCR_HEADER_ALIASES = {
    "content_type": "content-type",
    "content-type": "content-type",
    "accept": "accept",
    "authorization": "authorization",
}


def parse_vcr_match(value):
    if value is None:
        return list(DEFAULT_VCR_MATCH)
    if isinstance(value, str):
        items = [item.strip().lower() for item in value.replace(";", ",").split(",") if item.strip()]
    elif isinstance(value, (list, tuple)):
        items = [str(item).strip().lower() for item in value if str(item).strip()]
    else:
        return list(DEFAULT_VCR_MATCH)
    return [VCR_HEADER_ALIASES.get(item, item) for item in items] or list(DEFAULT_VCR_MATCH)


def cassette_key(method, url, body=None, headers=None, match=None):
    match_keys = parse_vcr_match(match)
    parts = urlsplit(url or "")
    if "query" in match_keys:
        query = sorted(parse_qsl(parts.query, keep_blank_values=True))
        query_blob = urlencode(query)
    else:
        query_blob = ""
    canonical = urlunsplit((parts.scheme, parts.netloc, parts.path, query_blob, parts.fragment))
    wanted_headers = {item for item in match_keys if item in ("accept", "content-type", "authorization")}
    selected = []
    for key, value in (headers or {}).items():
        lowered = str(key).lower()
        if lowered in wanted_headers:
            selected.append((lowered, "" if value is None else str(value)))
    selected.sort()
    header_blob = "\n".join(f"{key}:{value}" for key, value in selected)
    body_blob = _body_text(body) if "body" in match_keys else ""
    raw = f"{method.upper()}\n{canonical}\n{header_blob}\n{body_blob}"
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
