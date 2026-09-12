from snapapi.jsonpath import extract
from snapapi.exceptions import JsonPathError
import pytest


def test_dot_path():
    data = {"data": {"email": "jane@example.com", "id": 2}}
    assert extract(data, "$.data.email") == "jane@example.com"
    assert extract(data, "$.data.id") == 2


def test_numeric_index_dot_and_bracket():
    data = {"items": [{"id": 10}, {"id": 20}]}
    assert extract(data, "$.items.0.id") == 10
    assert extract(data, "$.items[1].id") == 20


def test_root():
    assert extract({"a": 1}, "$") == {"a": 1}


def test_missing_path():
    with pytest.raises(JsonPathError, match="not found"):
        extract({"a": 1}, "$.b.c")


def test_requires_dollar():
    with pytest.raises(JsonPathError, match="must start with"):
        extract({"a": 1}, "a.b")


def test_wildcard_map():
    data = {"items": [{"id": 10}, {"id": 20}]}
    assert extract(data, "$.items[*].id") == [10, 20]


def test_filter_equality():
    data = {"items": [{"id": 1, "status": "open"}, {"id": 2, "status": "closed"}]}
    assert extract(data, '$.items[?(@.status=="open")]') == [{"id": 1, "status": "open"}]
    assert extract(data, "$.items[?(@.id==1)]") == [{"id": 1, "status": "open"}]
    assert extract(data, '$.items[?(@.status=="open")].id') == [1]


def test_filter_root_array():
    data = [{"x": 1}, {"x": 2}]
    assert extract(data, "$[?(@.x==1)]") == [{"x": 1}]
