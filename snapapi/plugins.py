from __future__ import annotations

import copy
import inspect
import json
import re
from pathlib import Path

from snapapi.exceptions import CallError, SnapAPIError
from snapapi.listeners import _import_target, _split_spec

BUILTIN_HELPERS = frozenset({"uuid", "now", "random.int"})
PLUGIN_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


class ExtensionRegistry:
    """Maps ``crypto.hmac`` and unique bare names to callables."""

    def __init__(self):
        self.qualified = {}
        self.bare = {}
        self.ambiguous = {}

    def names(self):
        names = set(self.qualified)
        names.update(name for name in self.bare if name not in self.ambiguous)
        return names

    def get(self, name, default=None):
        if name in self.ambiguous:
            return None
        if name in self.qualified:
            return self.qualified[name]
        return self.bare.get(name, default)

    def resolve(self, name):
        if name in self.ambiguous:
            alts = " or ".join(self.ambiguous[name])
            raise SnapAPIError(
                f"{name}() is defined in more than one extension. Use {alts}"
            )
        fn = self.get(name)
        if fn is None:
            raise SnapAPIError(
                f"Unknown extension {name}(). Put the function in extensions/ "
                "or list it in snapapi.yaml, or pass --plugin."
            )
        return fn

    def add(self, namespace, func_name, fn):
        if not PLUGIN_NAME_RE.match(func_name or ""):
            raise SnapAPIError(f"Invalid extension name {func_name!r}")
        if namespace:
            if not PLUGIN_NAME_RE.match(namespace):
                raise SnapAPIError(f"Invalid extension namespace {namespace!r}")
            qualified = f"{namespace}.{func_name}"
            if qualified in self.qualified:
                fn = self.qualified[qualified]
            else:
                self.qualified[qualified] = fn
        if func_name in BUILTIN_HELPERS:
            if not namespace:
                raise SnapAPIError(
                    f"Plugin {func_name!r} shadows a built-in (${{{func_name}()}})"
                )
            return
        if func_name in self.ambiguous:
            qualified = f"{namespace}.{func_name}" if namespace else func_name
            if qualified not in self.ambiguous[func_name]:
                self.ambiguous[func_name].append(qualified)
                self.ambiguous[func_name].sort()
            return
        current = self.bare.get(func_name)
        if current is None:
            self.bare[func_name] = fn
            return
        if current is fn:
            return
        first = None
        for key, value in self.qualified.items():
            if value is current and key.endswith("." + func_name):
                first = key
                break
        first = first or func_name
        second = f"{namespace}.{func_name}" if namespace else func_name
        self.ambiguous[func_name] = sorted({first, second})
        self.bare.pop(func_name, None)

    def merge_mapping(self, mapping, namespace=None):
        for name, fn in (mapping or {}).items():
            if not callable(fn):
                raise SnapAPIError(f"Extension {name!r} is not callable")
            if "." in name:
                ns, _, func_name = name.rpartition(".")
                self.add(ns.split(".")[-1] if ns else None, func_name, fn)
            else:
                self.add(namespace, name, fn)

    def merge_registry(self, other):
        for qualified, fn in other.qualified.items():
            ns, _, name = qualified.rpartition(".")
            self.add(ns, name, fn)
        for name, fn in other.bare.items():
            if name in other.ambiguous:
                continue
            if any(key.endswith("." + name) for key in self.qualified):
                continue
            if name in self.bare:
                continue
            self.add(None, name, fn)


def load_plugins(specs):
    """Load ``--plugin`` specs into an :class:`ExtensionRegistry`."""
    registry = ExtensionRegistry()
    for spec in specs or []:
        _register_spec(registry, spec)
    return registry


def load_plugin(spec):
    """Load one ``path.py``, ``path.py:name``, or ``module:name`` spec as a dict."""
    registry = ExtensionRegistry()
    _register_spec(registry, spec)
    out = dict(registry.qualified)
    out.update(registry.bare)
    return out


def discover_extensions(suite_path=None):
    """Load ``extensions/*.py`` and optional ``snapapi.yaml`` near the suite."""
    registry = ExtensionRegistry()
    root = _project_root(suite_path)
    if root is None:
        return registry
    yaml_path = _yaml_path(root)
    if yaml_path is not None:
        for item in _extensions_from_yaml(yaml_path):
            _register_yaml_entry(registry, root, item)
    ext_dir = root / "extensions"
    if ext_dir.is_dir():
        for path in sorted(ext_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            _register_module_file(registry, path)
    return registry


def build_registry(suite_path=None, plugins=None, specs=None):
    registry = discover_extensions(suite_path)
    if specs:
        registry.merge_registry(load_plugins(specs))
    if isinstance(plugins, ExtensionRegistry):
        registry.merge_registry(plugins)
    elif isinstance(plugins, dict) and plugins:
        registry.merge_mapping(plugins)
    return registry


def invoke_extension(fn, name, args, test=None):
    if inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn):
        raise CallError(name, "async functions are not supported", test=test)
    call_args = [_call_arg(arg) for arg in args]
    try:
        result = fn(*call_args)
    except CallError as exc:
        raise exc.with_test(test) from None
    except Exception as exc:
        message = str(exc).strip() or type(exc).__name__
        raise CallError(name, message, test=test) from None
    if inspect.iscoroutine(result):
        result.close()
        raise CallError(name, "async functions are not supported", test=test)
    if inspect.isasyncgen(result):
        closer = result.aclose()
        if inspect.iscoroutine(closer):
            closer.close()
        raise CallError(name, "async functions are not supported", test=test)
    if inspect.isgenerator(result):
        result.close()
        raise CallError(
            name,
            "must return a string, number, bool, object, or array, not generator",
            test=test,
        )
    if result is None:
        raise CallError(name, "returned None", test=test)
    if isinstance(result, bytes):
        try:
            return result.decode("utf-8")
        except UnicodeDecodeError:
            raise CallError(name, "returned non-UTF-8 bytes", test=test) from None
    if isinstance(result, (str, int, float, bool, dict, list)):
        return result
    if isinstance(result, tuple):
        return list(result)
    raise CallError(
        name,
        f"must return a string, number, bool, object, or array, not {type(result).__name__}",
        test=test,
    )


