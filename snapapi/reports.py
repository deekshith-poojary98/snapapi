from __future__ import annotations

import json
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
            skipped=str(suite.get("skipped", 0)),
            time=_seconds(suite.get("duration_ms", 0)),
        )
        if suite.get("source"):
            suite_el.set("file", str(suite["source"]))
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
                failure = ET.SubElement(case, "failure", message=_xml_text(test.get("error") or "failed"))
                failure.text = _xml_text(test.get("error") or "failed")
            elif status == "skipped":
                ET.SubElement(case, "skipped")

    tree = ET.ElementTree(suites_el)
    ET.indent(tree, space="  ")
    tree.write(target, encoding="utf-8", xml_declaration=True)


def write_html_report(payload, path):
    target = Path(path)
    if target.parent and str(target.parent) not in ("", "."):
        target.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for suite in payload.get("suites", []):
        for test in suite.get("tests", []):
            status = test.get("status") or "passed"
            color = {"passed": "#15803d", "failed": "#b91c1c", "skipped": "#a16207"}.get(status, "#334155")
            error = _xml_text(test.get("error") or "")
            rows.append(
                f"<tr><td>{_xml_text(suite.get('name'))}</td><td>{_xml_text(test.get('name'))}</td>"
                f"<td style='color:{color}'>{status}</td><td>{_seconds(test.get('duration_ms', 0))}s</td>"
                f"<td>{error}</td></tr>"
            )
    html = (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>SnapAPI report</title>"
        "<style>body{font-family:ui-sans-serif,system-ui,sans-serif;margin:2rem;color:#0f172a}"
        "table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #e2e8f0;"
        "text-align:left;padding:.5rem .75rem}.summary{margin-bottom:1.5rem}</style></head><body>"
        f"<h1>SnapAPI report</h1><p class=\"summary\">{payload.get('passed', 0)} passed, "
        f"{payload.get('failed', 0)} failed, {payload.get('skipped', 0)} skipped</p>"
        "<table><thead><tr><th>Suite</th><th>Test</th><th>Status</th><th>Time</th><th>Error</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></body></html>\n"
    )
    target.write_text(html, encoding="utf-8")


def build_report_payload(suite_results):
    suites = [result.to_dict() for result in suite_results]
    total = sum(item["total"] for item in suites)
    passed = sum(item["passed"] for item in suites)
    failed = sum(item["failed"] for item in suites)
    skipped = sum(item["skipped"] for item in suites)
    duration_ms = sum(item["duration_ms"] for item in suites)
    return {
        "ok": failed == 0,
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
    return str(value) if value is not None else ""
