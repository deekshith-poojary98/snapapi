from __future__ import annotations

import json
import re
from pathlib import Path

from snapapi.exceptions import SnapAPIError

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
PATH_PARAM_RE = re.compile(r"\{[^}]+\}")


def generate_smoke(spec_path, base_url=None, output=None):
    spec = load_spec(spec_path)
    servers = spec.get("servers") or []
    url = base_url or spec.get("host")
    if not url and servers:
        url = servers[0].get("url")
    if not url:
        url = "https://api.example.com"
    paths = spec.get("paths") or {}
    lines = [
        f"SUITE: OpenAPI smoke ({Path(spec_path).name})",
        "DESC: Generated smoke tests from OpenAPI",
        "STOP-ON-FAILURE: false",
        f"URL: {url}",
        "",
    ]
    count = 0
    for path, ops in paths.items():
        if not isinstance(ops, dict):
            continue
        path_params = ops.get("parameters") or []
        for method in HTTP_METHODS:
            op = ops.get(method) or ops.get(method.upper())
            if not isinstance(op, dict):
                continue
            name = (op.get("operationId") or op.get("summary") or f"{method.upper()} {path}").strip()
            name = name.replace(":", " ")
            resolved, query = _apply_parameters(path, op, path_params)
            lines.append(f"TEST: {name}")
            lines.append(f"  {method.upper()}: {resolved}")
            if query:
                pairs = "&".join(f"{key}={value}" for key, value in query.items())
                lines.append(f"  QUERY: {pairs}")
            if method in ("post", "put", "patch"):
                body = _request_body_example(op)
                lines.append(f"  BODY: {json.dumps(body)}")
            lines.append(f"  EXPECT: status == {_first_2xx(op)}")
            lines.append("")
            count += 1
    if count == 0:
        raise SnapAPIError(f"No HTTP operations found in {spec_path}")
    text = "\n".join(lines)
    if output:
        Path(output).write_text(text, encoding="utf-8")
    return text


def load_spec(path):
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            import yaml
        except ImportError as exc:
            raise SnapAPIError("PyYAML is required to read OpenAPI YAML specs") from exc
        data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise SnapAPIError(f"OpenAPI spec is not an object: {path}")
    return data


def match_operation(spec, method, url_path):
    paths = spec.get("paths") or {}
    method = (method or "").lower()
    exact = paths.get(url_path)
    if isinstance(exact, dict):
        op = exact.get(method) or exact.get(method.upper())
        if isinstance(op, dict):
            return op
    for template, ops in paths.items():
        if not isinstance(ops, dict):
            continue
        if not _path_matches(template, url_path):
            continue
        op = ops.get(method) or ops.get(method.upper())
        if isinstance(op, dict):
            return op
    return None


def response_schema(spec, method, url_path, status_code):
    op = match_operation(spec, method, url_path)
    if not op:
        return None
    responses = op.get("responses") or {}
    payload = responses.get(str(status_code)) or responses.get("default")
    if not isinstance(payload, dict):
        return None
    content = payload.get("content") or {}
    json_content = content.get("application/json") or {}
    schema = json_content.get("schema")
    if schema is None:
        return None
    return resolve_ref(spec, schema)


def resolve_ref(spec, node):
    if isinstance(node, dict) and "$ref" in node:
        ref = str(node["$ref"])
        if not ref.startswith("#/"):
            return node
        current = spec
        for part in ref[2:].split("/"):
            if not isinstance(current, dict) or part not in current:
                return node
            current = current[part]
        return resolve_ref(spec, current)
    return node


def _apply_parameters(path, op, path_item_params):
    params = list(path_item_params or []) + list(op.get("parameters") or [])
    resolved = path
    query = {}
    for param in params:
        if not isinstance(param, dict) or "$ref" in param:
            continue
        name = param.get("name")
        if not name:
            continue
        example = _param_example(param)
        location = param.get("in")
        if location == "path":
            resolved = resolved.replace("{" + str(name) + "}", str(example))
        elif location == "query" and param.get("required"):
            query[str(name)] = str(example)
    resolved = PATH_PARAM_RE.sub("1", resolved)
    return resolved, query


def _param_example(param):
    if param.get("example") is not None:
        return param["example"]
    schema = param.get("schema") or {}
    if schema.get("example") is not None:
        return schema["example"]
    if schema.get("default") is not None:
        return schema["default"]
    return 1


def _request_body_example(op):
    body = op.get("requestBody") or {}
    content = body.get("content") or {}
    json_content = content.get("application/json") or next(iter(content.values()), {}) or {}
    if not isinstance(json_content, dict):
        return {}
    if "example" in json_content:
        return json_content["example"] or {}
    examples = json_content.get("examples") or {}
    if examples:
        first = next(iter(examples.values()))
        if isinstance(first, dict) and "value" in first:
            return first["value"] if first["value"] is not None else {}
        return first if isinstance(first, (dict, list)) else {}
    schema = json_content.get("schema") or {}
    if isinstance(schema, dict) and schema.get("example") is not None:
        return schema["example"]
    return {}


def _first_2xx(op):
    responses = op.get("responses") or {}
    for key in responses:
        try:
            code = int(key)
        except (TypeError, ValueError):
            continue
        if 200 <= code < 300:
            return code
    return 200


def _path_matches(template, actual):
    t_parts = [part for part in str(template).strip("/").split("/") if part or template == "/"]
    a_parts = [part for part in str(actual).strip("/").split("/") if part or actual == "/"]
    if str(template) == "/" or str(actual) == "/":
        t_parts = str(template).strip("/").split("/")
        a_parts = str(actual).strip("/").split("/")
    if len(t_parts) != len(a_parts):
        return False
    for template_part, actual_part in zip(t_parts, a_parts):
        if template_part.startswith("{") and template_part.endswith("}"):
            continue
        if template_part != actual_part:
            return False
    return True
