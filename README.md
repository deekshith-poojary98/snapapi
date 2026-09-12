# SnapAPI

SnapAPI is a lightweight HTTP API testing framework with a small custom DSL.
Write `.snaptest` files, then run them from the CLI.

## Features

- Human-readable DSL for GET, POST, PUT, PATCH, and DELETE
- Attach `BODY`/`DATA`, `HEADER`s, `QUERY`/`PARAM`, and `AUTH` to the current request
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
| `--name "Create User"` | Run tests with this name (repeatable) |
| `--env .env` | Load `KEY=VALUE` pairs for `${VAR}` interpolation |
| `--timeout 10` | HTTP timeout in seconds (overrides `TIMEOUT`) |
| `--report json:report.json` | Write a JSON report |
| `--report junit:report.xml` | Write a JUnit XML report |
| `--report html:report.html` | Write a self-contained HTML report |
| `--stop-on-failure` | Stop each suite on the first failed test |
| `--profile stage` | Load `environments/stage.env`, `.snapapi/stage.env`, or `stage.env` |
| `--workers N` | Run independent tests in parallel (SAVE is isolated per test; sibling SAVE falls back to sequential) |
| `--grep regex` | Filter tests by name/description |
| `--last-failed` | Re-run failures from `.snapapi/last-run.json` (matches file + suite + name, including `Test [row]`) |
| `--mode record\|replay\|record-on-miss` | VCR cassettes under `.snapapi/cassettes/` |
| `--record-on-miss` | With `--mode replay`, hit the network and save when a cassette is missing |
| `--on-fail curl` / `--on-fail har:dir` | Emit a redacted curl or HAR on failure |
| `--safe-url` | Block private/metadata hosts |
| `--proxy URL` | HTTP/HTTPS proxy |
| `--insecure` | Skip TLS certificate verification |
| `--cert PATH` | Client certificate |
| `--cacert PATH` | CA bundle used to verify TLS |
| `snapapi lint PATH` | Parse/validate without HTTP |
| `snapapi fmt PATH` | Format `.snaptest` files |
| `snapapi openapi spec.yaml` | Generate GET/POST/PUT/PATCH/DELETE smoke tests |
| `snapapi history [--failed] [--since 7d]` | Print `.snapapi/history.jsonl` |
| `snapapi mock mock.json [--port 0]` | Serve routes from a JSON mock file (prints the URL) |
| `snapapi watch PATH` | Re-run when `.snaptest` files change |

The process exits `0` when every test passed, `1` when a test failed, and `2` on parse or usage errors.

## DSL

Recommended form:

```
SUITE: Book Store
DESC: Validates the user API
TIMEOUT: 10
STOP-ON-FAILURE: false
URL: https://api.example.com
HEADER Content-Type: application/json

TEST: Create User
DESC: Create a user and keep the id
TAG: users write
  POST: /users
  AUTH: bearer ${TOKEN}
  BODY: {"name": "Jane", "email": "jane@example.com"}
  EXPECT: status == 201
  EXPECT: body contains id
  SAVE: userId FROM $.id

TEST: Get User
TAG: users
SETUP: Create User
  GET: /users/${userId}
  EXPECT: status == 200
  EXPECT: json $.email == "jane@example.com"
  EXPECT: header Content-Type contains json

TEST: List Users
  GET: /users
  QUERY: page=2&limit=10
  PARAM: sort name
  EXPECT: status == 200

TEST: Wait for ready
  GET: /jobs/${id}
  WAIT: json $.status == "ready" TIMEOUT 10s BACKOFF 0.5s
  EXPECT: status == 200
```

Comments are `//` lines. Indentation is cosmetic. JSON bodies may be one line or span multiple lines.

The older forms still parse:

```
OPTIONS: {"STOP-ON-FAILURE": false, "TIMEOUT": 10}
HEADERS: {"Content-Type": "application/json"}
TAG: users, write
REQUEST: POST /users
HEADERS: {"Authorization": "Bearer ${TOKEN}"}
DATA: {"name": "Jane"}
EXPECT: STATUS 201
EXPECT: CONTAINS id
EXPECT: JSON $.email == "jane@example.com"
EXPECT: HEADER Content-Type CONTAINS json
```

### Keywords

