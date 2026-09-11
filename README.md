# SnapAPI

SnapAPI is a lightweight HTTP API testing framework with a small custom DSL.
Write `.snaptest` files, then run them from the CLI.

## Features

- Human-readable DSL for GET, POST, PUT, PATCH, and DELETE
- Attach `DATA` and `HEADERS` to the current `REQUEST`
- `EXPECT` checks: status, body contains, JSONPath, response headers
- `SAVE` values from JSON responses and reuse them as `${var}`
- Setup / teardown with cycle detection
- Env files, tag filters, timeouts, retries, JSON and JUnit reports
- VS Code syntax highlighting for `.snaptest` files

## Requirements

- Python 3.9 or newer

## Installation

```bash
git clone https://github.com/Deekshith-07/snapapi.git
cd snapapi
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Or with the pinned runtime dependencies:

```bash
pip install -r requirements.txt
pip install -e .
```

## CLI

```bash
snapapi path/to/file.snaptest
python -m snapapi path/to/file.snaptest
```

Pass multiple files or a directory of `.snaptest` files:

```bash
snapapi tests/test_suite.snaptest
snapapi tests/ suites/auth.snaptest
```

Options:

| Flag | Meaning |
| --- | --- |
| `--tag user` | Run tests that have this tag (repeatable; all given tags must match) |
| `--env .env` | Load `KEY=VALUE` pairs for `${VAR}` interpolation |
| `--timeout 10` | HTTP timeout in seconds (overrides `OPTIONS TIMEOUT`) |
| `--report json:report.json` | Write a JSON report |
| `--report junit:report.xml` | Write a JUnit XML report |
| `--stop-on-failure` | Stop each suite on the first failed test |

The process exits `0` when every test passed, `1` when a test failed, and `2` on parse or usage errors.

## DSL

```
SUITE: Book Store
DESC: Validates the user API
OPTIONS: {"STOP-ON-FAILURE": false, "TIMEOUT": 10}

URL: https://api.example.com
HEADERS: {"Content-Type": "application/json"}

TEST: Create User
DESC: Create a user and keep the id
TAG: users, write
  REQUEST: POST /users
  HEADERS: {"Authorization": "Bearer ${TOKEN}"}
  DATA: {"name": "Jane", "email": "jane@example.com"}
  EXPECT: STATUS 201
  EXPECT: CONTAINS id
  SAVE: userId FROM $.id

TEST: Get User
TAG: users
SETUP: Create User
  REQUEST: GET /users/${userId}
  EXPECT: STATUS 200
  EXPECT: JSON $.email == "jane@example.com"
  EXPECT: HEADER Content-Type CONTAINS json

TEST: Wait for ready
  REQUEST: GET /health
  EXPECT: STATUS 200 RETRY 5
```

Comments are `//` lines. JSON bodies may be one line or span multiple lines.

### Keywords

- `SUITE`, `DESC`, `URL`, `OPTIONS`, `IMPORT`
- `TEST`, `TAG`, `SETUP`, `TEARDOWN`
- `REQUEST`, `DATA`, `HEADERS`, `EXPECT`, `SAVE`

`SETUP` / `TEARDOWN` name another `TEST`. Those helper tests are not run as standalone cases.

`IMPORT: other.snaptest` pulls tests from another file (paths are relative to the current file).

### Checks

```
EXPECT: STATUS 200
EXPECT: STATUS 200 RETRY 5
EXPECT: CONTAINS userId
EXPECT: JSON $.data.email == "jane@example.com"
EXPECT: HEADER Content-Type CONTAINS json
```

JSONPath is a small subset: `$.a.b`, `$.items.0.id`, or `$.items[0].id`.

### Variables

`${NAME}` is expanded in URLs, paths, headers, data, and expect values.

Lookup order: process environment, then `--env` file, then values stored by `SAVE`.

## Sample suite

`tests/test_suite.snaptest` is an example against [reqres.in](https://reqres.in) and needs network access. Automated tests in `tests/test_*.py` use a local mock HTTP server and do not call reqres.

## Project layout

```
snapapi/
├── snapapi/              # Python package
│   ├── parser.py         # .snaptest DSL parser
│   ├── engine.py         # runner, checks, setup/teardown
│   ├── api_client.py     # requests wrapper
│   └── cli.py            # snapapi command
├── tests/                # pytest + sample .snaptest
├── snapapi-language/     # VS Code grammar / run command
├── pyproject.toml
└── README.md
```

## Running the tests

```bash
pytest
```

## VS Code

The `snapapi-language` extension highlights `.snaptest` files and adds **SnapAPI: Run current file**, which shells out to the `snapapi` CLI.

## License

MIT — see [LICENSE](LICENSE).
