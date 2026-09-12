from __future__ import annotations

import csv
import difflib
import io
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl

from snapapi.exceptions import ParseError

HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
REQUEST_METHODS = set(HTTP_METHODS)
LINE_KEYWORD_RE = re.compile(r"^([A-Z][A-Z0-9_-]*):(.*)$")
HEADER_LINE_RE = re.compile(r"^HEADER\s+(\S+)\s*:\s*(.*)$")
SPACED_KEYWORD_RE = re.compile(r"^([A-Z][A-Z0-9_-]*(?:\s+[A-Z][A-Z0-9_-]*)+)\s*:(.*)$")
KNOWN_KEYWORDS = {
    "SUITE",
    "DESC",
    "URL",
    "OPTIONS",
    "TIMEOUT",
    "FOLLOW-REDIRECTS",
    "SUITE-SETUP",
    "SUITE-TEARDOWN",
    "TEST",
    "HELPER",
    "TAG",
    "SETUP",
    "TEARDOWN",
    "DEPENDS",
    "REQUEST",
    "DATA",
    "BODY",
    "HEADERS",
    "HEADER",
    "QUERY",
    "PARAM",
    "AUTH",
    "EXPECT",
    "SAVE",
    "IMPORT",
    "FILE",
    "GRAPHQL",
    "EXAMPLES",
    "SKIP",
    "ONLY",
    "QUARANTINE",
    "WAIT",
    "SET",
} | set(HTTP_METHODS)
EXPECT_RETRY_RE = re.compile(
    r"\sRETRY\s+(\d+)(?:\s+ON\s+(\S+))?(?:\s+BACKOFF\s+(\S+))?\s*$",
    re.IGNORECASE,
)
WAIT_TAIL_RE = re.compile(
    r"\sTIMEOUT\s+(\S+)(?:\s+BACKOFF\s+(\S+))?\s*$",
    re.IGNORECASE,
)
EXPECT_KIND_RE = re.compile(
    r"^(STATUS|CONTAINS|JSON|HEADER|BODY|SCHEMA|DURATION|OPENAPI|XPATH)(?:\s+|(?==)|$)(.*)$",
    re.IGNORECASE | re.DOTALL,
)
JSON_LENGTH_RE = re.compile(
    r"^(\S+)\s+length\s+(==|!=|>=|<=|>|<)\s+(.+)$",
    re.DOTALL | re.IGNORECASE,
)
JSON_EACH_RE = re.compile(
    r"^(.+?)\s+each\s+(\S+)\s+(==|!=|CONTAINS|MATCHES|>=|<=|>|<)\s+(.+)$",
    re.DOTALL | re.IGNORECASE,
)
JSON_CONTAINS_ALL_RE = re.compile(
    r"^(.+?)\s+contains(?:-|\s+)all\s+(.+)$",
    re.DOTALL | re.IGNORECASE,
)
JSON_EXPECT_RE = re.compile(
    r"^(\S+)\s+(==|!=|CONTAINS|MATCHES|>=|<=|>|<)\s+(.+)$",
    re.DOTALL | re.IGNORECASE,
)
XPATH_EXPECT_RE = re.compile(
    r"^(\S+)\s+(==|!=|CONTAINS)\s+(.+)$",
    re.DOTALL | re.IGNORECASE,
)
HEADER_EXPECT_RE = re.compile(r"^(\S+)\s+(==|!=|CONTAINS)\s+(.+)$", re.DOTALL | re.IGNORECASE)
STATUS_VALUE_RE = re.compile(r"^(==|!=)?\s*(.+)$", re.DOTALL)
BODY_CONTAINS_RE = re.compile(r"^(not\s+)?contains\s+(.+)$", re.IGNORECASE | re.DOTALL)
DURATION_RE = re.compile(r"^(<=|>=|<|>|==)\s*(\d+(?:\.\d+)?)(ms|s)?$", re.IGNORECASE)
SAVE_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)\s+FROM\s+(header|cookie|json)?\s*(.+)$",
    re.IGNORECASE,
)
FILE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+FROM\s+(.+)$", re.IGNORECASE)
TAG_SPLIT_RE = re.compile(r"[,\s]+")
AUTH_SCHEMES = {
    "bearer": "Bearer",
    "basic": "Basic",
    "digest": "Digest",
    "token": "Bearer",
}


def compact_keyword(raw):
    return "-".join(part for part in re.split(r"[\s_]+", raw) if part)


def suggest_keyword(raw, known=None):
    known = KNOWN_KEYWORDS if known is None else known
    compacted = compact_keyword(raw)
    if compacted in known:
        return compacted
    if " " not in raw and "-" not in raw:
        return None
    matches = difflib.get_close_matches(compacted, sorted(known), n=1, cutoff=0.7)
    return matches[0] if matches else None


