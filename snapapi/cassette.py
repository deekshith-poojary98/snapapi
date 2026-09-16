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


def cassette_key(method, url, body=None, headers=None, match=None, files=None):
    """Build a stable cassette id for the outbound HTTP request.

    Default match fields (``query``, ``body``, ``content-type``, ``accept``) identify
    the request. When ``body`` is matched, FILE uploads participate via field name,
    filename, and content digest — the multipart payload identity, not just JSON/form
    ``data`` / ``raw_body``.
    """
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
    if "body" in match_keys:
        body_blob = _payload_text(body, files)
    else:
        body_blob = ""
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


def _payload_text(body, files=None):
    """Canonicalize JSON/form/raw body plus multipart FILE parts for cassette identity."""
    chunks = []
    body_text = _body_text(body)
    if body_text:
        chunks.append(body_text)
    files_text = _files_text(files)
    if files_text:
        chunks.append(files_text)
    return "\n".join(chunks)


def _body_text(body):
    if body is None:
        return ""
    if isinstance(body, (dict, list)):
        return json.dumps(body, sort_keys=True, default=str)
    if isinstance(body, bytes):
        return hashlib.sha256(body).hexdigest()
    return str(body)


def _files_text(files):
    if not files:
        return ""
    lines = []
    for field, item in files.items():
        filename, digest = _file_fingerprint(item)
        lines.append(f"{field}:{filename}:{digest}")
    return "\n".join(lines)


def _file_fingerprint(item):
    """Return (filename, sha256-hex) for a requests-style files value; rewind file objects."""
    if isinstance(item, (tuple, list)) and item:
        filename = str(item[0])
        payload = item[1] if len(item) > 1 else b""
    else:
        filename = getattr(item, "name", "") or ""
        payload = item
    data = _read_file_bytes(payload)
    return filename, hashlib.sha256(data).hexdigest()


def _read_file_bytes(payload):
    if payload is None:
        return b""
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    if isinstance(payload, str):
        return payload.encode("utf-8")
    read = getattr(payload, "read", None)
    if not callable(read):
        return str(payload).encode("utf-8")
    seek = getattr(payload, "seek", None)
    if callable(seek):
        seek(0)
    data = read()
    if callable(seek):
        seek(0)
    if isinstance(data, str):
        return data.encode("utf-8")
    return bytes(data or b"")
