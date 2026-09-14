from __future__ import annotations

import random
import re
import uuid
from datetime import datetime, timezone

from snapapi.exceptions import SnapAPIError
from snapapi.plugins import (
    BUILTIN_HELPERS,
    ExtensionRegistry,
    format_extension_value,
    invoke_extension,
    split_call_args,
)

HELPER_RE = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_.]*)(?:\(([^)]*)\))?\}"
)


def expand_helpers(value, plugins=None, variables=None):
    registry = plugins
    env = variables or {}

    def walk(item):
        if isinstance(item, str):
            return HELPER_RE.sub(lambda match: _replace(match, registry, env), item)
        if isinstance(item, dict):
            return {walk(key): walk(val) for key, val in item.items()}
        if isinstance(item, list):
            return [walk(val) for val in item]
        return item

    return walk(value)


def helper_names_in(text):
    if not isinstance(text, str):
        return set()
    names = set()
    for match in HELPER_RE.finditer(text):
        if match.group(2) is not None or match.group(1) in BUILTIN_HELPERS:
            names.add(match.group(1))
    return names


def is_helper_ref(name, args_present):
    return name in BUILTIN_HELPERS or args_present


def _replace(match, plugins, variables):
    name = match.group(1)
    raw_args = match.group(2)
    if raw_args is None and name not in ("uuid", "now"):
        return match.group(0)
    return _eval(name, raw_args, plugins, variables)


def _eval(name, raw_args, plugins, variables):
    if name == "uuid":
        return str(uuid.uuid4())
    if name == "now":
        return datetime.now(timezone.utc).isoformat()
    if name == "random.int":
        args = split_call_args(raw_args)
        if len(args) != 2:
            raise SnapAPIError("random.int requires two arguments: ${random.int(min,max)}")
        try:
            low, high = int(args[0]), int(args[1])
        except ValueError as exc:
            raise SnapAPIError("random.int arguments must be integers") from exc
        if low > high:
            raise SnapAPIError("random.int min cannot be greater than max")
        return str(random.randint(low, high))
    fn = _lookup(plugins, name)
    if fn is None:
        raise SnapAPIError(
            f"Unknown helper ${{{name}()}}. Built-ins are uuid, now, random.int. "
            "Use CALL with a function from extensions/, or pass --plugin."
        )
    args = [_interpolate_arg(part, variables, plugins) for part in split_call_args(raw_args)]
    result = invoke_extension(fn, name, args)
    return format_extension_value(result)


def _lookup(plugins, name):
    if plugins is None:
        return None
    if isinstance(plugins, ExtensionRegistry):
        if name in plugins.ambiguous:
            plugins.resolve(name)
        return plugins.get(name)
    if isinstance(plugins, dict):
        return plugins.get(name)
    return None


def _interpolate_arg(text, variables, plugins):
    from snapapi.variables import interpolate

    return interpolate(text, variables or {}, plugins=plugins)
