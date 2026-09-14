from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def write_json_report(payload, path):
    target = Path(path)
    if target.parent and str(target.parent) not in ("", "."):
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, default=_json_default) + "\n", encoding="utf-8")


def write_junit_report(payload, path):
    target = Path(path)
    if target.parent and str(target.parent) not in ("", "."):
        target.parent.mkdir(parents=True, exist_ok=True)

    suites_el = ET.Element(
        "testsuites",
        name="snapapi",
        tests=str(payload.get("total", 0)),
        failures=str(payload.get("failed", 0)),
        errors=str(sum(1 for suite in payload.get("suites", []) if suite.get("error"))),
        skipped=str(payload.get("skipped", 0)),
        time=_seconds(payload.get("duration_ms", 0)),
    )
    for suite in payload.get("suites", []):
        suite_el = ET.SubElement(
            suites_el,
            "testsuite",
            name=suite.get("name") or "suite",
            tests=str(suite.get("total", 0)),
            failures=str(suite.get("failed", 0)),
            errors="1" if suite.get("error") else "0",
            skipped=str(suite.get("skipped", 0)),
            time=_seconds(suite.get("duration_ms", 0)),
        )
        if suite.get("source"):
            suite_el.set("file", str(suite["source"]))
        if suite.get("error"):
            error_el = ET.SubElement(
                suite_el,
                "error",
                message=_xml_text(suite.get("error_name") or "SUITE-SETUP"),
            )
            error_el.text = _xml_text(suite["error"])
        for test in suite.get("tests", []):
            case = ET.SubElement(
                suite_el,
                "testcase",
                name=test.get("name") or "test",
                classname="snapapi",
                time=_seconds(test.get("duration_ms", 0)),
            )
            status = test.get("status")
            if status == "failed":
                error = test.get("error") or "failed"
                failure = ET.SubElement(case, "failure", message=_xml_text(error.splitlines()[0]))
                failure.text = _xml_text(error)
            elif status == "skipped":
                ET.SubElement(case, "skipped")

    tree = ET.ElementTree(suites_el)
    ET.indent(tree, space="  ")
    tree.write(target, encoding="utf-8", xml_declaration=True)


def write_html_report(payload, path):
    target = Path(path)
    if target.parent and str(target.parent) not in ("", "."):
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_render_html_report(payload), encoding="utf-8")


def _render_html_report(payload):
    passed = int(payload.get("passed") or 0)
    failed = int(payload.get("failed") or 0)
    skipped = int(payload.get("skipped") or 0)
    total = int(payload.get("total") or (passed + failed + skipped))
    outcome = "failed" if not payload.get("ok", failed == 0) else "passed"
    generated = time.strftime("%Y-%m-%d %H:%M:%S")
    suites = "".join(_html_suite(suite) for suite in payload.get("suites") or [])
    if not suites:
        suites = '<p class="empty">No tests ran.</p>'
    else:
        suites += '<p class="empty" id="filter-empty" hidden>No tests match this filter.</p>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SnapAPI report — {passed} passed, {failed} failed</title>
  <style>{_HTML_REPORT_CSS}</style>
</head>
<body>
  <header class="top">
    <div class="wrap">
    <div class="brand">
      <span class="logo" aria-hidden="true"><svg viewBox="0 0 24 24"><path fill="currentColor" d="M13 2 4 14h7l-1 8 9-12h-7l1-8z"/></svg></span>
      <div>
        <p class="kicker">SnapAPI report</p>
        <h1>{_xml_text(f"{passed} passed, {failed} failed, {skipped} skipped")}</h1>
      </div>
    </div>
    <div class="stats">
      <span class="stat ok"><strong>{passed}</strong> passed</span>
      <span class="stat {'bad' if failed else ''}"><strong>{failed}</strong> failed</span>
      <span class="stat"><strong>{skipped}</strong> skipped</span>
      <span class="stat"><strong>{total}</strong> tests</span>
      <span class="stat"><strong>{_seconds(payload.get('duration_ms', 0))}s</strong> total</span>
    </div>
    <div class="toolbar">
      <p class="meta">Generated {generated}</p>
      <div class="filters" role="group" aria-label="Filter tests">
        <button type="button" class="filter active" data-filter="all">All</button>
        <button type="button" class="filter" data-filter="failed">Failed</button>
        <button type="button" class="filter" data-filter="passed">Passed</button>
        <button type="button" class="filter" data-filter="skipped">Skipped</button>
      </div>
    </div>
    </div>
  </header>
  <main data-outcome="{outcome}">
    {suites}
  </main>
  <script>
    (function () {{
      var buttons = document.querySelectorAll("[data-filter]");
      var tests = document.querySelectorAll(".test");
      var suites = document.querySelectorAll(".suite");
      var empty = document.getElementById("filter-empty");
      buttons.forEach(function (btn) {{
        btn.addEventListener("click", function () {{
          var value = btn.getAttribute("data-filter");
          buttons.forEach(function (item) {{ item.classList.toggle("active", item === btn); }});
          var visible = 0;
          tests.forEach(function (el) {{
            var show = value === "all" || el.getAttribute("data-status") === value;
            el.hidden = !show;
            if (show) visible += 1;
          }});
          suites.forEach(function (suite) {{
            suite.hidden = !suite.querySelector(".test:not([hidden])");
          }});
          if (empty) empty.hidden = visible > 0;
        }});
      }});
    }})();
  </script>
