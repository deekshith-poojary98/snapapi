from __future__ import annotations

from pathlib import Path

from snapapi.suites import is_suite_path


def snapshot_mtimes(files, previous=None):
    """Return current (mtime_ns, size) stamps and files that changed since ``previous``.

    The first snapshot (empty ``previous``) never reports changes. Later snapshots
    treat new files and size/mtime updates as changes so ``snapapi watch`` notices
    same-second writes and newly added ``.sapi`` / ``.snaptest`` files.
    """
    previous = previous or {}
    current = {}
    changed = []
    for item in files:
        path = Path(item)
        try:
            stat = path.stat()
            key = str(path.resolve())
            stamp = (int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9))), int(stat.st_size))
        except OSError:
            continue
        current[key] = stamp
        if previous and (key not in previous or stamp != previous[key]):
            changed.append(path)
    return current, changed


def try_watchdog_observer(paths, on_change):
    """Start a watchdog Observer if the optional extra is installed.

    Returns the observer or ``None`` when watchdog is unavailable.
    """
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        return None

    directories = []
    seen = set()
    for item in paths:
        directory = Path(item).resolve().parent
        key = str(directory)
        if key in seen:
            continue
        seen.add(key)
        directories.append(directory)
    if not directories:
        return None

    class _Handler(FileSystemEventHandler):
        def on_any_event(self, event):
            src = str(getattr(event, "src_path", "") or "")
            dest = str(getattr(event, "dest_path", "") or "")
            if is_suite_path(src) or is_suite_path(dest):
                on_change()

    observer = Observer()
    handler = _Handler()
    for directory in directories:
        observer.schedule(handler, str(directory), recursive=True)
    observer.daemon = True
    observer.start()
    return observer
