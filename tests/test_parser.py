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
    assert saves == [{"name": "userId", "source": "json", "path": "$.data.id"}]


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


def test_first_class_timeout_and_stop_on_failure():
    suite = parse_dsl(
        """
SUITE: Demo
TIMEOUT: 10
STOP-ON-FAILURE: false
TEST: A
  REQUEST: GET /x
  EXPECT: STATUS 200
"""
    )
    assert suite["options"]["TIMEOUT"] == 10
    assert suite["options"]["STOP-ON-FAILURE"] is False


def test_first_class_options_merge_with_options_json():
    suite = parse_dsl(
        """
SUITE: Demo
OPTIONS: {"STOP-ON-FAILURE": false}
TIMEOUT: 7
TEST: A
  REQUEST: GET /x
  EXPECT: STATUS 200
"""
    )
    assert suite["options"]["STOP-ON-FAILURE"] is False
    assert suite["options"]["TIMEOUT"] == 7


def test_http_method_aliases_and_body():
    suite = parse_dsl(
        """
SUITE: Demo
TEST: Create
  POST: /users
  BODY: {"name": "Jane"}
  EXPECT: STATUS 201
TEST: Fetch
  GET: /users/1
  EXPECT: STATUS 200
"""
    )
    assert suite["tests"][0]["steps"][0]["action"] == "POST"
    assert suite["tests"][0]["steps"][0]["endpoint"] == "/users"
    assert suite["tests"][0]["steps"][0]["data"] == {"name": "Jane"}
    assert suite["tests"][1]["steps"][0]["action"] == "GET"


def test_header_line_suite_test_and_step():
    suite = parse_dsl(
        """
SUITE: Demo
HEADER Content-Type: application/json
TEST: Auth
HEADER X-Test: suite-test
  GET: /me
  HEADER Authorization: Bearer ${TOKEN}
  EXPECT: STATUS 200
"""
    )
    assert suite["headers"]["Content-Type"] == "application/json"
    test = suite["tests"][0]
    assert test["headers"]["Content-Type"] == "application/json"
    assert test["headers"]["X-Test"] == "suite-test"
    assert test["steps"][0]["headers"]["Authorization"] == "Bearer ${TOKEN}"


def test_header_colon_form_and_headers_json_still_work():
    suite = parse_dsl(
        """
SUITE: Demo
HEADERS: {"X-Suite": "1"}
TEST: A
  REQUEST: GET /x
  HEADER: X-Step: 2
  EXPECT: STATUS 200
"""
    )
    assert suite["headers"]["X-Suite"] == "1"
    assert suite["tests"][0]["steps"][0]["headers"]["X-Step"] == "2"


def test_auth_sets_authorization_header():
    suite = parse_dsl(
        """
SUITE: Demo
TEST: A
  GET: /secure
  AUTH: bearer ${TOKEN}
  EXPECT: STATUS 200
"""
    )
    assert suite["tests"][0]["steps"][0]["headers"]["Authorization"] == "Bearer ${TOKEN}"


def test_query_and_param_attach_to_request():
    suite = parse_dsl(
        """
SUITE: Demo
TEST: A
  GET: /users?existing=1
  QUERY: page=2&limit=10
  PARAM: sort name
  PARAM: filter=active
  EXPECT: STATUS 200
"""
    )
    query = suite["tests"][0]["steps"][0]["query"]
    assert query["page"] == "2"
    assert query["limit"] == "10"
    assert query["sort"] == "name"
    assert query["filter"] == "active"
    assert suite["tests"][0]["steps"][0]["endpoint"] == "/users?existing=1"


def test_unified_expect_forms():
    suite = parse_dsl(
        """
SUITE: Demo
TEST: Checks
  GET: /api/users/2
  EXPECT: status == 200
  EXPECT: body contains id
  EXPECT: json $.email == "jane@example.com"
  EXPECT: header Content-Type contains json
  EXPECT: STATUS 201 RETRY 5
"""
    )
    checks = suite["tests"][0]["steps"][0]["checks"]
    assert checks[0]["type"] == "STATUS"
    assert checks[0]["value"] == "200"
    assert checks[0]["operator"] == "=="
    assert checks[1]["type"] == "CONTAINS"
    assert checks[1]["value"] == "id"
    assert checks[2]["type"] == "JSON"
    assert checks[2]["path"] == "$.email"
    assert checks[2]["operator"] == "=="
    assert checks[2]["value"] == "jane@example.com"
    assert checks[3]["type"] == "HEADER"
    assert checks[3]["name"] == "Content-Type"
    assert checks[3]["operator"] == "CONTAINS"
    assert checks[3]["value"] == "json"
    assert checks[4]["type"] == "STATUS"
    assert checks[4]["value"] == "201"
    assert checks[4]["retry"] == 5


def test_expect_status_without_spaces_around_operator():
    suite = parse_dsl(
        """
SUITE: Demo
TEST: A
  GET: /x
  EXPECT: status==200
"""
    )
    assert suite["tests"][0]["steps"][0]["checks"][0]["value"] == "200"


def test_tag_space_separated():
    suite = parse_dsl(
        """
SUITE: Demo
TEST: A
TAG: users write
  GET: /x
  EXPECT: status == 200
"""
    )
    assert suite["tests"][0]["tags"] == ["users", "write"]


def test_parse_error_includes_filename_and_line(tmp_path):
    path = tmp_path / "broken.snaptest"
    path.write_text("SUITE: Demo\nFOOBAR: nope\n", encoding="utf-8")
    with pytest.raises(ParseError, match="Unknown keyword 'FOOBAR'") as exc:
        TestParser().parse(path)
    assert exc.value.lineno == 2
    assert exc.value.filename == str(path.resolve())
    assert f"{path.resolve()}:2:" in str(exc.value)


def test_body_without_request_is_parse_error():
    with pytest.raises(ParseError, match="BODY must follow a REQUEST"):
        parse_dsl(
            """
SUITE: Demo
TEST: A
  BODY: {"a": 1}
"""
        )


def test_query_without_request_is_parse_error():
    with pytest.raises(ParseError, match="QUERY must follow a REQUEST"):
        parse_dsl(
            """
SUITE: Demo
TEST: A
  QUERY: page=2
"""
        )


def test_timeout_inside_test_is_parse_error():
    with pytest.raises(ParseError, match="TIMEOUT must appear at suite level"):
        parse_dsl(
            """
SUITE: Demo
TEST: A
TIMEOUT: 5
  GET: /x
  EXPECT: STATUS 200
"""
        )


def test_recommended_suite_parses():
    from pathlib import Path

    suite = TestParser().parse(Path(__file__).parent / "recommended.snaptest")
    assert suite["name"] == "Recommended syntax"
    assert suite["options"]["TIMEOUT"] == 5
    assert suite["options"]["STOP-ON-FAILURE"] is False
    assert suite["headers"]["Content-Type"] == "application/json"
    create = suite["test_map"]["Create User"]
    assert create["tags"] == ["users", "write"]
    assert create["steps"][0]["action"] == "POST"
    assert create["steps"][0]["data"]["email"] == "jane@example.com"
    assert create["steps"][0]["headers"]["Authorization"] == "Bearer ${TOKEN}"
    assert create["steps"][0]["checks"][0]["type"] == "STATUS"
    assert create["steps"][0]["checks"][0]["value"] == "201"
    listing = suite["test_map"]["List Users"]
    assert listing["steps"][0]["query"]["page"] == "2"
    assert listing["steps"][0]["query"]["sort"] == "name"


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

