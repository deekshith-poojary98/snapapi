from snapapi.variables import interpolate
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
