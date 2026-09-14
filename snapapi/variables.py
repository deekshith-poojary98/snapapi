from __future__ import annotations

import os
import re
from pathlib import Path

from snapapi.exceptions import ParseError, SnapAPIError
from snapapi.helpers import expand_helpers

VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def load_env_file(path):
    """Load KEY=VALUE pairs from a file. Lines starting with # are comments."""
    variables = {}
    with open(path, "r", encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                raise ParseError(
                    f"Invalid env line (expected KEY=VALUE): {line}",
                    filename=str(path),
                    lineno=lineno,
                )
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if (len(value) >= 2) and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if not key:
                raise ParseError("Empty env variable name", filename=str(path), lineno=lineno)
            variables[key] = value
    return variables


def base_variables(env_file=None, extra=None):
    merged = dict(os.environ)
    if env_file:
        merged.update(load_env_file(env_file))
    if extra:
        merged.update(extra)
    return merged


def discover_env_file(suite_path):
    """Find a KEY=VALUE file next to a suite when ``--env`` was omitted.

    Order: ``<stem>.env``, then ``.env`` in that directory, then the only
    other ``*.env`` sibling (``*.env.example`` is ignored).
    """
    suite = Path(suite_path)
    parent = suite.parent
    stem = parent / f"{suite.stem}.env"
    if stem.is_file():
        return str(stem)
    hidden = parent / ".env"
    if hidden.is_file():
        return str(hidden)
    siblings = []
    try:
        for path in parent.iterdir():
            if not path.is_file():
                continue
            name = path.name
            if name.endswith(".env.example") or name.endswith(".example"):
                continue
            if name.endswith(".env"):
                siblings.append(path)
    except OSError:
        return None
    if len(siblings) == 1:
        return str(siblings[0])
    return None


def resolve_env_file(explicit, suite_path):
    if explicit:
        return explicit
    return discover_env_file(suite_path)


def interpolate(value, variables, plugins=None):
    """Replace helpers and ``${VAR}`` in strings; walk dicts and lists."""
    value = expand_helpers(value, plugins=plugins, variables=variables)
    if isinstance(value, str):
        return _interpolate_string(value, variables, plugins=plugins)
    if isinstance(value, dict):
        return {
            interpolate(key, variables, plugins=plugins): interpolate(item, variables, plugins=plugins)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [interpolate(item, variables, plugins=plugins) for item in value]
    return value


WHOLE_VAR = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _interpolate_string(value, variables, plugins=None):
    whole = WHOLE_VAR.match(value)
    if whole:
        name = whole.group(1)
        if name not in variables or variables[name] is None:
            hint = ""
            if name.isupper():
                hint = ". Set it in the environment or pass --env"
            raise SnapAPIError(f"Undefined variable ${{{name}}}{hint}")
        return variables[name]

    def repl(match):
        name = match.group(1)
        if name not in variables or variables[name] is None:
            hint = ""
            if name.isupper():
                hint = ". Set it in the environment or pass --env"
            raise SnapAPIError(f"Undefined variable ${{{name}}}{hint}")
        resolved = variables[name]
        if isinstance(resolved, (dict, list, bool)):
            from snapapi.plugins import format_extension_value

            return format_extension_value(resolved)
        return str(resolved)

    return VAR_PATTERN.sub(repl, value)
