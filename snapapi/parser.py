from __future__ import annotations

import json
import re
from pathlib import Path

from snapapi.exceptions import ParseError

HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
LINE_KEYWORD_RE = re.compile(r"^([A-Z][A-Z0-9_-]*):(.*)$")
KNOWN_KEYWORDS = {
    "SUITE",
    "DESC",
    "URL",
    "OPTIONS",
    "TEST",
    "TAG",
    "SETUP",
    "TEARDOWN",
    "REQUEST",
    "DATA",
    "HEADERS",
    "EXPECT",
    "SAVE",
    "IMPORT",
}
EXPECT_RETRY_RE = re.compile(r"\sRETRY\s+(\d+)\s*$", re.IGNORECASE)
JSON_EXPECT_RE = re.compile(r"^(\S+)\s+(==|!=|CONTAINS)\s+(.+)$", re.DOTALL)
HEADER_EXPECT_RE = re.compile(r"^(\S+)\s+(==|!=|CONTAINS)\s+(.+)$", re.DOTALL)
SAVE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+FROM\s+(\S+)$", re.IGNORECASE)
TAG_SPLIT_RE = re.compile(r"[,\s]+")


class TestParser:
    def parse(self, test_file, _import_stack=None):
        path = Path(test_file)
        if not path.is_file():
            raise ParseError(f"Test file not found: {test_file}", filename=str(test_file))
        resolved = path.resolve()
        stack = list(_import_stack or [])
        if resolved in stack:
            cycle = " -> ".join(str(item) for item in stack + [resolved])
            raise ParseError(f"IMPORT cycle detected: {cycle}", filename=str(resolved))
        text = resolved.read_text(encoding="utf-8")
        return self.parse_text(text, filename=str(resolved), _import_stack=stack + [resolved])

    def parse_text(self, text, filename="<string>", _import_stack=None):
        lines = text.splitlines()
        suite = {
            "name": None,
            "description": None,
            "base_url": None,
            "headers": {},
            "options": {},
            "tests": [],
            "test_map": {},
            "source": filename,
        }
        tests = []
        test_map = {}
        current_test = None
        current_step = None
        i = 0
        import_stack = list(_import_stack or [])

        while i < len(lines):
            lineno = i + 1
            stripped = lines[i].strip()
            if not stripped or stripped.startswith("//"):
                i += 1
                continue

            match = LINE_KEYWORD_RE.match(stripped)
            if not match:
                raise ParseError(
                    "Invalid line (expected KEYWORD: value)",
                    filename=filename,
                    lineno=lineno,
                )

            keyword, rest = match.group(1), match.group(2).strip()
            if keyword not in KNOWN_KEYWORDS:
                raise ParseError(f"Unknown keyword '{keyword}'", filename=filename, lineno=lineno)

            if keyword == "SUITE":
                self._require_no_test(current_test, keyword, filename, lineno)
                suite["name"] = rest
            elif keyword == "DESC":
                if current_test is not None:
                    current_test["description"] = rest
                else:
                    suite["description"] = rest
            elif keyword == "URL":
                if current_test is not None:
                    current_test["base_url"] = rest
                else:
                    suite["base_url"] = rest
            elif keyword == "OPTIONS":
                self._require_no_test(current_test, keyword, filename, lineno)
                payload, i = self._read_json(rest, lines, i, filename, lineno)
                if not isinstance(payload, dict):
                    raise ParseError("OPTIONS must be a JSON object", filename=filename, lineno=lineno)
                suite["options"] = payload
            elif keyword == "IMPORT":
                self._require_no_test(current_test, keyword, filename, lineno)
                imported = self._import_file(rest, filename, lineno, import_stack)
                self._merge_imported(suite, tests, test_map, imported, filename, lineno)
            elif keyword == "TEST":
                if current_test is not None:
                    tests.append(current_test)
                if not rest:
                    raise ParseError("TEST name is required", filename=filename, lineno=lineno)
                if rest in test_map:
                    raise ParseError(f"Duplicate test name '{rest}'", filename=filename, lineno=lineno)
                current_test = {
                    "name": rest,
                    "description": None,
                    "tags": [],
                    "base_url": suite.get("base_url"),
                    "headers": dict(suite.get("headers") or {}),
                    "setup": None,
                    "teardown": None,
                    "steps": [],
                    "source": filename,
                    "lineno": lineno,
                }
                current_step = None
                test_map[rest] = current_test
            elif keyword == "TAG":
                self._require_test(current_test, keyword, filename, lineno)
                current_test["tags"] = [tag for tag in TAG_SPLIT_RE.split(rest) if tag]
            elif keyword == "SETUP":
                self._require_test(current_test, keyword, filename, lineno)
                current_test["setup"] = rest
            elif keyword == "TEARDOWN":
                self._require_test(current_test, keyword, filename, lineno)
                current_test["teardown"] = rest
            elif keyword == "REQUEST":
                self._require_test(current_test, keyword, filename, lineno)
                current_step = self._parse_request(rest, filename, lineno)
                current_test["steps"].append(current_step)
            elif keyword == "DATA":
                self._require_step(current_step, keyword, filename, lineno)
                payload, i = self._read_json(rest, lines, i, filename, lineno)
                current_step["data"] = payload
            elif keyword == "HEADERS":
                payload, i = self._read_json(rest, lines, i, filename, lineno)
                if not isinstance(payload, dict):
                    raise ParseError("HEADERS must be a JSON object", filename=filename, lineno=lineno)
                if current_step is not None:
                    current_step["headers"] = payload
                elif current_test is not None:
                    merged = dict(current_test.get("headers") or {})
                    merged.update(payload)
                    current_test["headers"] = merged
                else:
                    suite["headers"] = payload
            elif keyword == "EXPECT":
                self._require_step(current_step, keyword, filename, lineno)
                current_step["checks"].append(self._parse_expect(rest, filename, lineno))
            elif keyword == "SAVE":
                self._require_step(current_step, keyword, filename, lineno)
                current_step["saves"].append(self._parse_save(rest, filename, lineno))
            i += 1

        if current_test is not None:
            tests.append(current_test)

        if not suite.get("name"):
            source_path = Path(filename)
            suite["name"] = source_path.stem if filename != "<string>" else "Unnamed suite"
        suite["tests"] = tests
        suite["test_map"] = test_map
        suite.setdefault("options", {})
        if "STOP-ON-FAILURE" not in suite["options"]:
            suite["options"]["STOP-ON-FAILURE"] = True
        self._validate_suite(suite, filename)
        return suite

    def _import_file(self, spec, filename, lineno, import_stack):
        if not spec:
            raise ParseError("IMPORT path is required", filename=filename, lineno=lineno)
        base = Path(filename).parent if filename != "<string>" else Path.cwd()
        target = (base / spec).resolve()
        if not target.is_file():
            raise ParseError(f"IMPORT file not found: {spec}", filename=filename, lineno=lineno)
        return self.parse(target, _import_stack=import_stack)

    def _merge_imported(self, suite, tests, test_map, imported, filename, lineno):
        for test in imported["tests"]:
            name = test["name"]
            if name in test_map:
                raise ParseError(
                    f"Duplicate test name '{name}' imported from {imported['source']}",
                    filename=filename,
                    lineno=lineno,
                )
            tests.append(test)
            test_map[name] = test
        if not suite.get("base_url") and imported.get("base_url"):
            suite["base_url"] = imported["base_url"]

    def _parse_request(self, rest, filename, lineno):
        parts = rest.split(None, 1)
        if len(parts) != 2:
            raise ParseError(
                "REQUEST must be in the form: METHOD /path",
                filename=filename,
                lineno=lineno,
            )
        method, endpoint = parts[0].upper(), parts[1]
        if method not in HTTP_METHODS:
            raise ParseError(f"Unknown HTTP method '{method}'", filename=filename, lineno=lineno)
        return {
            "action": method,
            "endpoint": endpoint,
            "data": None,
            "headers": {},
            "checks": [],
            "saves": [],
            "lineno": lineno,
        }

    def _parse_expect(self, rest, filename, lineno):
        if not rest:
            raise ParseError("EXPECT requires a check", filename=filename, lineno=lineno)
        retry = None
        retry_match = EXPECT_RETRY_RE.search(rest)
        if retry_match:
            retry = int(retry_match.group(1))
            if retry < 1:
                raise ParseError("RETRY must be >= 1", filename=filename, lineno=lineno)
            rest = rest[: retry_match.start()].strip()

        kind, _, remainder = rest.partition(" ")
        kind = kind.upper()
        remainder = remainder.strip()
        check = {"retry": retry}
        if kind == "STATUS":
            if not remainder:
                raise ParseError("EXPECT STATUS requires a status code", filename=filename, lineno=lineno)
            check.update({"type": "STATUS", "value": remainder})
        elif kind == "CONTAINS":
            if not remainder:
                raise ParseError("EXPECT CONTAINS requires a value", filename=filename, lineno=lineno)
            check.update({"type": "CONTAINS", "value": _strip_quotes(remainder)})
        elif kind == "JSON":
            match = JSON_EXPECT_RE.match(remainder)
            if not match:
                raise ParseError(
                    'EXPECT JSON must look like: JSON $.path == "value"',
                    filename=filename,
                    lineno=lineno,
                )
            check.update({
                "type": "JSON",
                "path": match.group(1),
                "operator": match.group(2).upper() if match.group(2).upper() == "CONTAINS" else match.group(2),
                "value": _parse_expect_value(match.group(3).strip()),
            })
        elif kind == "HEADER":
            match = HEADER_EXPECT_RE.match(remainder)
            if not match:
                raise ParseError(
                    "EXPECT HEADER must look like: HEADER Content-Type CONTAINS json",
                    filename=filename,
                    lineno=lineno,
                )
            operator = match.group(2)
            check.update({
                "type": "HEADER",
                "name": match.group(1),
                "operator": operator.upper() if operator.upper() == "CONTAINS" else operator,
                "value": _strip_quotes(match.group(3).strip()),
            })
        else:
            raise ParseError(f"Unknown EXPECT check '{kind}'", filename=filename, lineno=lineno)
        return check

    def _parse_save(self, rest, filename, lineno):
        match = SAVE_RE.match(rest)
        if not match:
            raise ParseError(
                "SAVE must look like: SAVE: name FROM $.path",
                filename=filename,
                lineno=lineno,
            )
        return {"name": match.group(1), "path": match.group(2)}

    def _read_json(self, rest, lines, index, filename, lineno):
        buf = rest
        last = index
        while not _json_complete(buf):
            last += 1
            if last >= len(lines):
                raise ParseError("Unterminated JSON value", filename=filename, lineno=lineno)
            nxt = lines[last]
            stripped = nxt.strip()
            if not stripped or stripped.startswith("//"):
                continue
            buf = buf + "\n" + nxt
        try:
            return json.loads(buf), last
        except json.JSONDecodeError as exc:
            raise ParseError(f"Invalid JSON: {exc.msg}", filename=filename, lineno=lineno) from exc

    def _validate_suite(self, suite, filename):
        test_map = suite["test_map"]
        for test in suite["tests"]:
            for field in ("setup", "teardown"):
                name = test.get(field)
                if name and name not in test_map:
                    raise ParseError(
                        f"Unknown {field.upper()} test '{name}' referenced by '{test['name']}'",
                        filename=test.get("source") or filename,
                        lineno=test.get("lineno"),
                    )
        self._detect_cycles(test_map, filename)

    def _detect_cycles(self, test_map, filename):
        visiting = []
        seen = set()

        def visit(name):
            if name in visiting:
                cycle = visiting[visiting.index(name):] + [name]
                raise ParseError(
                    "SETUP/TEARDOWN cycle detected: " + " -> ".join(cycle),
                    filename=filename,
                )
            if name in seen or name not in test_map:
                return
            visiting.append(name)
            test = test_map[name]
            if test.get("setup"):
                visit(test["setup"])
            if test.get("teardown"):
                visit(test["teardown"])
            visiting.pop()
            seen.add(name)

        for name in test_map:
            visit(name)

    def _require_test(self, current_test, keyword, filename, lineno):
        if current_test is None:
            raise ParseError(f"{keyword} must appear inside a TEST", filename=filename, lineno=lineno)

    def _require_step(self, current_step, keyword, filename, lineno):
        if current_step is None:
            raise ParseError(
                f"{keyword} must follow a REQUEST",
                filename=filename,
                lineno=lineno,
            )

    def _require_no_test(self, current_test, keyword, filename, lineno):
        if current_test is not None:
            raise ParseError(
                f"{keyword} must appear at suite level (before TEST)",
                filename=filename,
                lineno=lineno,
            )


def _strip_quotes(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_expect_value(raw):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return _strip_quotes(raw)


def _json_complete(text):
    in_string = False
    escape = False
    depth = 0
    started = False
    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char.isspace():
            continue
        if char == '"':
            in_string = True
            started = True
            continue
        if char in "{[":
            depth += 1
            started = True
        elif char in "}]":
            depth -= 1
            if depth < 0:
                return False
            started = True
        else:
            started = True
    return started and depth == 0 and not in_string
