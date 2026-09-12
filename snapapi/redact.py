from __future__ import annotations

import re

REDACTED = "***"
SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-apikey",
    "api-key",
    "proxy-authorization",
}
BEARER_RE = re.compile(r"(?i)\b(bearer)\s+\S+")
TOKENISH_RE = re.compile(r"(?i)(token|secret|password|passwd|api[_-]?key)\s*[:=]\s*\S+")


def header_name_is_sensitive(name):
    return str(name).lower() in SENSITIVE_HEADERS


def redact_value(value):
    if value is None:
        return value
    if not isinstance(value, str):
        return value
    text = BEARER_RE.sub(r"\1 " + REDACTED, value)
    text = TOKENISH_RE.sub(lambda match: match.group(0).split()[0] + " " + REDACTED, text)
    return text


def redact_headers(headers):
    if not headers:
        return {}
    redacted = {}
    for key, value in headers.items():
        if header_name_is_sensitive(key):
            redacted[key] = REDACTED
        else:
            redacted[key] = redact_value(value)
    return redacted


def redact_body(body, limit=2000):
    if body is None:
        return None
    if isinstance(body, (dict, list)):
        text = _jsonish(body)
    else:
        text = str(body)
    text = BEARER_RE.sub(r"\1 " + REDACTED, text)
    if len(text) > limit:
        return text[:limit] + f"... ({len(text)} bytes)"
    return text


def _jsonish(value):
    import json

    return json.dumps(value, default=str)
