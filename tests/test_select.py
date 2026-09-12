from snapapi.exceptions import SnapAPIError
from snapapi.select import eval_keyword_expr, eval_tag_expr, parse_bool_expr
import pytest


def test_keyword_or_and_not():
    expr = parse_bool_expr("Login or Health")
    assert eval_keyword_expr(expr, "Login rejects a missing password")
    assert eval_keyword_expr(expr, "Health check")
    assert not eval_keyword_expr(expr, "Change password")

    expr = parse_bool_expr("not Health")
    assert eval_keyword_expr(expr, "Login")
    assert not eval_keyword_expr(expr, "Health")

    expr = parse_bool_expr("Create User")
    assert eval_keyword_expr(expr, "Create User")
    assert not eval_keyword_expr(expr, "Create")


def test_tag_expression_and_parentheses():
    expr = parse_bool_expr("smoke and not wip")
    assert eval_tag_expr(expr, ["smoke", "auth"])
    assert not eval_tag_expr(expr, ["smoke", "wip"])
    assert not eval_tag_expr(expr, ["auth"])

    expr = parse_bool_expr("(smoke or health) and not slow")
    assert eval_tag_expr(expr, ["health"])
    assert not eval_tag_expr(expr, ["health", "slow"])


def test_quoted_keyword_term():
    expr = parse_bool_expr('"Create User" or Health')
    assert eval_keyword_expr(expr, "TEST Create User")
    assert not eval_keyword_expr(expr, "Create something User")


def test_invalid_expression():
    with pytest.raises(SnapAPIError, match="Invalid expression"):
        parse_bool_expr("Login && Health")
