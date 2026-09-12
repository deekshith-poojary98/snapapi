from __future__ import annotations

import re

from snapapi.exceptions import JsonPathError

FILTER_RE = re.compile(
    r"^\?\(\s*@\.([A-Za-z_][\w.]*)\s*==\s*(.*?)\s*\)$",
    re.DOTALL,
)


def extract(data, path):
    """Resolve a small JSONPath subset.

    Supported: ``$.a.b``, ``$.items.0.id``, ``$.items[0].id``, ``$.items[*].id``,
    and equality filters ``$.items[?(@.status=="open")]`` / ``$.items[?(@.id==1)]``.
    """
    if path is None or path == "" or path == "$":
        return data
    if not isinstance(path, str) or not path.startswith("$"):
        raise JsonPathError(f"JSONPath must start with $: {path!r}")

    return _extract_tokens(data, _tokenize(path[1:]), path)


def _extract_tokens(current, tokens, path):
    if not tokens:
        return current
    token = tokens[0]
    rest = tokens[1:]
    if _is_filter(token):
        items = current if isinstance(current, list) else [current]
        filtered = [item for item in items if _match_filter(item, token)]
        if not rest:
            return filtered
        return [_extract_tokens(item, rest, path) for item in filtered]
    if token == "*":
        if not isinstance(current, list):
            raise JsonPathError(f"Path {path} not found (at '*')")
        return [_extract_tokens(item, rest, path) for item in current]
    return _extract_tokens(_step(current, token, path), rest, path)


def _tokenize(remainder):
    tokens = []
    i = 0
    length = len(remainder)
    while i < length:
        char = remainder[i]
        if char == ".":
            i += 1
            if i >= length:
                raise JsonPathError("Trailing '.' in JSONPath")
            if remainder[i] == "[":
                continue
            start = i
            while i < length and remainder[i] not in ".[":
                i += 1
            key = remainder[start:i]
            if not key:
                raise JsonPathError("Empty path segment in JSONPath")
            if key == "*":
                tokens.append("*")
            else:
                tokens.append(int(key) if key.isdigit() else key)
        elif char == "[":
            i += 1
            inner, i = _read_bracket(remainder, i)
            tokens.append(_parse_bracket(inner))
        else:
            raise JsonPathError(f"Invalid JSONPath near {remainder[i:]!r}")
    return tokens


def _read_bracket(remainder, i):
    start = i
    depth = 1
    in_string = None
    length = len(remainder)
    while i < length:
        char = remainder[i]
        if in_string:
            if char == "\\" and i + 1 < length:
                i += 2
                continue
            if char == in_string:
                in_string = None
            i += 1
            continue
        if char in ("'", '"'):
            in_string = char
            i += 1
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return remainder[start:i].strip(), i + 1
        i += 1
    raise JsonPathError("Unclosed '[' in JSONPath")


def _parse_bracket(inner):
    if not inner:
        raise JsonPathError("Empty [] in JSONPath")
    if inner == "*":
        return "*"
    if inner.startswith("?"):
        match = FILTER_RE.match(inner)
        if not match:
            raise JsonPathError(
                f"Unsupported JSONPath filter {inner!r}; use ?(@.field==value)"
            )
        return ("filter", match.group(1), _parse_filter_value(match.group(2)))
    if (inner[0] == inner[-1]) and inner[0] in ("'", '"') and len(inner) >= 2:
        return inner[1:-1]
    if inner.isdigit() or (inner.startswith("-") and inner[1:].isdigit()):
        return int(inner)
    return inner


def _parse_filter_value(raw):
    text = (raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def _is_filter(token):
    return isinstance(token, tuple) and token and token[0] == "filter"


def _match_filter(item, token):
    _, field, expected = token
    if not isinstance(item, dict):
        return False
    current = item
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return current == expected


def _step(current, token, path):
    try:
        if isinstance(token, int):
            if isinstance(current, dict):
                key = str(token)
                if key in current:
                    return current[key]
                if token in current:
                    return current[token]
                raise KeyError(token)
            return current[token]
        if isinstance(current, dict):
            return current[token]
        raise TypeError(type(current))
    except (KeyError, IndexError, TypeError) as exc:
        raise JsonPathError(f"Path {path} not found (at {token!r})") from exc
