from __future__ import annotations

from pathlib import Path


def snapshot_mtimes(files, previous=None):
    """Return current mtimes and the files whose mtime increased since ``previous``."""
    previous = previous or {}
    current = {}
    changed = []
    for item in files:
        path = Path(item)
        try:
            mtime = path.stat().st_mtime
            key = str(path.resolve())
        except OSError:
            continue
        current[key] = mtime
        if key in previous and mtime > previous[key]:
            changed.append(path)
    return current, changed
