from __future__ import annotations

from pathlib import Path

# Preferred short form. `.snaptest` stays valid so existing files keep running.
# `.snap` is not used — it collides with Jest snapshot files.
SUITE_EXTENSIONS = (".sapi", ".snaptest")


def is_suite_path(value):
    text = str(value or "").lower()
    return any(text.endswith(ext) for ext in SUITE_EXTENSIONS)


def find_suite_files(directory):
    root = Path(directory)
    found = []
    seen = set()
    for ext in SUITE_EXTENSIONS:
        for item in root.rglob(f"*{ext}"):
            if not item.is_file():
                continue
            resolved = item.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(item)
    found.sort(key=lambda path: str(path).lower())
    return found