def _call_arg(arg):
    if isinstance(arg, (dict, list)):
        return copy.deepcopy(arg)
    return arg


def format_extension_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def public_functions(module):
    mapping = getattr(module, "PLUGINS", None)
    if isinstance(mapping, dict) and mapping:
        out = {}
        for name, fn in mapping.items():
            if not isinstance(name, str) or not callable(fn):
                raise SnapAPIError("PLUGINS must map names to functions")
            out[name] = fn
        return out
    out = {}
    module_name = getattr(module, "__name__", None)
    for name, obj in inspect.getmembers(module, inspect.isfunction):
        if name.startswith("_"):
            continue
        if module_name and getattr(obj, "__module__", None) not in (module_name, None):
            continue
        out[name] = obj
    if not out:
        raise SnapAPIError(
            f"{getattr(module, '__file__', module)} has no public functions"
        )
    return out


def _register_spec(registry, spec):
    if not spec or not str(spec).strip():
        raise SnapAPIError("Empty --plugin value")
    target, attr = _split_spec(str(spec).strip())
    module = _import_plugin_target(target)
    namespace = Path(target).stem
    if attr:
        obj = getattr(module, attr, None)
        if obj is None:
            raise SnapAPIError(f"Plugin {attr!r} not found in {target}")
        if not callable(obj):
            raise SnapAPIError(f"Plugin {attr!r} in {target} is not callable")
        registry.add(namespace, attr, obj)
        return
    for name, fn in public_functions(module).items():
        registry.add(namespace, name, fn)


def _register_module_file(registry, path):
    module = _import_plugin_target(str(path))
    for name, fn in public_functions(module).items():
        registry.add(path.stem, name, fn)


def _register_yaml_entry(registry, root, item):
    if not isinstance(item, str) or not item.strip():
        raise SnapAPIError("snapapi.yaml extensions entries must be strings")
    item = item.strip()
    dotted = root.joinpath(*item.split("."))
    py_path = dotted if dotted.suffix == ".py" else Path(str(dotted) + ".py")
    if py_path.is_file():
        _register_module_file(registry, py_path)
        return
    rel = root / item
    if rel.is_file():
        _register_module_file(registry, rel)
        return
    module = _import_plugin_target(item)
    namespace = item.rsplit(".", 1)[-1]
    for name, fn in public_functions(module).items():
        registry.add(namespace, name, fn)


def _project_root(suite_path):
    starts = []
    if suite_path:
        path = Path(suite_path)
        starts.append(path.parent if path.suffix else path)
    starts.append(Path.cwd())
    seen = set()
    for start in starts:
        try:
            here = start.resolve()
        except OSError:
            continue
        for _ in range(12):
            marker = str(here)
            if marker in seen:
                break
            seen.add(marker)
            if _yaml_path(here) is not None or _has_extension_modules(here):
                return here
            if here.parent == here:
                break
            here = here.parent
    return None


def _yaml_path(root):
    for name in ("snapapi.yaml", "snapapi.yml"):
        path = root / name
        if path.is_file():
            return path
    return None


def _has_extension_modules(root):
    ext_dir = root / "extensions"
    if not ext_dir.is_dir():
        return False
    return any(path.is_file() and not path.name.startswith("_") for path in ext_dir.glob("*.py"))


def _extensions_from_yaml(path):
    if yaml is None:
        raise SnapAPIError("PyYAML is required to read snapapi.yaml")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SnapAPIError(f"Cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise SnapAPIError(f"Invalid {path.name}: {exc}") from exc
    if not data:
        return []
    if not isinstance(data, dict):
        raise SnapAPIError(f"{path.name} must be a mapping")
    listed = data.get("extensions") or []
    if not isinstance(listed, list):
        raise SnapAPIError(f"{path.name} extensions must be a list")
    return listed


def split_call_args(raw_args):
    if not raw_args:
        return []
    args = []
    current = []
    quote = None
    for char in raw_args:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            current.append(char)
            continue
        if char == ",":
            piece = "".join(current).strip()
            if piece:
                args.append(_strip_quotes(piece))
            current = []
            continue
        current.append(char)
    piece = "".join(current).strip()
    if piece:
        args.append(_strip_quotes(piece))
    return args


def _strip_quotes(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


_MODULE_CACHE = {}


def _import_plugin_target(target):
    path = Path(target)
    if target.endswith(".py") or path.is_file():
        if not path.is_file():
            raise SnapAPIError(f"Plugin file not found: {target}")
        key = str(path.resolve())
        cached = _MODULE_CACHE.get(key)
        if cached is not None:
            return cached
        try:
            module = _import_target(target)
        except SnapAPIError as exc:
            message = str(exc).replace("Listener", "Plugin").replace("listener", "plugin")
            raise SnapAPIError(message) from exc
        _MODULE_CACHE[key] = module
        return module
    try:
        return _import_target(target)
    except SnapAPIError as exc:
        message = str(exc).replace("Listener", "Plugin").replace("listener", "plugin")
        raise SnapAPIError(message) from exc