- Suite: `SUITE`, `DESC`, `URL`, `TIMEOUT`, `STOP-ON-FAILURE`, `FOLLOW-REDIRECTS`, `OPTIONS`, `IMPORT`, `SUITE SETUP`, `SET`
- Test: `TEST`, `TAG`, `SETUP`, `TEARDOWN`, `SKIP`, `ONLY`, `QUARANTINE`, `EXAMPLES`, `SET`
- Request: `REQUEST`, `GET`/`POST`/`PUT`/`PATCH`/`DELETE`, `BODY`/`DATA`, `FILE`, `GRAPHQL`, `HEADER`/`HEADERS`, `QUERY`, `PARAM`, `AUTH`, `EXPECT`, `SAVE`, `WAIT`, `SET`

`SETUP` / `TEARDOWN` name another `TEST`. Those helper tests are not run as standalone cases.

`IMPORT: other.snaptest` pulls tests from another file (paths are relative to the current file).

`SET: orderId ${uuid()}` assigns an interpolated value (including helpers) without an HTTP call. It may appear at suite, test, or step level.

`WAIT: json $.status == "ready" TIMEOUT 10s BACKOFF 0.5s` reissues the current request until the check passes or the timeout expires. `EXPECT: json $.status == "ready" RETRY 20 BACKOFF 0.5s` also retries when the HTTP status is already 200.

`AUTH: bearer ${TOKEN}` sets `Authorization: Bearer ${TOKEN}`. Explicit `HEADER` lines still work.

`AUTH: oauth2 grant=client_credentials token_url=... client_id=...` and `grant=password username=... password=...` fetch a token (cached). If the token response includes `refresh_token`, a 401 retries once after refresh. Browser PKCE is not implemented.

`QUERY: page=2&limit=10` and `PARAM: page 2` attach query parameters to the current request (they merge with any query string already in the path).

`OPTIONS: {"OPENAPI": "spec.yaml"}` validates JSON responses against the matching path+method response schema when present. `EXPECT: openapi ./spec.yaml` does the same for one step. Partial path match (`/users/{id}` vs `/users/1`) is allowed; missing schemas are skipped.

### Checks

Check types are case-insensitive. Preferred:

```
EXPECT: status == 200
EXPECT: status != 500
EXPECT: status == 200 RETRY 5 ON 5xx BACKOFF 1s
EXPECT: body contains userId
EXPECT: body not contains stack
EXPECT: json $.email matches ^.+@example\\.com$
EXPECT: json $.items length == 3
EXPECT: json $.score > 0
EXPECT: json $.tags contains "admin"
EXPECT: schema ./schemas/user.json
EXPECT: duration < 200ms
EXPECT: header Content-Type contains json
EXPECT: openapi ./openapi.yaml
```

Also accepted:

```
EXPECT: STATUS 200
EXPECT: CONTAINS userId
EXPECT: JSON $.data.email == "jane@example.com"
EXPECT: HEADER Content-Type CONTAINS json
```

JSONPath is a small subset: `$.a.b`, `$.items.0.id`, `$.items[0].id`, and `$.items[*].id`. Filter expressions such as `$[?(@.x==1)]` are not supported.

### Variables

`${NAME}` is expanded in URLs, paths, headers, data, and expect values.

Lookup order: process environment, then `--env` file, then `SET` / `SAVE` values.

## Sample suite

`tests/recommended.snaptest` shows the current DSL against a local mock server (pytest injects `BASE_URL`). `tests/test_suite.snaptest` is a classic-syntax example against [reqres.in](https://reqres.in) and needs network access. Automated tests in `tests/test_*.py` use a local mock HTTP server and do not call reqres. CI replays `tests/fixtures/offline.snaptest` from a checked-in cassette.

HTML reports include redacted request/response bodies. VCR cassette keys include method, sorted query string, `Content-Type`/`Accept`, and body. Replay restores `Set-Cookie` onto the session.

`snapapi mock tests/fixtures/mock.json --port 0` serves `{"routes":[{"method":"GET","path":"/ping","status":200,"json":{"ok":true}}]}`. There is no language server, gRPC, or WebSocket support.

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

The `snapapi-language` extension is a language pack for `.snaptest` files: syntax highlighting, snippets, completions, lightweight diagnostics, and **SnapAPI: Run current file** / **Run test at cursor**. See [snapapi-language/README.md](snapapi-language/README.md).

## License

MIT — see [LICENSE](LICENSE).
