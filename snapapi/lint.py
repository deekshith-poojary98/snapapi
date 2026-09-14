from __future__ import annotations

import re

from snapapi.helpers import HELPER_RE
from snapapi.parser import TestParser
from snapapi.plugins import BUILTIN_HELPERS
from snapapi.variables import VAR_PATTERN

SAVE_NAME_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def lint_files(files, variables=None, strict=False, plugins=None):
    parser = TestParser()
    env = dict(variables or {})
    issues = []
    for path in files:
        issues.extend(lint_suite(parser.parse(path), env, strict=strict, plugins=plugins))
    return issues


def lint_suite(suite, variables=None, strict=False, plugins=None):
    env = set((variables or {}).keys())
    known_helpers = set(BUILTIN_HELPERS)
    known_helpers.update(plugins or ())
    issues = []
    saved = set()
    used_saves = set()
    source = suite.get("source") or "<string>"
    for item in _prep_items(suite):
        env.add(item["name"])

    for test in suite.get("tests") or []:
        for item in _prep_items(test):
            env.add(item["name"])
        for step in test.get("steps") or []:
            for item in _prep_items(step):
                env.add(item["name"])
            for save in step.get("saves") or []:
                saved.add(save["name"])
            blobs = [
                test.get("base_url"),
                step.get("endpoint"),
                step.get("data"),
                step.get("headers"),
                step.get("query"),
                step.get("raw_body"),
                *[item.get("value") for item in (step.get("sets") or [])],
                *[arg for item in _prep_items(step) for arg in item.get("args") or []],
            ]
            for blob in blobs:
                _lint_helpers(
                    blob,
                    known_helpers,
                    issues,
                    test.get("source") or source,
                    step.get("lineno") or test.get("lineno"),
                )
                for name in _names_in(blob):
                    if name in saved:
                        used_saves.add(name)
                    if name in env or name in saved or _is_helper(name, blob, known_helpers):
                        continue
                    issues.append(
                        {
                            "level": "error",
                            "code": "undefined-var",
                            "message": f"Undefined variable ${{{name}}}",
                            "filename": test.get("source") or source,
                            "lineno": step.get("lineno") or test.get("lineno"),
                        }
                    )
            for check in step.get("checks") or []:
                for blob in check.values():
                    _lint_helpers(
                        blob,
                        known_helpers,
                        issues,
                        test.get("source") or source,
                        step.get("lineno") or test.get("lineno"),
                    )
                    for name in _names_in(blob):
                        if name in saved:
                            used_saves.add(name)

    for owner, filename, lineno in _all_prep_scopes(suite, source):
        for item in _prep_items(owner):
            if item.get("kind") != "call":
                continue
            func = item.get("func")
            if func in known_helpers:
                continue
            issues.append(
                {
                    "level": "error",
                    "code": "unknown-helper",
                    "message": (
                        f"Unknown extension {func}(). Put it in extensions/ "
                        "or list it in snapapi.yaml, or pass --plugin."
                    ),
                    "filename": filename,
                    "lineno": lineno,
                }
            )

    unused = saved - used_saves
    for name in sorted(unused):
        issues.append(
            {
                "level": "error" if strict else "warning",
                "code": "unused-save",
                "message": f"SAVE {name} is never referenced",
                "filename": source,
                "lineno": None,
            }
        )
    return issues


def format_issues(issues):
    lines = []
    for issue in issues:
        loc = issue.get("filename") or ""
        if issue.get("lineno"):
            loc = f"{loc}:{issue['lineno']}"
        prefix = "error" if issue["level"] == "error" else "warning"
        where = f"{loc}: " if loc else ""
        lines.append(f"{prefix}: {where}{issue['message']}")
    return "\n".join(lines)


def _lint_helpers(value, known_helpers, issues, filename, lineno):
    for name in _helper_calls_in(value):
        if name in known_helpers:
            continue
        issues.append(
            {
                "level": "error",
                "code": "unknown-helper",
                "message": (
                    f"Unknown helper ${{{name}()}}. Built-ins are uuid, now, random.int. "
                    "Use CALL with a function from extensions/, or pass --plugin."
                ),
                "filename": filename,
                "lineno": lineno,
            }
        )


def _helper_calls_in(value):
    names = set()
    if isinstance(value, str):
        for match in HELPER_RE.finditer(value):
            if match.group(2) is not None or match.group(1) in BUILTIN_HELPERS:
                names.add(match.group(1))
    elif isinstance(value, dict):
        for key, item in value.items():
            names.update(_helper_calls_in(key))
            names.update(_helper_calls_in(item))
    elif isinstance(value, list):
        for item in value:
            names.update(_helper_calls_in(item))
    return names


def _names_in(value):
    names = set()
    if isinstance(value, str):
        names.update(VAR_PATTERN.findall(value))
        for match in HELPER_RE.finditer(value):
            if match.group(2) is None:
                names.add(match.group(1))
    elif isinstance(value, dict):
        for key, item in value.items():
            names.update(_names_in(key))
            names.update(_names_in(item))
    elif isinstance(value, list):
        for item in value:
            names.update(_names_in(item))
    return names


def _is_helper(name, blob, known_helpers=None):
    text = blob if isinstance(blob, str) else str(blob)
    known = known_helpers if known_helpers is not None else BUILTIN_HELPERS
    return bool(re.search(r"\$\{" + re.escape(name) + r"\([^)]*\)\}", text)) or name in known


def _prep_items(owner):
    return list((owner or {}).get("preps") or (owner or {}).get("sets") or [])


def _all_prep_scopes(suite, source):
    yield suite, source, None
    for test in suite.get("tests") or []:
        filename = test.get("source") or source
        yield test, filename, test.get("lineno")
        for step in test.get("steps") or []:
            yield step, filename, step.get("lineno") or test.get("lineno")
