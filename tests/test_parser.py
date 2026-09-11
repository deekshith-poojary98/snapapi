import pytest

from snapapi.exceptions import ParseError
from snapapi.parser import TestParser
from tests.helpers import parse_dsl


def test_parses_suite_test_url_options_and_request():
    suite = parse_dsl(
        """
SUITE: Demo
DESC: Suite description
OPTIONS: {"STOP-ON-FAILURE": false, "TIMEOUT": 5}
URL: https://api.example.com

TEST: List Users
DESC: fetch users
TAG: users, read
  REQUEST: GET /api/users
  EXPECT: STATUS 200
"""
    )
    assert suite["name"] == "Demo"
    assert suite["description"] == "Suite description"
    assert suite["base_url"] == "https://api.example.com"
    assert suite["options"]["STOP-ON-FAILURE"] is False
    assert suite["options"]["TIMEOUT"] == 5
    test = suite["tests"][0]
    assert test["name"] == "List Users"
    assert test["description"] == "fetch users"
    assert test["tags"] == ["users", "read"]
    assert test["base_url"] == "https://api.example.com"
    assert test["steps"][0]["action"] == "GET"
    assert test["steps"][0]["endpoint"] == "/api/users"
    assert test["steps"][0]["checks"][0]["type"] == "STATUS"
    assert test["steps"][0]["checks"][0]["value"] == "200"


def test_data_attaches_to_current_request_not_as_new_step():
    suite = parse_dsl(
        """
SUITE: Demo
TEST: Create
  REQUEST: POST /api/users
  DATA: {"name": "Jane", "email": "jane@example.com"}
  EXPECT: STATUS 201
"""
    )
    steps = suite["tests"][0]["steps"]
    assert len(steps) == 1
    assert steps[0]["action"] == "POST"
    assert steps[0]["data"] == {"name": "Jane", "email": "jane@example.com"}
    assert steps[0]["checks"][0]["type"] == "STATUS"


def test_headers_attach_to_current_request():
    suite = parse_dsl(
        """
SUITE: Demo
TEST: Auth
  REQUEST: GET /api/me
  HEADERS: {"Authorization": "Bearer ${TOKEN}"}
  EXPECT: STATUS 200
"""
    )
    step = suite["tests"][0]["steps"][0]
    assert step["headers"]["Authorization"] == "Bearer ${TOKEN}"


def test_suite_level_headers_copy_onto_tests():
    suite = parse_dsl(
        """
SUITE: Demo
HEADERS: {"X-Suite": "1"}
TEST: A
  REQUEST: GET /ping
  EXPECT: STATUS 200
"""
    )
    assert suite["headers"]["X-Suite"] == "1"
    assert suite["tests"][0]["headers"]["X-Suite"] == "1"


def test_save_attaches_to_request():
    suite = parse_dsl(
        """
SUITE: Demo
TEST: Create
  REQUEST: POST /api/users
  DATA: {"name": "Jane"}
  EXPECT: STATUS 201
  SAVE: userId FROM $.data.id
"""
    )
    saves = suite["tests"][0]["steps"][0]["saves"]
    assert saves == [{"name": "userId", "path": "$.data.id"}]


def test_expect_json_and_header_and_retry():
    suite = parse_dsl(
        """
SUITE: Demo
TEST: Checks
  REQUEST: GET /api/users/2
  EXPECT: JSON $.data.email == "jane@example.com"
  EXPECT: HEADER Content-Type CONTAINS json
  EXPECT: STATUS 200 RETRY 5
  EXPECT: CONTAINS data
"""
    )
    checks = suite["tests"][0]["steps"][0]["checks"]
    assert checks[0]["type"] == "JSON"
    assert checks[0]["path"] == "$.data.email"
    assert checks[0]["operator"] == "=="
    assert checks[0]["value"] == "jane@example.com"
    assert checks[1]["type"] == "HEADER"
    assert checks[1]["name"] == "Content-Type"
    assert checks[1]["operator"] == "CONTAINS"
    assert checks[1]["value"] == "json"
    assert checks[2]["type"] == "STATUS"
    assert checks[2]["retry"] == 5
    assert checks[3]["type"] == "CONTAINS"
    assert checks[3]["value"] == "data"


def test_setup_teardown_and_tag():
    suite = parse_dsl(
        """
SUITE: Demo
TEST: Setup User
  REQUEST: POST /users
  EXPECT: STATUS 201
TEST: Cleanup User
  REQUEST: DELETE /users/1
  EXPECT: STATUS 200
TEST: Main
TAG: user
SETUP: Setup User
TEARDOWN: Cleanup User
  REQUEST: GET /users
  EXPECT: STATUS 200
"""
    )
    main = suite["test_map"]["Main"]
    assert main["setup"] == "Setup User"
    assert main["teardown"] == "Cleanup User"
    assert main["tags"] == ["user"]


def test_comments_are_ignored():
    suite = parse_dsl(
        """
// suite comment
SUITE: Demo
// ignored
TEST: A
  REQUEST: GET /ping
  // inline-looking but whole line
  EXPECT: STATUS 200
"""
    )
    assert suite["name"] == "Demo"
    assert len(suite["tests"]) == 1
    assert len(suite["tests"][0]["steps"][0]["checks"]) == 1