</body>
</html>
"""


_HTML_REPORT_CSS = """
:root {
  --bg: #f4f1ea;
  --card: #fffcf7;
  --text: #1b1f29;
  --muted: #5c6473;
  --border: #e4ddd0;
  --accent: #0f766e;
  --ok: #067647;
  --ok-bg: #ecfdf3;
  --bad: #b42318;
  --bad-bg: #fef3f2;
  --warn: #b54708;
  --warn-bg: #fffaeb;
  --code: #1b1f29;
  --code-text: #e7ecf3;
  --sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: var(--sans);
  color: var(--text);
  background: var(--bg);
  line-height: 1.5;
  min-height: 100vh;
}
.top {
  background: #12151c;
  color: #e7ecf3;
  padding: 1.6rem 1.4rem 1.3rem;
}
.wrap, main { max-width: 980px; margin-left: auto; margin-right: auto; }
.brand { display: flex; align-items: flex-start; gap: 0.75rem; }
.logo { color: #5eead4; display: inline-flex; margin-top: 0.15rem; }
.logo svg { width: 24px; height: 24px; display: block; }
.kicker {
  margin: 0 0 0.2rem;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: #5eead4;
}
h1 { margin: 0; font-size: 1.45rem; letter-spacing: -0.02em; font-weight: 700; word-spacing: 0.08em; }
.stats {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin: 1.1rem 0 0.9rem;
}
.stat {
  background: rgba(255,255,255,0.06);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 999px;
  padding: 0.28rem 0.7rem;
  font-size: 0.82rem;
  color: #c8cdd6;
}
.stat strong { color: #fff; font-weight: 700; margin-right: 0.2rem; }
.stat.ok strong { color: #6ee7b7; }
.stat.bad { background: rgba(180, 35, 24, 0.22); }
.stat.bad strong { color: #fda29b; }
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.7rem;
}
.meta { margin: 0; color: #8b93a1; font-size: 0.78rem; }
.filters { display: flex; gap: 0.35rem; }
.filter {
  font: inherit;
  font-size: 0.8rem;
  font-weight: 600;
  border: 0;
  border-radius: 8px;
  padding: 0.35rem 0.7rem;
  background: rgba(255,255,255,0.08);
  color: #c8cdd6;
  cursor: pointer;
}
.filter:hover { background: rgba(255,255,255,0.14); }
.filter.active { background: #0f766e; color: #ecfdf5; }
main { padding: 1.3rem 1.4rem 3rem; max-width: 980px; margin: 0 auto; }
.suite { margin: 0 0 1.4rem; }
.suite-head { margin: 0 0 0.7rem; }
.suite-head h2 { margin: 0 0 0.2rem; font-size: 1.05rem; letter-spacing: -0.02em; }
.suite-meta { margin: 0; color: var(--muted); font-size: 0.82rem; }
.test {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 0.85rem 1rem;
  margin: 0 0 0.65rem;
  box-shadow: 0 10px 28px rgba(18, 21, 28, 0.05);
}
.test-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem 0.7rem;
}
.test-head h3 { margin: 0; font-size: 1rem; flex: 1 1 12rem; }
.time { color: var(--muted); font-variant-numeric: tabular-nums; font-size: 0.82rem; }
.pill {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 0.12rem 0.55rem;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.pill.passed { background: var(--ok-bg); color: var(--ok); }
.pill.failed { background: var(--bad-bg); color: var(--bad); }
.pill.skipped { background: var(--warn-bg); color: var(--warn); }
.tags { display: flex; flex-wrap: wrap; gap: 0.3rem; }
.tag {
  font-size: 0.72rem;
  color: var(--accent);
  background: rgba(13, 148, 136, 0.1);
  border-radius: 6px;
  padding: 0.08rem 0.4rem;
}
.error {
  margin: 0.65rem 0 0;
  padding: 0.55rem 0.7rem;
  background: var(--bad-bg);
  color: #7a271a;
  border-radius: 8px;
  font-size: 0.88rem;
  white-space: pre-wrap;
}
.req {
  margin: 0.7rem 0 0;
  padding: 0.65rem 0.75rem;
  background: #f7f3ea;
  border-radius: 10px;
}
.req-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.45rem 0.6rem;
  font-size: 0.88rem;
}
.req-end { display: flex; align-items: center; gap: 0.45rem; margin-left: auto; }
.method {
  font-family: var(--mono);
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  border-radius: 6px;
  padding: 0.12rem 0.4rem;
  color: #0f766e;
  background: rgba(13, 148, 136, 0.14);
}
.method.post, .method.put { color: #1d4ed8; background: #eff6ff; }
.method.patch { color: #6d28d9; background: #f5f3ff; }
.method.delete { color: #b42318; background: #fef3f2; }
.url { font-family: var(--mono); font-size: 0.8rem; word-break: break-all; color: #334155; flex: 1 1 12rem; min-width: 0; }
.code {
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  min-width: 2.6rem;
  text-align: center;
  border-radius: 6px;
  padding: 0.08rem 0.4rem;
}
.code.ok { color: var(--ok); background: var(--ok-bg); }
.code.bad { color: var(--bad); background: var(--bad-bg); }
.code.warn { color: var(--warn); background: var(--warn-bg); }
.req-time { color: var(--muted); font-size: 0.78rem; }
details { margin: 0.45rem 0 0; }
summary {
  cursor: pointer;
  color: var(--muted);
  font-size: 0.78rem;
  font-weight: 600;
  user-select: none;
}
summary:hover { color: var(--text); }
pre {
  margin: 0.4rem 0 0;
  padding: 0.7rem 0.8rem;
  background: var(--code);
  color: var(--code-text);
  border-radius: 8px;
  overflow: auto;
  font-family: var(--mono);
  font-size: 0.78rem;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 22rem;
}
.tok-label { color: #8b93a1; }
.tok-key { color: #5eead4; }
.tok-str { color: #f3d19e; }
.tok-num { color: #fdba74; }
.tok-kw { color: #99f6e4; }
.tok-p { color: #64748b; }
.empty { color: var(--muted); }
#filter-empty { display: none; }
#filter-empty:not([hidden]) { display: block; }
@media (max-width: 640px) {
  .top, main { padding-left: 1rem; padding-right: 1rem; }
  h1 { font-size: 1.2rem; }
}
"""


def _html_suite(suite):
    tests = suite.get("tests") or []
    if not tests:
        return ""
    source = suite.get("source") or ""
    source_name = Path(source).name if source else ""
    source_html = f'<span title="{_xml_text(source)}">{_xml_text(source_name)}</span> · ' if source_name else ""
    cards = "".join(_html_test(test) for test in tests)
    return (
        f'<section class="suite">'
        f'<div class="suite-head"><h2>{_xml_text(suite.get("name") or "Suite")}</h2>'
        f'<p class="suite-meta">{source_html}{suite.get("total", len(tests))} tests · '
        f'{_seconds(suite.get("duration_ms", 0))}s</p></div>'
        f"{cards}</section>"
    )


def _html_test(test):
    status = test.get("status") or "passed"
    tags = "".join(f'<span class="tag">{_xml_text(tag)}</span>' for tag in test.get("tags") or [])
    tag_wrap = f'<div class="tags">{tags}</div>' if tags else ""
    error = test.get("error")
    error_html = f'<pre class="error">{_xml_text(error)}</pre>' if error else ""
    requests = "".join(_html_request(req, failed=status == "failed") for req in test.get("requests") or [])
    return (
        f'<article class="test" data-status="{_xml_text(status)}">'
        f'<div class="test-head"><span class="pill {_xml_text(status)}">{_xml_text(status)}</span>'
        f'<h3>{_xml_text(test.get("name") or "test")}</h3>'
        f'{tag_wrap}<span class="time">{_seconds(test.get("duration_ms", 0))}s</span></div>'
        f"{error_html}{requests}</article>"
    )


def _html_request(req, failed=False):
    method = str(req.get("method") or "?").upper()
    url = req.get("url") or ""
    code = req.get("status_code")
    code_class = _status_class(code)
    code_html = f'<span class="code {code_class}">{_xml_text(code)}</span>' if code is not None else ""
    duration = req.get("duration_ms")
    time_html = f'<span class="req-time">{_seconds(duration)}s</span>' if duration else ""
    end_html = f'<span class="req-end">{code_html}{time_html}</span>' if code_html or time_html else ""
    open_attr = " open" if failed else ""
    bodies = []
    if req.get("request_body") is not None:
        bodies.append(_html_body("request", req.get("request_body"), open_attr=open_attr))
    if req.get("response_body"):
        bodies.append(_html_body("response", req.get("response_body"), open_attr=open_attr))
    if req.get("error"):
        bodies.append(f'<pre class="error">{_xml_text(req.get("error"))}</pre>')
    return (
        f'<div class="req"><div class="req-line">'
        f'<span class="method {_xml_text(method.lower())}">{_xml_text(method)}</span>'
        f'<span class="url">{_xml_text(url)}</span>{end_html}</div>'
        f'{"".join(bodies)}</div>'
    )


def _html_body(label, value, open_attr=""):
    pretty = _pretty_body(value)
    body = _highlight_json(pretty) if _looks_like_json(pretty) else _xml_text(pretty)
    return (
        f"<details{open_attr}><summary>{_xml_text(label)}</summary>"
        f'<pre><span class="tok-label">{_xml_text(label)}:</span> {body}</pre></details>'
    )


def _pretty_body(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, default=_json_default, ensure_ascii=False)
    text = "" if value is None else str(value)
    stripped = text.strip()
    if stripped[:1] in "{[":
        try:
            return json.dumps(json.loads(stripped), indent=2, default=_json_default, ensure_ascii=False)
        except (TypeError, ValueError):
            return text
    return text


def _looks_like_json(text):
    stripped = (text or "").strip()
    return stripped[:1] in "{["


_JSON_TOKEN_RE = re.compile(
    r'("(?:\\.|[^"\\])*")(\s*:)?'
    r"|(-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"|(true|false|null)"
    r"|([\[\]{},:])"
    r"|(\s+)"
    r"|([^\s\[\]{},:\"]+)"
)


def _highlight_json(text):
    parts = []
    for match in _JSON_TOKEN_RE.finditer(text):
        string, colon, number, literal, punct, space, other = match.groups()
        if string is not None:
            klass = "tok-key" if colon else "tok-str"
            parts.append(f'<span class="{klass}">{_xml_text(string)}</span>')
            if colon:
                parts.append(f'<span class="tok-p">{_xml_text(colon)}</span>')
        elif number is not None:
            parts.append(f'<span class="tok-num">{_xml_text(number)}</span>')
        elif literal is not None:
            parts.append(f'<span class="tok-kw">{_xml_text(literal)}</span>')
        elif punct is not None:
            parts.append(f'<span class="tok-p">{_xml_text(punct)}</span>')
        elif space is not None:
            parts.append(space)
        else:
            parts.append(_xml_text(other or match.group(0)))
    return "".join(parts)


def _status_class(code):
    try:
        number = int(code)
    except (TypeError, ValueError):
        return ""
    if 200 <= number < 300:
        return "ok"
    if 300 <= number < 400:
        return "warn"
    if number >= 400:
        return "bad"
    return ""


def build_report_payload(suite_results):
    suites = [result.to_dict() for result in suite_results]
    total = sum(item["total"] for item in suites)
    passed = sum(item["passed"] for item in suites)
    failed = sum(item["failed"] for item in suites)
    skipped = sum(item["skipped"] for item in suites)
    duration_ms = sum(item["duration_ms"] for item in suites)
    return {
        "ok": all(item.get("ok", item["failed"] == 0) for item in suites),
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "duration_ms": round(duration_ms, 3),
        "suites": suites,
    }


def _seconds(duration_ms):
    try:
        return f"{float(duration_ms) / 1000.0:.3f}"
    except (TypeError, ValueError):
        return "0.000"


def _json_default(value):
    return str(value)


def _xml_text(value):
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
