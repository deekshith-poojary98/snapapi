from __future__ import annotations

import random
import re
import uuid
from datetime import datetime, timezone

from snapapi.exceptions import SnapAPIError

HELPER_RE = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_.]*)(?:\(([^)]*)\))?\}"
)


def expand_helpers(value):
    if isinstance(value, str):
        return HELPER_RE.sub(_replace, value)
    if isinstance(value, dict):
        return {expand_helpers(key): expand_helpers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_helpers(item) for item in value]
    return value


def helper_names_in(text):
    if not isinstance(text, str):
        return set()
    names = set()
    for match in HELPER_RE.finditer(text):
        if match.group(2) is not None or match.group(1) in ("uuid", "now", "random.int"):
            names.add(match.group(1))
    return names


def is_helper_ref(name, args_present):
    return name in ("uuid", "now") or name == "random.int" or args_present


def _replace(match):
    name = match.group(1)
    raw_args = match.group(2)
    if raw_args is None and name not in ("uuid", "now"):
        return match.group(0)
    return str(_eval(name, raw_args))


def _eval(name, raw_args):
    if name == "uuid":
        return str(uuid.uuid4())
    if name == "now":
        return datetime.now(timezone.utc).isoformat()
    if name == "random.int":
        args = [part.strip() for part in (raw_args or "").split(",") if part.strip()]
        if len(args) != 2:
            raise SnapAPIError("random.int requires two arguments: ${random.int(min,max)}")
        try:
            low, high = int(args[0]), int(args[1])
        except ValueError as exc:
            raise SnapAPIError("random.int arguments must be integers") from exc
        if low > high:
            raise SnapAPIError("random.int min cannot be greater than max")
        return str(random.randint(low, high))
    raise SnapAPIError(f"Unknown helper ${{{name}()}}")
