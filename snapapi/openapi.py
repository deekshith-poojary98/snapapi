from __future__ import annotations

import json
import re
from pathlib import Path

from snapapi.exceptions import SnapAPIError

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
PATH_PARAM_RE = re.compile(r"\{[^}]+\}")
_SYNTH_MAX_DEPTH = 6


class _Unsupported(Exception):
    """Schema construct the smoke synthesizer refuses to invent values for."""


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
            lines.append("  TAG: smoke")
            body_plan = _smoke_body_plan(op, spec) if method in ("post", "put", "patch") else None
            if body_plan and body_plan.get("skip"):
                lines.append(f"  SKIP: {body_plan['skip']}")
            lines.append(f"  {method.upper()}: {resolved}")
            if query:
                pairs = "&".join(f"{key}={value}" for key, value in query.items())
                lines.append(f"  QUERY: {pairs}")
            if body_plan is not None and "body" in body_plan:
                lines.append("  HEADER Content-Type: application/json")
                lines.append(f"  BODY: {json.dumps(body_plan['body'], default=str)}")
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
    _template, _item, op = match_path_item(spec, method, url_path)
    return op


def match_path_item(spec, method, url_path):
    paths = spec.get("paths") or {}
    method = (method or "").lower()
    exact = paths.get(url_path)
    if isinstance(exact, dict):
        op = exact.get(method) or exact.get(method.upper())
        if isinstance(op, dict):
            return url_path, exact, op
    for template, ops in paths.items():
        if not isinstance(ops, dict):
            continue
        if not _path_matches(template, url_path):
            continue
        op = ops.get(method) or ops.get(method.upper())
        if isinstance(op, dict):
            return template, ops, op
    return None, None, None


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


def request_body_schema(spec, method, url_path):
    op = match_operation(spec, method, url_path)
    if not op:
        return None, False
    body = op.get("requestBody") or {}
    if not isinstance(body, dict):
        return None, False
    body = resolve_ref(spec, body)
    required = bool(body.get("required"))
    content = body.get("content") or {}
    json_content = content.get("application/json") or next(iter(content.values()), {}) or {}
    if not isinstance(json_content, dict):
        return None, required
    schema = json_content.get("schema")
    if schema is None:
        return None, required
    return resolve_ref(spec, schema), required


def collect_parameters(spec, method, url_path):
    _template, path_item, op = match_path_item(spec, method, url_path)
    if op is None:
        return []
    params = []
    for source in (path_item, op):
        for param in (source or {}).get("parameters") or []:
            if not isinstance(param, dict):
                continue
            params.append(resolve_ref(spec, param))
    return params


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


def _smoke_body_plan(op, spec):
    """Decide BODY for a smoke test.

    * Prefer OpenAPI examples.
    * Else synthesize a minimal value from a simple JSON Schema subset.
    * If ``requestBody.required`` and synthesis fails → SKIP (never silent ``{}``).
    * If body is absent/optional and nothing better exists → ``{}`` (reachability).
    """
    raw_body = op.get("requestBody")
    if not isinstance(raw_body, dict):
        return {"body": {}}
    body = resolve_ref(spec, raw_body)
    required = bool(body.get("required"))
    content = body.get("content") or {}
    if not content:
        if required:
            return {"skip": "required request body has no content schema to synthesize"}
        return {"body": {}}

    json_content = content.get("application/json")
    if json_content is None:
        # Prefer JSON when present; otherwise take the first content type.
        json_content = next(iter(content.values()), {}) or {}
    if not isinstance(json_content, dict):
        if required:
            return {"skip": "required request body content is not an object schema"}
        return {"body": {}}

    example = _content_example(json_content)
    if example is not None:
        return {"body": example}

    schema = json_content.get("schema")
    if schema is None:
        if required:
            return {"skip": "required request body could not be synthesized"}
        return {"body": {}}

    try:
        synthesized = _synthesize_value(resolve_ref(spec, schema), spec, depth=0)
    except _Unsupported as exc:
        if required:
            return {"skip": f"required request body could not be synthesized ({exc})"}
        return {"body": {}}
    return {"body": synthesized}


def _content_example(json_content):
    if "example" in json_content:
        return json_content["example"] if json_content["example"] is not None else {}
    examples = json_content.get("examples") or {}
    if examples:
        first = next(iter(examples.values()))
        if isinstance(first, dict) and "value" in first:
            return first["value"] if first["value"] is not None else {}
        if isinstance(first, (dict, list, str, int, float, bool)) or first is None:
            return first if first is not None else {}
    schema = json_content.get("schema") or {}
    if isinstance(schema, dict) and schema.get("example") is not None:
        return schema["example"]
    return None


def _synthesize_value(schema, spec, depth):
    if not isinstance(schema, dict):
        raise _Unsupported("non-object schema node")
    schema = resolve_ref(spec, schema)
    if depth > _SYNTH_MAX_DEPTH:
        raise _Unsupported("schema too deeply nested")
    if any(key in schema for key in ("oneOf", "anyOf", "allOf", "not")):
        raise _Unsupported("composite schema")
    if schema.get("example") is not None:
        return schema["example"]
    if schema.get("default") is not None:
        return schema["default"]
    if schema.get("enum"):
        return schema["enum"][0]

    type_name = _schema_type(schema)
    if type_name == "object" or "properties" in schema or "required" in schema:
        props = schema.get("properties") or {}
        required = [name for name in (schema.get("required") or []) if isinstance(name, str)]
        result = {}
        for name in required:
            prop = props.get(name)
            if not isinstance(prop, dict):
                raise _Unsupported(f"required property {name!r} has no schema")
            result[name] = _synthesize_value(resolve_ref(spec, prop), spec, depth + 1)
        return result
    if type_name == "array":
        items = schema.get("items")
        if items is None:
            return []
        return [_synthesize_value(resolve_ref(spec, items), spec, depth + 1)]
    if type_name == "string":
        return _string_example(schema)
    if type_name == "integer":
        return 0
    if type_name == "number":
        return 0.0
    if type_name == "boolean":
        return True
    if type_name == "null":
        return None
    if schema.get("nullable") is True and type_name is None:
        return None
    raise _Unsupported("untyped or unsupported schema")


def _schema_type(schema):
    type_name = schema.get("type")
    if isinstance(type_name, list):
        non_null = [item for item in type_name if item != "null"]
        return non_null[0] if non_null else "null"
    if type_name:
        return type_name
    if "properties" in schema or "required" in schema:
        return "object"
    if "items" in schema:
        return "array"
    return None


def _string_example(schema):
    fmt = schema.get("format")
    if fmt == "email":
        return "user@example.com"
    if fmt == "uuid":
        return "00000000-0000-4000-8000-000000000000"
    if fmt == "date":
        return "2020-01-01"
    if fmt in ("date-time", "datetime"):
        return "2020-01-01T00:00:00Z"
    if fmt == "uri":
        return "https://example.com"
    return "string"


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
