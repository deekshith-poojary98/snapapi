from __future__ import annotations

import re
from pathlib import Path

from snapapi.exceptions import ParseError
from snapapi.helpers import HELPER_RE
from snapapi.parser import TestParser
from snapapi.variables import VAR_PATTERN

SAVE_NAME_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def lint_files(files, variables=None, strict=False):
    parser = TestParser()
    env = dict(variables or {})
    issues = []
    for path in files:
        issues.extend(lint_suite(parser.parse(path), env, strict=strict))
    return issues


def lint_suite(suite, variables=None, strict=False):
    env = set((variables or {}).keys())
    issues = []
    saved = set()
    used_saves = set()
    source = suite.get("source") or "<string>"

    for test in suite.get("tests") or []:
        for step in test.get("steps") or []:
            for save in step.get("saves") or []:
                saved.add(save["name"])
            blobs = [
                test.get("base_url"),
                step.get("endpoint"),
                step.get("data"),
                step.get("headers"),
                step.get("query"),
                step.get("raw_body"),
            ]
            for blob in blobs:
                for name in _names_in(blob):
                    if name in saved:
                        used_saves.add(name)
                    if name in env or name in saved or _is_helper(name, blob):
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
                    for name in _names_in(blob):
                        if name in saved:
                            used_saves.add(name)

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


def _is_helper(name, blob):
    text = blob if isinstance(blob, str) else str(blob)
    return bool(re.search(r"\$\{" + re.escape(name) + r"\([^)]*\)\}", text)) or name in {
        "uuid",
        "now",
        "random.int",
    }