def unknown_keyword_message(raw):
    hint = suggest_keyword(raw)
    if " " in raw:
        if hint:
            if hint == compact_keyword(raw):
                return f"Unknown keyword '{raw}'. Keywords cannot contain spaces; use {hint}"
            return f"Unknown keyword '{raw}'. Keywords cannot contain spaces; did you mean {hint}?"
        return f"Unknown keyword '{raw}'. Keywords cannot contain spaces"
    if hint:
        return f"Unknown keyword '{raw}'. Did you mean {hint}?"
    return f"Unknown keyword '{raw}'"


BOOL_OP_RE = re.compile(r"\s*(AND|OR)\b", re.IGNORECASE)


def walk_expect_checks(check):
    if check.get("type") in ("AND", "OR"):
        for term in check.get("terms") or []:
            yield from walk_expect_checks(term)
    else:
        yield check


def _helper_names(suite):
    names = set()
    if suite.get("setup"):
        names.add(suite["setup"])
    if suite.get("teardown"):
        names.add(suite["teardown"])
    for test in suite.get("tests") or []:
        if test.get("kind") == "helper":
            names.add(test["name"])
        if test.get("setup"):
            names.add(test["setup"])
        if test.get("teardown"):
            names.add(test["teardown"])
    return names


class _ExpectExprParser:
    def __init__(self, text, filename, lineno, parse_atomic):
        self.text = text
        self.filename = filename
        self.lineno = lineno
        self.parse_atomic = parse_atomic
        self.i = 0

    def parse(self):
        node = self._parse_or()
        self._skip_ws()
        if self.i < len(self.text):
            raise ParseError(
                f"Unexpected extra EXPECT text '{self.text[self.i:].strip()}'",
                filename=self.filename,
                lineno=self.lineno,
            )
        return node

    def _parse_or(self):
        terms = [self._parse_and()]
        while self._consume_op("OR"):
            terms.append(self._parse_and())
        if len(terms) == 1:
            return terms[0]
        return {"type": "OR", "terms": terms}

    def _parse_and(self):
        terms = [self._parse_primary()]
        while self._consume_op("AND"):
            terms.append(self._parse_primary())
        if len(terms) == 1:
            return terms[0]
        return {"type": "AND", "terms": terms}

    def _parse_primary(self):
        self._skip_ws()
        if self.i >= len(self.text):
            raise ParseError("EXPECT requires a check", filename=self.filename, lineno=self.lineno)
        if self.text[self.i] == "(":
            self.i += 1
            node = self._parse_or()
            self._skip_ws()
            if self.i >= len(self.text) or self.text[self.i] != ")":
                raise ParseError("Unbalanced parentheses in EXPECT", filename=self.filename, lineno=self.lineno)
            self.i += 1
            return node
        end = self._atomic_end()
        slice_ = self.text[self.i : end].strip()
        if not slice_:
            raise ParseError("EXPECT requires a check", filename=self.filename, lineno=self.lineno)
        self.i = end
        return self.parse_atomic(slice_)

    def _consume_op(self, wanted):
        match = BOOL_OP_RE.match(self.text[self.i :])
        if not match or match.group(1).upper() != wanted:
            return False
        self.i += match.end()
        return True

    def _skip_ws(self):
        while self.i < len(self.text) and self.text[self.i].isspace():
            self.i += 1

    def _atomic_end(self):
        i = self.i
        quote = None
        escape = False
        brackets = 0
        parens = 0
        length = len(self.text)
        while i < length:
            ch = self.text[i]
            if quote:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == quote:
                    quote = None
                i += 1
                continue
            if ch in "\"'":
                quote = ch
                i += 1
                continue
            if ch == "[":
                brackets += 1
                i += 1
                continue
            if ch == "]" and brackets:
                brackets -= 1
                i += 1
                continue
            if ch == "(":
                parens += 1
                i += 1
                continue
            if ch == ")":
                if parens:
                    parens -= 1
                    i += 1
                    continue
                return i
            if brackets == 0 and parens == 0:
                op = re.match(r"\s+(AND|OR)\b", self.text[i:], re.IGNORECASE)
                if op:
                    return i
            i += 1
        return i


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
            "setup": None,
            "teardown": None,
            "follow_redirects": None,
            "oauth2": None,
            "digest": None,
            "sets": [],
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

            keyword, rest = self._split_keyword(stripped, filename, lineno)
            if keyword not in KNOWN_KEYWORDS:
                raise ParseError(unknown_keyword_message(keyword), filename=filename, lineno=lineno)

            if keyword == "SUITE":
                current_test, current_step = self._close_open_helper(current_test, current_step, tests)
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
                current_test, current_step = self._close_open_helper(current_test, current_step, tests)
                self._require_no_test(current_test, keyword, filename, lineno)
                payload, i = self._read_json(rest, lines, i, filename, lineno)
                if not isinstance(payload, dict):
                    raise ParseError("OPTIONS must be a JSON object", filename=filename, lineno=lineno)
                payload = dict(payload)
                payload.pop("STOP-ON-FAILURE", None)
                suite["options"].update(payload)
            elif keyword == "TIMEOUT":
                current_test, current_step = self._close_open_helper(current_test, current_step, tests)
                self._require_no_test(current_test, keyword, filename, lineno)
                suite["options"]["TIMEOUT"] = self._parse_timeout(rest, filename, lineno)
            elif keyword == "FOLLOW-REDIRECTS":
                value = self._parse_bool(rest, keyword, filename, lineno)
                if current_step is not None:
                    current_step["follow_redirects"] = value
                else:
                    current_test, current_step = self._close_open_helper(current_test, current_step, tests)
                    self._require_no_test(current_test, keyword, filename, lineno)
                    suite["follow_redirects"] = value
                    suite["options"]["FOLLOW-REDIRECTS"] = value
            elif keyword == "SUITE-SETUP":
                current_test, current_step = self._close_open_helper(current_test, current_step, tests)
                self._require_no_test(current_test, keyword, filename, lineno)
                suite["setup"] = rest
            elif keyword == "SUITE-TEARDOWN":
                current_test, current_step = self._close_open_helper(current_test, current_step, tests)
                self._require_no_test(current_test, keyword, filename, lineno)
                suite["teardown"] = rest
            elif keyword == "IMPORT":
                current_test, current_step = self._close_open_helper(current_test, current_step, tests)
                self._require_no_test(current_test, keyword, filename, lineno)
                imported = self._import_file(rest, filename, lineno, import_stack)
                self._merge_imported(suite, tests, test_map, imported, filename, lineno)
            elif keyword in ("TEST", "HELPER"):
                if current_test is not None:
                    tests.append(current_test)
                if not rest:
                    raise ParseError(f"{keyword} name is required", filename=filename, lineno=lineno)
                if rest in test_map:
                    raise ParseError(f"Duplicate name '{rest}'", filename=filename, lineno=lineno)
                current_test = {
                    "name": rest,
                    "kind": "helper" if keyword == "HELPER" else "test",
                    "description": None,
                    "tags": [],
                    "base_url": suite.get("base_url"),
                    "headers": dict(suite.get("headers") or {}),
                    "setup": None,
                    "teardown": None,
                    "depends": [],
                    "steps": [],
                    "examples": [],
                    "skip": None,
                    "only": False,
                    "quarantine": None,
                    "sets": [],
                    "digest": None,
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
            elif keyword == "DEPENDS":
                self._require_primary(current_test, keyword, filename, lineno)
                for name in self._parse_depends(rest, filename, lineno):
                    if name not in current_test["depends"]:
                        current_test["depends"].append(name)
            elif keyword == "REQUEST":
                self._require_test(current_test, keyword, filename, lineno)
                current_step = self._parse_request(rest, filename, lineno)
                current_test["steps"].append(current_step)
            elif keyword == "HEAD" or (keyword in HTTP_METHODS and keyword != "OPTIONS"):
                self._require_test(current_test, keyword, filename, lineno)
                if not rest:
                    raise ParseError(f"{keyword} requires a path", filename=filename, lineno=lineno)
                current_step = self._new_step(keyword, rest, lineno)
                current_test["steps"].append(current_step)
            elif keyword in ("DATA", "BODY"):
                self._require_step(current_step, keyword, filename, lineno)
                i = self._parse_body(current_step, rest, lines, i, filename, lineno)
            elif keyword == "FILE":
                self._require_step(current_step, keyword, filename, lineno)
                current_step["files"].append(self._parse_file(rest, filename, lineno))
            elif keyword == "GRAPHQL":
                self._require_step(current_step, keyword, filename, lineno)
                payload, i = self._read_json(rest, lines, i, filename, lineno)
                current_step["body_type"] = "graphql"
                current_step["data"] = payload
            elif keyword == "EXAMPLES":
                self._require_primary(current_test, keyword, filename, lineno)
                rows, i = self._parse_examples(rest, lines, i, filename, lineno)
                current_test["examples"] = rows
            elif keyword == "SKIP":
                self._require_primary(current_test, keyword, filename, lineno)
                current_test["skip"] = rest or "skipped"
            elif keyword == "ONLY":
                self._require_primary(current_test, keyword, filename, lineno)
                current_test["only"] = True
            elif keyword == "QUARANTINE":
                self._require_primary(current_test, keyword, filename, lineno)
                current_test["quarantine"] = rest or "quarantine"
            elif keyword == "HEADERS":
                payload, i = self._read_json(rest, lines, i, filename, lineno)
                if not isinstance(payload, dict):
                    raise ParseError("HEADERS must be a JSON object", filename=filename, lineno=lineno)
                self._apply_headers(payload, suite, current_test, current_step)
            elif keyword == "HEADER":
                name, value = self._parse_header_line(rest, filename, lineno)
                self._apply_headers({name: value}, suite, current_test, current_step)
            elif keyword == "QUERY":
                self._require_step(current_step, keyword, filename, lineno)
                self._apply_query(current_step, self._parse_query(rest, filename, lineno))
            elif keyword == "PARAM":
                self._require_step(current_step, keyword, filename, lineno)
                name, value = self._parse_param(rest, filename, lineno)
                current_step["query"][name] = value
            elif keyword == "AUTH":
                headers = self._parse_auth(rest, filename, lineno)
                if "_oauth2" in headers:
                    oauth = headers["_oauth2"]
                    if current_step is not None:
                        current_step["oauth2"] = oauth
                    elif current_test is not None:
                        current_test["oauth2"] = oauth
                    else:
                        suite["oauth2"] = oauth
                elif "_digest" in headers:
                    digest = headers["_digest"]
                    if current_step is not None:
                        current_step["digest"] = digest
                    elif current_test is not None:
                        current_test["digest"] = digest
                    else:
                        suite["digest"] = digest
                else:
                    self._apply_headers(headers, suite, current_test, current_step)
            elif keyword == "EXPECT":
                self._require_step(current_step, keyword, filename, lineno)
                current_step["checks"].append(self._parse_expect(rest, filename, lineno))
            elif keyword == "SAVE":
                self._require_step(current_step, keyword, filename, lineno)
                current_step["saves"].append(self._parse_save(rest, filename, lineno))
            elif keyword == "WAIT":
                self._require_step(current_step, keyword, filename, lineno)
                current_step["wait"] = self._parse_wait(rest, filename, lineno)
            elif keyword == "SET":
                item = self._parse_set(rest, filename, lineno)
                if current_step is not None:
                    current_step.setdefault("sets", []).append(item)
                elif current_test is not None:
                    current_test.setdefault("sets", []).append(item)
                else:
                    suite.setdefault("sets", []).append(item)
            i += 1

        if current_test is not None:
            tests.append(current_test)

        if not suite.get("name"):
            source_path = Path(filename)
            suite["name"] = source_path.stem if filename != "<string>" else "Unnamed suite"
        suite["tests"] = tests
        suite["test_map"] = test_map
        suite.setdefault("options", {})
        self._validate_suite(suite, filename)
        return suite

    def _split_keyword(self, stripped, filename, lineno):
        match = LINE_KEYWORD_RE.match(stripped)
        if match:
            return match.group(1), match.group(2).strip()
        header_match = HEADER_LINE_RE.match(stripped)
        if header_match:
            return "HEADER", f"{header_match.group(1)}: {header_match.group(2)}"
        spaced = SPACED_KEYWORD_RE.match(stripped)
        if spaced:
            return spaced.group(1), spaced.group(2).strip()
        raise ParseError(
            "Invalid line (expected KEYWORD: value)",
            filename=filename,
            lineno=lineno,
        )

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
        if method not in REQUEST_METHODS:
            raise ParseError(f"Unknown HTTP method '{method}'", filename=filename, lineno=lineno)
        return self._new_step(method, endpoint, lineno)

    def _new_step(self, method, endpoint, lineno):
        return {
            "action": method,
            "endpoint": endpoint,
            "data": None,
            "raw_body": None,
            "content_type": None,
            "body_type": "json",
            "files": [],
            "headers": {},
            "query": {},
            "checks": [],
            "saves": [],
            "follow_redirects": None,
            "wait": None,
            "sets": [],
            "digest": None,
            "lineno": lineno,
        }

    def _apply_headers(self, payload, suite, current_test, current_step):
        headers = {str(key): value for key, value in payload.items()}
        if current_step is not None:
            current_step["headers"].update(headers)
        elif current_test is not None:
            current_test["headers"].update(headers)
        else:
            suite["headers"].update(headers)

    def _apply_query(self, step, params):
        step["query"].update(params)

    def _parse_header_line(self, rest, filename, lineno):
        if ":" not in rest:
            raise ParseError(
                "HEADER must look like: HEADER Name: value",
                filename=filename,
                lineno=lineno,
            )
        name, _, value = rest.partition(":")
        name = name.strip()
        if not name:
            raise ParseError(
                "HEADER must look like: HEADER Name: value",
                filename=filename,
                lineno=lineno,
            )
        return name, value.strip()

    def _parse_query(self, rest, filename, lineno):
        if not rest:
            raise ParseError("QUERY requires parameters", filename=filename, lineno=lineno)
        pairs = parse_qsl(rest, keep_blank_values=True)
        if not pairs:
            raise ParseError(
                "QUERY must look like: QUERY: page=2&limit=10",
                filename=filename,
                lineno=lineno,
            )
        return dict(pairs)

    def _parse_param(self, rest, filename, lineno):
        if not rest:
            raise ParseError("PARAM requires a name and value", filename=filename, lineno=lineno)
        if "=" in rest:
            name, _, value = rest.partition("=")
            name = name.strip()
            if name and " " not in name:
                return name, value.strip()
        parts = rest.split(None, 1)
        if len(parts) != 2:
            raise ParseError(
                "PARAM must look like: PARAM: page 2",
                filename=filename,
                lineno=lineno,
            )
        return parts[0], parts[1]

    def _parse_auth(self, rest, filename, lineno):
        if not rest:
            raise ParseError("AUTH requires a scheme and value", filename=filename, lineno=lineno)
        parts = rest.split(None, 1)
        scheme = parts[0].lower()
        if scheme == "oauth2":
            params = dict(parse_qsl(parts[1].replace(" ", "&") if len(parts) == 2 else "", keep_blank_values=True))
            if len(parts) == 2 and not params:
                for item in parts[1].split():
                    if "=" in item:
                        key, _, value = item.partition("=")
                        params[key.strip()] = value.strip()
            if not params.get("token_url") or not params.get("client_id"):
                raise ParseError(
                    "AUTH oauth2 requires token_url and client_id",
                    filename=filename,
                    lineno=lineno,
                )
            grant = (params.get("grant") or params.get("grant_type") or "client_credentials").lower()
            if grant in ("authorization-code", "authorization_code"):
                grant = "authorization_code"
            if grant == "password" and (not params.get("username") or not params.get("password")):
                raise ParseError(
                    "AUTH oauth2 grant=password requires username and password",
                    filename=filename,
                    lineno=lineno,
                )
            if grant not in ("password", "client_credentials", "authorization_code"):
                raise ParseError(
                    "AUTH oauth2 supports grant=password, grant=client_credentials, or grant=authorization_code",
                    filename=filename,
                    lineno=lineno,
                )
            params.setdefault("grant", grant)
            return {"_oauth2": params}
        if len(parts) != 2:
            raise ParseError(
                "AUTH must look like: AUTH: bearer <token>",
                filename=filename,
                lineno=lineno,
            )
        scheme, value = parts[0], parts[1]
        if scheme.lower() == "digest":
            if ":" not in value:
                raise ParseError(
                    "AUTH digest must look like: AUTH: digest user:pass",
                    filename=filename,
                    lineno=lineno,
                )
            username, _, password = value.partition(":")
            if not username:
                raise ParseError(
                    "AUTH digest must look like: AUTH: digest user:pass",
                    filename=filename,
                    lineno=lineno,
                )
            return {"_digest": {"username": username, "password": password}}
        if scheme.lower() == "basic" and ":" in value:
            import base64

            token = base64.b64encode(value.encode("utf-8")).decode("ascii")
            return {"Authorization": f"Basic {token}"}
        header_scheme = AUTH_SCHEMES.get(scheme.lower(), scheme)
        return {"Authorization": f"{header_scheme} {value}"}

    def _parse_timeout(self, rest, filename, lineno):
        if not rest:
            raise ParseError("TIMEOUT requires a number", filename=filename, lineno=lineno)
        try:
            value = json.loads(rest)
        except json.JSONDecodeError:
            try:
                value = float(rest)
            except ValueError as exc:
                raise ParseError("TIMEOUT must be a number", filename=filename, lineno=lineno) from exc
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ParseError("TIMEOUT must be a number", filename=filename, lineno=lineno)
        return value

    def _parse_bool(self, rest, keyword, filename, lineno):
        if not rest:
            raise ParseError(f"{keyword} requires a boolean", filename=filename, lineno=lineno)
        lowered = rest.strip().lower()
        if lowered in ("true", "yes", "on", "1"):
            return True
        if lowered in ("false", "no", "off", "0"):
            return False
        try:
            value = json.loads(rest)
        except json.JSONDecodeError as exc:
            raise ParseError(f"{keyword} must be a boolean", filename=filename, lineno=lineno) from exc
        if isinstance(value, bool):
            return value
        raise ParseError(f"{keyword} must be a boolean", filename=filename, lineno=lineno)

    def _parse_expect(self, rest, filename, lineno):
        if not rest:
            raise ParseError("EXPECT requires a check", filename=filename, lineno=lineno)
        retry = None
        retry_on = None
        retry_backoff = None
        retry_match = EXPECT_RETRY_RE.search(rest)
        if retry_match:
            retry = int(retry_match.group(1))
            if retry < 1:
                raise ParseError("RETRY must be >= 1", filename=filename, lineno=lineno)
            retry_on = (retry_match.group(2) or "").lower() or None
            if retry_match.group(3):
                retry_backoff = _parse_duration_seconds(retry_match.group(3), filename, lineno)
            rest = rest[: retry_match.start()].strip()

        node = _ExpectExprParser(rest, filename, lineno, lambda slice_: self._parse_expect_atomic(slice_, filename, lineno)).parse()
        node["retry"] = retry
        node["retry_on"] = retry_on
        node["retry_backoff"] = retry_backoff
        return node

    def _parse_expect_atomic(self, rest, filename, lineno):
        kind_match = EXPECT_KIND_RE.match(rest)
        if not kind_match:
            kind, _, _remainder = rest.partition(" ")
            raise ParseError(f"Unknown EXPECT check '{kind.upper()}'", filename=filename, lineno=lineno)

        kind = kind_match.group(1).upper()
        remainder = kind_match.group(2).strip()
        check = {}
        if kind == "STATUS":
            if not remainder:
                raise ParseError("EXPECT STATUS requires a status code", filename=filename, lineno=lineno)
            status_match = STATUS_VALUE_RE.match(remainder)
            operator = "=="
            value = remainder
            if status_match:
                operator = status_match.group(1) or "=="
                value = status_match.group(2).strip()
            if not value:
                raise ParseError("EXPECT STATUS requires a status code", filename=filename, lineno=lineno)
            check.update({"type": "STATUS", "operator": operator, "value": _strip_quotes(value)})
        elif kind in ("CONTAINS", "BODY"):
            negated = False
            value = remainder
            if kind == "BODY":
                body_match = BODY_CONTAINS_RE.match(remainder)
                if not body_match:
                    raise ParseError(
                        "EXPECT body must look like: body contains <text>",
                        filename=filename,
                        lineno=lineno,
                    )
                negated = bool(body_match.group(1))
                value = body_match.group(2).strip()
            elif remainder.lower().startswith("not contains"):
                negated = True
                value = remainder[12:].strip()
            if not value:
                raise ParseError("EXPECT CONTAINS requires a value", filename=filename, lineno=lineno)
            check.update({"type": "CONTAINS", "value": _strip_quotes(value), "negated": negated})
        elif kind == "JSON":
            length_match = JSON_LENGTH_RE.match(remainder)
            each_match = JSON_EACH_RE.match(remainder)
            contains_all_match = JSON_CONTAINS_ALL_RE.match(remainder)
            if length_match:
                check.update({
                    "type": "JSON",
                    "path": length_match.group(1),
                    "operator": "length " + length_match.group(2),
                    "value": _parse_expect_value(length_match.group(3).strip()),
                })
            elif each_match:
                operator = each_match.group(3)
                check.update({
                    "type": "JSON",
                    "path": each_match.group(1).strip(),
                    "operator": "EACH",
                    "each_path": each_match.group(2).strip(),
                    "each_operator": operator.upper() if operator.upper() in ("CONTAINS", "MATCHES") else operator,
                    "value": _parse_expect_value(each_match.group(4).strip()),
                })
            elif contains_all_match:
                check.update({
                    "type": "JSON",
                    "path": contains_all_match.group(1).strip(),
                    "operator": "CONTAINS-ALL",
                    "value": _parse_expect_value(contains_all_match.group(2).strip()),
                })
            else:
                match = JSON_EXPECT_RE.match(remainder)
                if not match:
                    raise ParseError(
                        'EXPECT JSON must look like: JSON $.path == "value"',
                        filename=filename,
                        lineno=lineno,
                    )
                operator = match.group(2)
                check.update({
                    "type": "JSON",
                    "path": match.group(1),
                    "operator": operator.upper() if operator.upper() in ("CONTAINS", "MATCHES") else operator,
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
        elif kind == "SCHEMA":
            if remainder.lower().startswith("inline"):
                payload, _ = self._read_json(remainder[6:].strip(), [remainder[6:].strip()], 0, filename, lineno)
                check.update({"type": "SCHEMA", "mode": "inline", "schema": payload})
            else:
                if not remainder:
                    raise ParseError("EXPECT schema requires a path or inline JSON", filename=filename, lineno=lineno)
                check.update({"type": "SCHEMA", "mode": "file", "path": remainder})
        elif kind == "DURATION":
            match = DURATION_RE.match(remainder.replace(" ", ""))
            if not match:
                raise ParseError(
                    "EXPECT duration must look like: duration < 200ms",
                    filename=filename,
                    lineno=lineno,
                )
            unit = (match.group(3) or "ms").lower()
            amount = float(match.group(2))
            ms = amount if unit == "ms" else amount * 1000
            check.update({"type": "DURATION", "operator": match.group(1), "value": ms})
        elif kind == "OPENAPI":
            if not remainder:
                raise ParseError("EXPECT openapi requires a spec path", filename=filename, lineno=lineno)
            spec_path, strict = _parse_openapi_spec(remainder)
            if not spec_path:
                raise ParseError("EXPECT openapi requires a spec path", filename=filename, lineno=lineno)
            check.update({"type": "OPENAPI", "path": spec_path, "strict": strict})
        elif kind == "XPATH":
            match = XPATH_EXPECT_RE.match(remainder)
            if not match:
                raise ParseError(
                    'EXPECT xpath must look like: xpath //Order/@id == "1"',
                    filename=filename,
                    lineno=lineno,
                )
            operator = match.group(2)
            check.update({
                "type": "XPATH",
                "path": match.group(1),
                "operator": operator.upper() if operator.upper() == "CONTAINS" else operator,
                "value": _parse_expect_value(match.group(3).strip()),
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
        source = (match.group(2) or "json").lower()
        selector = match.group(3).strip()
        if source == "json" and selector.lower().startswith("header "):
            source = "header"
            selector = selector[7:].strip()
        if source == "json" and selector.lower().startswith("cookie "):
            source = "cookie"
            selector = selector[7:].strip()
        if source == "json" and not selector.startswith("$") and selector.lower() in ("header", "cookie"):
            raise ParseError("SAVE header/cookie requires a name", filename=filename, lineno=lineno)
        return {"name": match.group(1), "source": source, "path": selector}

    def _parse_wait(self, rest, filename, lineno):
        if not rest:
            raise ParseError("WAIT requires a check", filename=filename, lineno=lineno)
        timeout = 10.0
        backoff = 0.5
        match = WAIT_TAIL_RE.search(rest)
        if match:
            timeout = _parse_duration_seconds(match.group(1), filename, lineno)
            if match.group(2):
                backoff = _parse_duration_seconds(match.group(2), filename, lineno)
            rest = rest[: match.start()].strip()
        check = self._parse_expect(rest, filename, lineno)
        return {"check": check, "timeout": timeout, "backoff": backoff}

    def _parse_set(self, rest, filename, lineno):
        if not rest:
            raise ParseError("SET requires a name and value", filename=filename, lineno=lineno)
        parts = rest.split(None, 1)
        if len(parts) != 2:
            raise ParseError(
                "SET must look like: SET: name ${uuid()}",
                filename=filename,
                lineno=lineno,
            )
        name, value = parts
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            raise ParseError("SET name must be an identifier", filename=filename, lineno=lineno)
        return {"name": name, "value": value}

    def _parse_file(self, rest, filename, lineno):
        match = FILE_RE.match(rest)
        if not match:
            raise ParseError(
                "FILE must look like: FILE: field FROM ./path",
                filename=filename,
                lineno=lineno,
            )
        return {"field": match.group(1), "path": match.group(2).strip()}

    def _parse_body(self, step, rest, lines, index, filename, lineno):
        lowered = rest.lstrip().lower()
        if lowered.startswith("form"):
            payload = rest[4:].strip()
            if not payload:
                raise ParseError("BODY form requires key=value pairs", filename=filename, lineno=lineno)
            step["body_type"] = "form"
            step["data"] = dict(parse_qsl(payload, keep_blank_values=True))
            return index
        if lowered.startswith("raw"):
            remainder = rest[3:].strip()
            parts = remainder.split(None, 1)
            if not parts:
                raise ParseError("BODY raw requires a content type and body", filename=filename, lineno=lineno)
            step["body_type"] = "raw"
            step["content_type"] = parts[0]
            step["raw_body"] = parts[1] if len(parts) == 2 else ""
            return index
        payload, last = self._read_json(rest, lines, index, filename, lineno)
        step["body_type"] = "json"
        step["data"] = payload
        return last

    def _parse_examples(self, rest, lines, index, filename, lineno):
        if rest and not rest.startswith("{") and "\n" not in rest and Path(rest).suffix.lower() == ".csv":
            base = Path(filename).parent if filename != "<string>" else Path.cwd()
            path = (base / rest).resolve()
            if not path.is_file():
                raise ParseError(f"EXAMPLES file not found: {rest}", filename=filename, lineno=lineno)
            return _read_csv(path.read_text(encoding="utf-8")), index
        buf = [rest] if rest else []
        last = index
        while last + 1 < len(lines):
            nxt = lines[last + 1]
            stripped = nxt.strip()
            if not stripped or stripped.startswith("//"):
                last += 1
                continue
            if LINE_KEYWORD_RE.match(stripped) or HEADER_LINE_RE.match(stripped):
                break
            buf.append(stripped)
            last += 1
        text = "\n".join(item for item in buf if item)
        if not text:
            raise ParseError("EXAMPLES requires a CSV file or inline table", filename=filename, lineno=lineno)
        return _read_csv(text), last

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
        for field, label in (("setup", "SUITE-SETUP"), ("teardown", "SUITE-TEARDOWN")):
            name = suite.get(field)
            if name and name not in test_map:
                raise ParseError(f"Unknown {label} test '{name}'", filename=filename)
        helpers = _helper_names(suite)
        for test in suite["tests"]:
            for field in ("setup", "teardown"):
                name = test.get(field)
                if name and name not in test_map:
                    raise ParseError(
                        f"Unknown {field.upper()} test '{name}' referenced by '{test['name']}'",
                        filename=test.get("source") or filename,
                        lineno=test.get("lineno"),
                    )
            for name in test.get("depends") or []:
                if name not in test_map:
                    raise ParseError(
                        f"Unknown DEPENDS test '{name}' referenced by '{test['name']}'",
                        filename=test.get("source") or filename,
                        lineno=test.get("lineno"),
                    )
                if name == test["name"]:
                    raise ParseError(
                        f"TEST '{test['name']}' cannot DEPENDS on itself",
                        filename=test.get("source") or filename,
                        lineno=test.get("lineno"),
                    )
                if name in helpers or (test_map.get(name) or {}).get("kind") == "helper":
                    raise ParseError(
                        f"DEPENDS '{name}' is a HELPER, not a primary TEST",
                        filename=test.get("source") or filename,
                        lineno=test.get("lineno"),
                    )
        self._detect_cycles(test_map, filename)

    def _detect_cycles(self, test_map, filename):
        self._walk_cycles(test_map, filename, ("setup", "teardown"), "SETUP/TEARDOWN")
        self._walk_cycles(test_map, filename, ("depends",), "DEPENDS")

    def _walk_cycles(self, test_map, filename, fields, label):
        visiting = []
        seen = set()

        def neighbors(test):
            names = []
            for field in fields:
                value = test.get(field)
                if isinstance(value, list):
                    names.extend(value)
                elif value:
                    names.append(value)
            return names

        def visit(name):
            if name in visiting:
                cycle = visiting[visiting.index(name):] + [name]
                raise ParseError(
                    f"{label} cycle detected: " + " -> ".join(cycle),
                    filename=filename,
                )
            if name in seen or name not in test_map:
                return
            visiting.append(name)
            for nxt in neighbors(test_map[name]):
                visit(nxt)
            visiting.pop()
            seen.add(name)

        for name in test_map:
            visit(name)

    def _parse_depends(self, rest, filename, lineno):
        names = [item.strip() for item in rest.split(",") if item.strip()]
        if not names:
            raise ParseError("DEPENDS requires a test name", filename=filename, lineno=lineno)
        return names

    def _require_test(self, current_test, keyword, filename, lineno):
        if current_test is None:
            raise ParseError(f"{keyword} must appear inside a TEST or HELPER", filename=filename, lineno=lineno)

    def _require_primary(self, current_test, keyword, filename, lineno):
        self._require_test(current_test, keyword, filename, lineno)
        if current_test.get("kind") == "helper":
            raise ParseError(f"{keyword} cannot appear on a HELPER", filename=filename, lineno=lineno)

    def _require_step(self, current_step, keyword, filename, lineno):
        if current_step is None:
            raise ParseError(
                f"{keyword} must follow a REQUEST",
                filename=filename,
                lineno=lineno,
            )

    def _close_open_helper(self, current_test, current_step, tests):
        if current_test is not None and current_test.get("kind") == "helper":
            tests.append(current_test)
            return None, None
        return current_test, current_step

    def _require_no_test(self, current_test, keyword, filename, lineno):
        if current_test is not None:
            raise ParseError(
                f"{keyword} must appear at suite level (before TEST)",
                filename=filename,
                lineno=lineno,
            )


def _parse_openapi_spec(remainder):
    parts = remainder.split()
    strict = False
    if parts and parts[-1].lower() == "strict":
        strict = True
        parts = parts[:-1]
    return " ".join(parts), strict


def _strip_quotes(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_expect_value(raw):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return _strip_quotes(raw)


def _parse_duration_seconds(raw, filename, lineno):
    text = str(raw).strip().lower()
    try:
        if text.endswith("ms"):
            return float(text[:-2]) / 1000.0
        if text.endswith("s"):
            return float(text[:-1])
        return float(text)
    except ValueError as exc:
        raise ParseError(f"Invalid BACKOFF value {raw!r}", filename=filename, lineno=lineno) from exc


def _read_csv(text):
    handle = io.StringIO(text.strip())
    reader = csv.DictReader(handle)
    if not reader.fieldnames:
        raise ParseError("EXAMPLES CSV is missing a header row")
    rows = []
    for row in reader:
        rows.append({key.strip(): (value or "").strip() for key, value in row.items() if key})
    if not rows:
        raise ParseError("EXAMPLES CSV has no data rows")
    return rows


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
