from __future__ import annotations

import os
import re

from snapapi.exceptions import ParseError, SnapAPIError

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


def interpolate(value, variables):
    """Replace ``${VAR}`` in strings; walk dicts and lists."""
    if isinstance(value, str):
        return _interpolate_string(value, variables)
    if isinstance(value, dict):
        return {
            interpolate(key, variables): interpolate(item, variables)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [interpolate(item, variables) for item in value]
    return value


def _interpolate_string(value, variables):
    def repl(match):
        name = match.group(1)
        if name not in variables or variables[name] is None:
            raise SnapAPIError(f"Undefined variable ${{{name}}}")
        return str(variables[name])

    return VAR_PATTERN.sub(repl, value)
