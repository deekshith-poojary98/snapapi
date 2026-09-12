from __future__ import annotations

import json
from pathlib import Path

from snapapi.exceptions import SnapAPIError


def generate_smoke(spec_path, base_url=None, output=None):
    spec = _load_spec(spec_path)
    servers = spec.get("servers") or []
    url = base_url or spec.get("host")
    if not url and servers:
        url = servers[0].get("url")
    if not url:
        url = "https://api.example.com"
    paths = spec.get("paths") or {}
    lines = [
        f"SUITE: OpenAPI smoke ({Path(spec_path).name})",
        "DESC: Generated GET smoke tests from OpenAPI",
        "STOP-ON-FAILURE: false",
        f"URL: {url}",
        "",
    ]
    count = 0
    for path, ops in paths.items():
        if not isinstance(ops, dict):
            continue
        get = ops.get("get") or ops.get("GET")
        if not get:
            continue
        name = (get.get("operationId") or get.get("summary") or f"GET {path}").strip()
        name = name.replace(":", " ")
        lines.extend(
            [
                f"TEST: {name}",
                f"  GET: {path}",
                "  EXPECT: status == 200",
                "",
            ]
        )
        count += 1
    if count == 0:
        raise SnapAPIError(f"No GET operations found in {spec_path}")
    text = "\n".join(lines)
    if output:
        Path(output).write_text(text, encoding="utf-8")
    return text


def _load_spec(path):
    raw = Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    try:
        import yaml
    except ImportError as exc:
        raise SnapAPIError("PyYAML is required to read OpenAPI YAML specs") from exc
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise SnapAPIError(f"OpenAPI spec is not an object: {path}")
    return data
