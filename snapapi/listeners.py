from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

from snapapi.exceptions import SnapAPIError


def notify(listeners, method, *args, on_error=None):
    """Call ``method`` on each listener that implements it.

    Listener exceptions are swallowed so a down reporting system cannot
    hide a real test failure. ``on_error(listener, method, exc)`` is optional.
    """
    for listener in listeners or []:
        hook = getattr(listener, method, None)
        if not callable(hook):
            continue
        try:
            hook(*args)
        except Exception as exc:
            if on_error is not None:
                on_error(listener, method, exc)
            else:
                name = getattr(listener, "__class__", type(listener)).__name__
                print(f"snapapi: listener {name}.{method} failed: {exc}", file=sys.stderr)


def finish_listeners(listeners, on_error=None):
    notify(listeners, "close", on_error=on_error)


def load_listeners(specs):
    return [load_listener(spec) for spec in specs or []]


def load_listener(spec):
    """Load a listener from ``path.py``, ``path.py:ClassName``, or ``module:ClassName``."""
    if not spec or not str(spec).strip():
        raise SnapAPIError("Empty --listener value")
    target, class_name = _split_spec(str(spec).strip())
    module = _import_target(target)
    if class_name:
        obj = getattr(module, class_name, None)
        if obj is None:
            raise SnapAPIError(f"Listener class {class_name!r} not found in {target}")
        return obj() if isinstance(obj, type) else obj
    inferred = getattr(module, Path(target).stem, None)
    if isinstance(inferred, type):
        return inferred()
    return module


def _split_spec(spec):
    if ":" not in spec:
        return spec, None
    left, right = spec.rsplit(":", 1)
    if right.isidentifier() and (
        left.endswith(".py")
        or "/" in left
        or "\\" in left
        or Path(left).exists()
        or "." in left
    ):
        return left, right
    return spec, None


def _import_target(target):
    path = Path(target)
    if target.endswith(".py") or path.is_file():
        if not path.is_file():
            raise SnapAPIError(f"Listener file not found: {target}")
        resolved = path.resolve()
        name = f"snapapi_listener_{resolved.stem}_{abs(hash(str(resolved)))}"
        spec = importlib.util.spec_from_file_location(name, resolved)
        if spec is None or spec.loader is None:
            raise SnapAPIError(f"Cannot import listener: {target}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    try:
        return importlib.import_module(target)
    except ImportError as exc:
        raise SnapAPIError(f"Cannot import listener {target!r}: {exc}") from exc
