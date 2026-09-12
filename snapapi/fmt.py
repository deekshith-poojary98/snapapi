from __future__ import annotations

from pathlib import Path

from snapapi.parser import LINE_KEYWORD_RE, HTTP_METHODS

SUITE_KEYWORDS = {
    "SUITE",
    "DESC",
    "URL",
    "OPTIONS",
    "TIMEOUT",
    "STOP-ON-FAILURE",
    "IMPORT",
    "HEADERS",
    "HEADER",
    "AUTH",
    "FOLLOW-REDIRECTS",
    "SUITE-SETUP",
    "SUITE-TEARDOWN",
    "SET",
}
TEST_STARTERS = {"TEST"}
CHILD_KEYWORDS = {
    "DESC",
    "TAG",
    "SETUP",
    "TEARDOWN",
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
    "FILE",
    "GRAPHQL",
    "EXAMPLES",
    "SKIP",
    "ONLY",
    "QUARANTINE",
    "FOLLOW-REDIRECTS",
    "WAIT",
    "SET",
} | set(HTTP_METHODS)


def format_text(text):
    lines = text.splitlines()
    out = []
    in_test = False
    in_json = 0
    prev_blank = False
    for raw in lines:
        stripped = raw.rstrip()
        if not stripped:
            if not prev_blank:
                out.append("")
            prev_blank = True
            continue
        prev_blank = False
        if stripped.startswith("//"):
            out.append(("  " if in_test else "") + stripped.lstrip())
            continue
        if in_json:
            out.append(("  " if in_test else "") + stripped.lstrip())
            in_json += stripped.count("{") + stripped.count("[")
            in_json -= stripped.count("}") + stripped.count("]")
            if in_json < 0:
                in_json = 0
            continue
        match = LINE_KEYWORD_RE.match(stripped.lstrip())
        if match:
            keyword = match.group(1)
            rest = match.group(2).strip()
            if keyword == "TEST":
                in_test = True
                out.append(f"TEST: {rest}")
            elif keyword in SUITE_KEYWORDS and not in_test:
                out.append(f"{keyword}: {rest}".rstrip())
            else:
                out.append(f"  {keyword}: {rest}".rstrip() if in_test or keyword in CHILD_KEYWORDS else f"{keyword}: {rest}".rstrip())
            if rest.startswith("{") or rest.startswith("["):
                in_json = rest.count("{") + rest.count("[") - rest.count("}") - rest.count("]")
            continue
        if stripped.upper().startswith("SUITE SETUP:"):
            in_test = False
            out.append("SUITE SETUP: " + stripped.split(":", 1)[1].strip())
            continue
        if stripped.upper().startswith("SUITE TEARDOWN:"):
            in_test = False
            out.append("SUITE TEARDOWN: " + stripped.split(":", 1)[1].strip())
            continue
        out.append(("  " if in_test else "") + stripped.lstrip())
    formatted = "\n".join(out).rstrip() + "\n"
    return formatted


def format_file(path, check=False):
    target = Path(path)
    original = target.read_text(encoding="utf-8")
    formatted = format_text(original)
    if original == formatted:
        return False
    if not check:
        target.write_text(formatted, encoding="utf-8")
    return True