def test_slash_alone_is_not_a_comment():
    with pytest.raises(ParseError, match="Invalid line"):
        parse_dsl(
            """
SUITE: Demo
TEST: A
  REQUEST: GET /ping
/ old comment style
"""
        )


def test_unknown_keyword_is_parse_error_with_line():
    with pytest.raises(ParseError, match="Unknown keyword 'FOOBAR'") as exc:
        parse_dsl(
            """
SUITE: Demo
FOOBAR: nope
"""
        )
    assert exc.value.lineno == 3


def test_bad_json_is_parse_error():
    with pytest.raises(ParseError, match="Invalid JSON"):
        parse_dsl(
            """
SUITE: Demo
TEST: A
  REQUEST: POST /x
  DATA: {not json}
"""
        )


def test_unknown_http_method_is_parse_error():
    with pytest.raises(ParseError, match="Unknown HTTP method 'BLAST'"):
        parse_dsl(
            """
SUITE: Demo
TEST: A
  REQUEST: BLAST /x
"""
        )


def test_data_without_request_is_parse_error():
    with pytest.raises(ParseError, match="DATA must follow a REQUEST"):
        parse_dsl(
            """
SUITE: Demo
TEST: A
  DATA: {"a": 1}
"""
        )


def test_unknown_setup_name_is_parse_error():
    with pytest.raises(ParseError, match="Unknown SETUP test 'Missing'"):
        parse_dsl(
            """
SUITE: Demo
TEST: A
SETUP: Missing
  REQUEST: GET /x
  EXPECT: STATUS 200
"""
        )


def test_setup_teardown_cycle_is_parse_error():
    with pytest.raises(ParseError, match="SETUP/TEARDOWN cycle detected"):
        parse_dsl(
            """
SUITE: Demo
TEST: A
SETUP: B
  REQUEST: GET /a
  EXPECT: STATUS 200
TEST: B
SETUP: A
  REQUEST: GET /b
  EXPECT: STATUS 200
"""
        )


def test_multiline_data_json():
    suite = parse_dsl(
        """
SUITE: Demo
TEST: A
  REQUEST: POST /users
  DATA: {
    "name": "Jane",
    "email": "jane@example.com"
  }
  EXPECT: STATUS 201
"""
    )
    assert suite["tests"][0]["steps"][0]["data"]["email"] == "jane@example.com"


def test_test_level_url_overrides_suite():
    suite = parse_dsl(
        """
SUITE: Demo
URL: https://suite.example.com
TEST: A
URL: https://test.example.com
  REQUEST: GET /x
  EXPECT: STATUS 200
"""
    )
    assert suite["base_url"] == "https://suite.example.com"
    assert suite["tests"][0]["base_url"] == "https://test.example.com"


def test_default_stop_on_failure_true():
    suite = parse_dsl(
        """
SUITE: Demo
TEST: A
  REQUEST: GET /x
  EXPECT: STATUS 200
"""
    )
    assert suite["options"]["STOP-ON-FAILURE"] is True


def test_import_merges_tests(tmp_path):
    helper = tmp_path / "helper.snaptest"
    helper.write_text(
        """
SUITE: Helper
URL: https://imported.example.com
TEST: Imported Setup
  REQUEST: POST /setup
  EXPECT: STATUS 200
""",
        encoding="utf-8",
    )
    main = tmp_path / "main.snaptest"
    main.write_text(
        f"""
SUITE: Main
IMPORT: helper.snaptest
TEST: Uses Import
SETUP: Imported Setup
  REQUEST: GET /ok
  EXPECT: STATUS 200
""",
        encoding="utf-8",
    )
    suite = TestParser().parse(main)
    assert "Imported Setup" in suite["test_map"]
    assert suite["test_map"]["Uses Import"]["setup"] == "Imported Setup"


def test_import_cycle(tmp_path):
    a = tmp_path / "a.snaptest"
    b = tmp_path / "b.snaptest"
    a.write_text("SUITE: A\nIMPORT: b.snaptest\n", encoding="utf-8")
    b.write_text("SUITE: B\nIMPORT: a.snaptest\n", encoding="utf-8")
    with pytest.raises(ParseError, match="IMPORT cycle detected"):
        TestParser().parse(a)


def test_unknown_expect_type():
    with pytest.raises(ParseError, match="Unknown EXPECT check 'MAGIC'"):
        parse_dsl(
            """
SUITE: Demo
TEST: A
  REQUEST: GET /x
  EXPECT: MAGIC foo
"""
        )


def test_sample_suite_parses():
    from pathlib import Path

    suite = TestParser().parse(Path(__file__).parent / "test_suite.snaptest")
    assert suite["name"] == "Reqres sample APIs"
    assert suite["base_url"] == "https://reqres.in"
    create = suite["test_map"]["Create User"]
    assert create["steps"][0]["data"]["name"] == "Jane"
    assert create["steps"][0]["saves"][0]["name"] == "userId"
    assert suite["test_map"]["Update User"]["setup"] == "Create User"
    assert suite["test_map"]["Update User"]["teardown"] == "Cleanup User"

