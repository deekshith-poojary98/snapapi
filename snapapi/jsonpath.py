from __future__ import annotations

from snapapi.exceptions import JsonPathError


def extract(data, path):
    """Resolve a small JSONPath subset: ``$.a.b``, ``$.items.0.id``, ``$.items[0].id``, ``$.items[*].id``.

    Filter expressions such as ``$[*]?(@.x==1)`` are not supported.
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
            start = i
            while i < length and remainder[i] != "]":
                i += 1
            if i >= length:
                raise JsonPathError("Unclosed '[' in JSONPath")
            inner = remainder[start:i].strip()
            i += 1
            tokens.append(_parse_bracket(inner))
        else:
            raise JsonPathError(f"Invalid JSONPath near {remainder[i:]!r}")
    return tokens


def _parse_bracket(inner):
    if not inner:
        raise JsonPathError("Empty [] in JSONPath")
    if inner == "*":
        return "*"
    if inner.startswith("?"):
        raise JsonPathError("JSONPath filters such as ?(@.x==1) are not supported; use [*]")
    if (inner[0] == inner[-1]) and inner[0] in ("'", '"') and len(inner) >= 2:
        return inner[1:-1]
    if inner.isdigit() or (inner.startswith("-") and inner[1:].isdigit()):
        return int(inner)
    return inner


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
