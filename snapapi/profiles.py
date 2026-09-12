from __future__ import annotations

from pathlib import Path

from snapapi.exceptions import SnapAPIError
from snapapi.variables import load_env_file


def resolve_profile(name, cwd=None):
    if not name:
        return None
    root = Path(cwd or Path.cwd())
    candidates = [
        root / "environments" / f"{name}.env",
        root / ".snapapi" / f"{name}.env",
        root / f"{name}.env",
    ]
    for path in candidates:
        if path.is_file():
            return path
    searched = ", ".join(str(path) for path in candidates)
    raise SnapAPIError(f"Profile {name!r} not found (looked in: {searched})")


def load_profile(name, cwd=None):
    path = resolve_profile(name, cwd=cwd)
    return load_env_file(path) if path else {}
