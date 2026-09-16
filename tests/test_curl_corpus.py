"""Golden-file corpus for curl → SnapAPI conversion quality.

Each case is three files under tests/fixtures/curl/:
  name.curl  — input
  name.sapi  — expected .sapi output
  name.json  — expectations (warnings, must_contain, semantic, …)

Assertion levels:
  1. Syntax   — generated DSL parses (unless skip_parse)
  2. Structural — golden .sapi match + optional must_contain
  3. Semantic — parsed request means the same as the curl (method/auth/body/…)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from snapapi.convert import curl_to_sapi
from tests.helpers import parse_dsl

CORPUS = Path(__file__).parent / "fixtures" / "curl"


def _cases():
    return sorted(CORPUS.glob("*.curl"))


@pytest.mark.parametrize("curl_path", _cases(), ids=lambda p: p.stem)
def test_curl_corpus_case(curl_path: Path):
    meta_path = curl_path.with_suffix(".json")
    sapi_path = curl_path.with_suffix(".sapi")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    kwargs = {}
    if meta.get("suite_name"):
        kwargs["suite_name"] = meta["suite_name"]

    result = curl_to_sapi(curl_path.read_text(encoding="utf-8"), **kwargs)
    features = [w.feature for w in result.warnings]

    if meta.get("warnings") == []:
        assert features == [], f"unexpected warnings: {features}"

    for needle in meta.get("warnings_contain") or []:
        assert any(needle in feature for feature in features), f"missing warning {needle!r} in {features}"

    if meta.get("require_warnings"):
        assert features, "expected conversion warnings"

    for fragment in meta.get("must_contain") or []:
        assert fragment in result.text, f"missing {fragment!r} in output"

    for fragment in meta.get("must_not_contain") or []:
        assert fragment not in result.text, f"should not contain {fragment!r}"

    # Intent guardrail: AUTH must not be mechanically dumped as HEADER Authorization.
    if "AUTH: bearer" in result.text or "AUTH: basic" in result.text:
        assert "HEADER Authorization:" not in result.text
        assert "HEADER Authorization :" not in result.text

    assert sapi_path.is_file(), f"missing golden file {sapi_path.name}"
    assert result.text == sapi_path.read_text(encoding="utf-8")

    suite = None
    if not meta.get("skip_parse"):
        suite = parse_dsl(result.text)
        assert suite["tests"], "expected at least one TEST"

    semantic = meta.get("semantic")
    if semantic:
        assert suite is not None, "semantic checks require a parseable suite"
        _assert_semantic(suite, result.text, semantic)


def _assert_semantic(suite, text, semantic):
    test = suite["tests"][0]
    step = test["steps"][0]

    if "base_url" in semantic:
        assert suite["base_url"] == semantic["base_url"]

    if "method" in semantic:
        assert step["action"] == semantic["method"]

    if "endpoint" in semantic:
        assert step["endpoint"] == semantic["endpoint"]

    if "query" in semantic:
        for key, value in semantic["query"].items():
            assert step["query"].get(key) == value, f"query {key}: {step['query'].get(key)!r} != {value!r}"

    if "headers" in semantic:
        for key, value in semantic["headers"].items():
            assert step["headers"].get(key) == value, f"header {key}: {step['headers'].get(key)!r} != {value!r}"

    for key in semantic.get("headers_absent") or []:
        # Authorization may exist after AUTH parse; "absent" means not as a literal HEADER line.
        if key.lower() == "authorization":
            assert "HEADER Authorization:" not in text
        else:
            assert key not in step["headers"], f"header {key} should be absent"

    if "auth" in semantic:
        scheme, _, value = semantic["auth"].partition(" ")
        assert f"AUTH: {scheme} {value}" in text
        if scheme == "bearer":
            assert step["headers"].get("Authorization") == f"Bearer {value}"
        elif scheme == "basic":
            # Parser base64-encodes user:pass into Authorization.
            assert step["headers"].get("Authorization", "").startswith("Basic ")

    if "body" in semantic:
        assert step["data"] == semantic["body"]

    if "raw_body" in semantic:
        assert step.get("raw_body") == semantic["raw_body"]

    if "body_type" in semantic:
        assert step.get("body_type") == semantic["body_type"]

    if "follow_redirects" in semantic:
        assert step.get("follow_redirects") is semantic["follow_redirects"]

    if semantic.get("prefer_auth"):
        assert "AUTH:" in text
        assert "HEADER Authorization:" not in text
