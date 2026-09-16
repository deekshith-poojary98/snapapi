"""I7 — OpenAPI smoke must not silently emit {} for required bodies."""

import json

from snapapi.openapi import generate_smoke
from tests.helpers import parse_dsl


def _write_spec(tmp_path, name, spec):
    path = tmp_path / name
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def _parsed_tests(text):
    suite = parse_dsl(text)
    return {test["name"]: test for test in suite["tests"]}


def test_required_body_synthesizes_required_properties(tmp_path):
    spec = _write_spec(
        tmp_path,
        "users.json",
        {
            "servers": [{"url": "https://api.example.com"}],
            "paths": {
                "/users": {
                    "post": {
                        "operationId": "createUser",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["name", "email"],
                                        "properties": {
                                            "name": {"type": "string"},
                                            "email": {"type": "string", "format": "email"},
                                            "nickname": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        },
                        "responses": {"201": {"description": "created"}},
                    }
                }
            },
        },
    )
    text = generate_smoke(spec)
    tests = _parsed_tests(text)
    step = tests["createUser"]["steps"][0]
    assert step["action"] == "POST"
    assert step["data"] == {"name": "string", "email": "user@example.com"}
    assert "nickname" not in step["data"]
    assert tests["createUser"].get("skip") in (None, False, "")


def test_required_body_optional_only_properties_allows_empty_object(tmp_path):
    spec = _write_spec(
        tmp_path,
        "opt.json",
        {
            "paths": {
                "/items": {
                    "post": {
                        "operationId": "createItem",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"label": {"type": "string"}},
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            }
        },
    )
    text = generate_smoke(spec)
    step = _parsed_tests(text)["createItem"]["steps"][0]
    assert step["data"] == {}


def test_required_nested_properties(tmp_path):
    spec = _write_spec(
        tmp_path,
        "nested.json",
        {
            "paths": {
                "/profiles": {
                    "post": {
                        "operationId": "createProfile",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["user"],
                                        "properties": {
                                            "user": {
                                                "type": "object",
                                                "required": ["id"],
                                                "properties": {"id": {"type": "integer"}},
                                            }
                                        },
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            }
        },
    )
    step = _parsed_tests(generate_smoke(spec))["createProfile"]["steps"][0]
    assert step["data"] == {"user": {"id": 0}}


def test_required_array_and_enum_and_primitive(tmp_path):
    spec = _write_spec(
        tmp_path,
        "misc.json",
        {
            "paths": {
                "/tags": {
                    "post": {
                        "operationId": "createTags",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"type": "string", "enum": ["a", "b"]},
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                },
                "/ping": {
                    "post": {
                        "operationId": "ping",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {"schema": {"type": "boolean"}}
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                },
            }
        },
    )
    tests = _parsed_tests(generate_smoke(spec))
    assert tests["createTags"]["steps"][0]["data"] == ["a"]
    assert tests["ping"]["steps"][0]["data"] is True


def test_required_ref_schema(tmp_path):
    spec = _write_spec(
        tmp_path,
        "ref.json",
        {
            "components": {
                "schemas": {
                    "User": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {"name": {"type": "string"}},
                    }
                }
            },
            "paths": {
                "/users": {
                    "post": {
                        "operationId": "createUser",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/User"}
                                }
                            },
                        },
                        "responses": {"201": {"description": "created"}},
                    }
                }
            },
        },
    )
    step = _parsed_tests(generate_smoke(spec))["createUser"]["steps"][0]
    assert step["data"] == {"name": "string"}


def test_required_unsupported_schema_skips_instead_of_empty_body(tmp_path):
    spec = _write_spec(
        tmp_path,
        "oneof.json",
        {
            "paths": {
                "/users": {
                    "post": {
                        "operationId": "createUser",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "oneOf": [
                                            {"type": "object", "required": ["a"]},
                                            {"type": "object", "required": ["b"]},
                                        ]
                                    }
                                }
                            },
                        },
                        "responses": {"201": {"description": "created"}},
                    }
                }
            }
        },
    )
    text = generate_smoke(spec)
    assert "SKIP:" in text
    assert "could not be synthesized" in text
    assert "BODY: {}" not in text
    tests = _parsed_tests(text)
    assert tests["createUser"].get("skip")
    assert tests["createUser"]["steps"][0].get("data") is None


def test_optional_or_absent_body_still_emits_empty_object(tmp_path):
    spec = _write_spec(
        tmp_path,
        "optional.json",
        {
            "paths": {
                "/users": {
                    "put": {
                        "operationId": "replaceUsers",
                        "responses": {"200": {"description": "ok"}},
                    },
                    "post": {
                        "operationId": "createOptional",
                        "requestBody": {
                            "required": False,
                            "content": {
                                "application/json": {
                                    "schema": {"oneOf": [{"type": "string"}]}
                                }
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    },
                }
            }
        },
    )
    text = generate_smoke(spec)
    tests = _parsed_tests(text)
    assert tests["replaceUsers"]["steps"][0]["data"] == {}
    assert tests["createOptional"]["steps"][0]["data"] == {}
    assert not tests["createOptional"].get("skip")


def test_example_still_preferred_over_synthesis(tmp_path):
    spec = _write_spec(
        tmp_path,
        "ex.json",
        {
            "paths": {
                "/users": {
                    "post": {
                        "operationId": "createUser",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "example": {"name": "Jane"},
                                    "schema": {
                                        "type": "object",
                                        "required": ["name", "email"],
                                        "properties": {
                                            "name": {"type": "string"},
                                            "email": {"type": "string"},
                                        },
                                    },
                                }
                            },
                        },
                        "responses": {"201": {"description": "created"}},
                    }
                }
            }
        },
    )
    step = _parsed_tests(generate_smoke(spec))["createUser"]["steps"][0]
    assert step["data"] == {"name": "Jane"}


def test_nullable_string_synthesizes_string(tmp_path):
    spec = _write_spec(
        tmp_path,
        "null.json",
        {
            "paths": {
                "/notes": {
                    "post": {
                        "operationId": "createNote",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["text"],
                                        "properties": {
                                            "text": {"type": ["string", "null"]}
                                        },
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            }
        },
    )
    step = _parsed_tests(generate_smoke(spec))["createNote"]["steps"][0]
    assert step["data"] == {"text": "string"}
