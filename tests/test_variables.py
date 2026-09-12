from snapapi.variables import discover_env_file, interpolate, resolve_env_file
from snapapi.exceptions import SnapAPIError
import pytest


def test_interpolate_strings_dicts_and_lists():
    variables = {"TOKEN": "abc", "ID": 7}
    assert interpolate("Bearer ${TOKEN}", variables) == "Bearer abc"
    assert interpolate({"Authorization": "Bearer ${TOKEN}"}, variables) == {"Authorization": "Bearer abc"}
    assert interpolate(["/users/${ID}"], variables) == ["/users/7"]


def test_undefined_variable():
    with pytest.raises(SnapAPIError, match="Undefined variable"):
        interpolate("/${missing}", {})


def test_undefined_uppercase_variable_hints_env():
    with pytest.raises(SnapAPIError, match="pass --env"):
        interpolate("${BASE_URL}", {})


def test_discover_env_file_prefers_stem(tmp_path):
    suite = tmp_path / "users.sapi"
    suite.write_text("SUITE: X\n", encoding="utf-8")
    stem = tmp_path / "users.env"
    stem.write_text("A=1\n", encoding="utf-8")
    other = tmp_path / "shared.env"
    other.write_text("A=2\n", encoding="utf-8")
    assert discover_env_file(suite) == str(stem)


def test_discover_env_file_single_sibling(tmp_path):
    suite = tmp_path / "connecthr-auth.sapi"
    suite.write_text("SUITE: X\n", encoding="utf-8")
    sibling = tmp_path / "connecthr.env"
    sibling.write_text("BASE_URL=https://example.com\n", encoding="utf-8")
    (tmp_path / "connecthr.env.example").write_text("BASE_URL=nope\n", encoding="utf-8")
    assert discover_env_file(suite) == str(sibling)


def test_discover_env_file_skips_when_multiple_siblings(tmp_path):
    suite = tmp_path / "users.sapi"
    suite.write_text("SUITE: X\n", encoding="utf-8")
    (tmp_path / "a.env").write_text("A=1\n", encoding="utf-8")
    (tmp_path / "b.env").write_text("B=2\n", encoding="utf-8")
    assert discover_env_file(suite) is None


def test_resolve_env_file_keeps_explicit(tmp_path):
    suite = tmp_path / "users.sapi"
    suite.write_text("SUITE: X\n", encoding="utf-8")
    explicit = tmp_path / "custom.env"
    explicit.write_text("A=1\n", encoding="utf-8")
    (tmp_path / "users.env").write_text("A=2\n", encoding="utf-8")
    assert resolve_env_file(str(explicit), suite) == str(explicit)
