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
TOKENISH_RE = re.compile(
    r"(?i)(token|secret|password|passwd|api[_-]?key|code_verifier)\s*[:=]\s*\S+"
)
SENSITIVE_NAME_RE = re.compile(
    r"(?i)(token|secret|password|passwd|authorization|cookie|jwt|api[_-]?key|verifier)"
)
JWT_RE = re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


def header_name_is_sensitive(name):
    return str(name).lower() in SENSITIVE_HEADERS


def redact_value(value):
    if value is None:
        return value
    if not isinstance(value, str):
        return value
    text = BEARER_RE.sub(r"\1 " + REDACTED, value)
    text = TOKENISH_RE.sub(_redact_tokenish_match, text)
    return text


def _redact_tokenish_match(match):
    text = match.group(0)
    if "=" in text:
        key, _, _rest = text.partition("=")
        return f"{key}={REDACTED}"
    if ":" in text:
        key, _, _rest = text.partition(":")
        return f"{key}:{REDACTED}" if not _rest.startswith(" ") else f"{key}: {REDACTED}"
    parts = text.split(None, 1)
    return parts[0] + " " + REDACTED


def redact_saved(name, value):
    """Return a CLI-safe display value for SAVE output.

    Tokens, passwords, cookies, and JWT-shaped strings print as ``***``.
    Ordinary ids and emails stay visible.
    """
    if value is None:
        return value
    text = str(value)
    if SENSITIVE_NAME_RE.search(str(name or "")) or JWT_RE.match(text):
        return REDACTED
    return value


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
    text = TOKENISH_RE.sub(_redact_tokenish_match, text)
    if len(text) > limit:
        return text[:limit] + f"... ({len(text)} bytes)"
    return text


def _jsonish(value):
    import json

    return json.dumps(value, default=str)
