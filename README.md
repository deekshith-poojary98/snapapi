# SnapAPI

[![PyPI version](https://badge.fury.io/py/pysnapapi.svg)](https://pypi.org/project/pysnapapi/)
[![Python](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/downloads/)
[![CI Tests](https://github.com/deekshith-poojary98/snapapi/actions/workflows/snapapi.yml/badge.svg)](https://github.com/deekshith-poojary98/snapapi/actions/workflows/snapapi.yml)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/deekshith-poojary98/snapapi)


SnapAPI is a small language for HTTP tests. Write a `.sapi` file, run `snapapi`, get PASS or FAIL.

It is not a replacement for every API framework. If your tests are mostly **request → assert → extract → request → assert**, that's the 80% SnapAPI is for. Loops, custom crypto inside the suite, WebSockets, gRPC, or importing 2,000 existing cases: keep the tool you have. One ugly value is [`CALL`](https://deekshith-poojary98.github.io/snapapi/guide/plugins.html) from `extensions/`. There is no `IF` / `FOR` / `WHILE` in the file — [when SnapAPI is not for you](https://deekshith-poojary98.github.io/snapapi/guide/not-for-you.html).

**Start here:** [playground](https://deekshith-poojary98.github.io/snapapi/playground.html) (no install) → [quick start](https://deekshith-poojary98.github.io/snapapi/guide/quick-start.html) (same GET on the CLI).

The PyPI package is **`pysnapapi`**. The command is **`snapapi`**. `pip install snapapi` is a different project.

## First PASS

You need something that answers `GET /users`. `snapapi mock` is a tiny server for that — local, not the public internet.

Create `mock.json`:

```json
{
  "routes": [
    {
      "method": "GET",
      "path": "/users",
      "status": 200,
      "json": { "data": [{ "id": 1, "name": "Ada" }] }
    }
  ]
}
```

Create `hello.sapi`:

```
SUITE: Hello API
URL: http://127.0.0.1:8765

TEST: Get Users
  GET: /users
  EXPECT: status == 200
```

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install pysnapapi
snapapi --version

snapapi mock mock.json --port 8765   # leave running
snapapi hello.sapi
```

You want `1 passed  0 failed`. Same files in the repo: `examples/hello/mock.json` and `examples/hello/hello.sapi`.

## After first PASS

Guide pages, in order:

1. [POST + BODY](https://deekshith-poojary98.github.io/snapapi/guide/post.html)
2. [SAVE](https://deekshith-poojary98.github.io/snapapi/guide/save.html) an id, then `${userId}`
3. [Env / AUTH](https://deekshith-poojary98.github.io/snapapi/guide/env.html)
4. [HELPER / SETUP](https://deekshith-poojary98.github.io/snapapi/guide/helpers.html)
5. [Your API](https://deekshith-poojary98.github.io/snapapi/guide/your-api.html) — put your real origin in `URL:`

[When SnapAPI is not for you](https://deekshith-poojary98.github.io/snapapi/guide/not-for-you.html) · [CALL](https://deekshith-poojary98.github.io/snapapi/guide/plugins.html) (`extensions/`) · [Troubleshooting](https://deekshith-poojary98.github.io/snapapi/guide/troubleshooting.html) if you’re stuck. Flag tables below are reference.

## Features

- Human-readable DSL for GET, POST, PUT, PATCH, DELETE, HEAD, and OPTIONS
- Attach `BODY`/`DATA`, `HEADER`s, `QUERY`/`PARAM`, and `AUTH` to the current request
- `EXPECT` checks: status, body contains, JSONPath (filters + collection asserts), XPath, response headers
- `SAVE` values from JSON responses and reuse them as `${var}`
- Setup / teardown with cycle detection
- Env files, tag filters, timeouts, retries, JSON and JUnit reports
- OpenAPI response/request contract checks, VCR cassettes, JSON mock server
- pytest plugin (`snapapi_run` / `@pytest.mark.snapapi`)
- Python `CALL` / `extensions/` for values the HTTP DSL will not grow keywords for
- VS Code syntax highlighting and diagnostics for `.sapi` files

## Requirements

- Python 3.9 or newer

## Installation

From PyPI (most people):

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install pysnapapi
snapapi --version
```

From this repository (contributors):

```bash
git clone https://github.com/deekshith-poojary98/snapapi.git
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

After a green hello. You don’t need this table to get a first PASS.

```bash
snapapi path/to/file.sapi
python -m snapapi path/to/file.sapi
```

Pass multiple files or a directory of `.sapi` files:

```bash
snapapi examples/hello/hello.sapi
snapapi examples/users/post.sapi
snapapi tests/
```

Options:

| Flag | Meaning |
| --- | --- |
| `-k "Login or not Health"` | Pytest-style keyword expression on name/description/tags |
| `-m "smoke and not slow"` | Pytest-style tag expression |
| `--tag user` | Run tests that have this tag (repeatable; all given tags must match) |
| `--exclude slow` | Skip tests with this tag (repeatable) |
| `--name "Create User"` | Run tests with this name (repeatable) |
| `--env .env` | Load `KEY=VALUE` pairs for `${VAR}`. If omitted, SnapAPI loads `<suite>.env`, then `.env` next to the file, then a single sibling `*.env` |
| `-D TOKEN=secret` | Set `${VAR}` from the CLI (repeatable) |
| `--timeout 10` | HTTP timeout in seconds (overrides `TIMEOUT`) |
| `--report json:report.json` | Write a JSON report |
| `--report junit:report.xml` | Write a JUnit XML report |
| `--report html:report.html` | Write a self-contained HTML report |
| `-x` / `--exitfirst` / `--stop-on-failure` | Stop each suite on the first failed test (off by default) |
| `--maxfail N` | Stop after N failures |
| `--collect-only` | List selected tests without HTTP |
| `-q` / `-v` | Quiet or verbose console |
| `--durations N` | Show the N slowest tests |
| `--profile stage` | Load `environments/stage.env`, `.snapapi/stage.env`, or `stage.env` |
| `--workers N` | Run independent tests in parallel (SAVE is isolated per test; sibling SAVE falls back to sequential) |
| `--grep regex` | Filter tests by name/description |
| `--lf` / `--last-failed` | Re-run failures from `.snapapi/last-run.json` (matches file + suite + name, including `Test [row]`) |
| `--ff` / `--failed-first` | Run last-failed tests first, then the rest |
| `--mode record\|replay\|record-on-miss` | VCR cassettes under `.snapapi/cassettes/` |
| `--record-on-miss` | With `--mode replay`, hit the network and save when a cassette is missing |
| `--vcr-match query,body,accept,authorization` | Cassette identity fields (default: query, content-type, accept, body) |
| `--contract-strict` | Fail when an OpenAPI path/method/schema is missing (default: skip/warn) |
| `--reruns N` | Re-run failed *tests* up to N times (distinct from `EXPECT RETRY`) |
| `--listener PATH[:Class]` | Python listener called after each test / suite (repeatable) |
| `--plugin PATH[:name]` | Extra Python extension (repeatable). Prefer `extensions/` or `snapapi.yaml` |
| `--on-fail curl` / `--on-fail har:dir` | Emit a redacted curl or HAR on failure |
| `--safe-url` | Block private/metadata hosts |
| `--proxy URL` | HTTP/HTTPS proxy |
| `--insecure` | Skip TLS certificate verification |
| `--cert PATH` | Client certificate |
| `--cacert PATH` | CA bundle used to verify TLS |
| `snapapi lint PATH` | Parse/validate without HTTP |
| `snapapi fmt PATH` | Format `.sapi` files |
| `snapapi openapi spec.yaml` | Generate GET/POST/PUT/PATCH/DELETE smoke tests |
| `snapapi history [--failed] [--since 7d]` | Print `.snapapi/history.jsonl` |
| `snapapi mock mock.json [--port 0]` | Serve routes from a JSON mock file (prints the URL) |
| `snapapi watch PATH [--interval 0.5]` | Re-run when `.sapi` files change (poll; optional `watchdog` extra) |

The process exits `0` when every test passed, `1` when a test failed, and `2` on parse or usage errors.

## DSL

Recommended form (after you have a first PASS — see above). `https://api.example.com` is a **placeholder**, not a live host:

```
SUITE: Book Store
DESC: Validates the user API
TIMEOUT: 10
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
OPTIONS: {"TIMEOUT": 10}
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

- Suite: `SUITE`, `DESC`, `URL`, `TIMEOUT`, `FOLLOW-REDIRECTS`, `OPTIONS`, `IMPORT`, `SUITE-SETUP`, `SET`, `CALL`
- Test: `TEST`, `TAG`, `SETUP`, `TEARDOWN`, `DEPENDS`, `SKIP`, `ONLY`, `QUARANTINE`, `EXAMPLES`, `SET`, `CALL`
- Helper: `HELPER` (named procedure for `SUITE-SETUP` / `SETUP` / `TEARDOWN`; not a test case)
- Request: `REQUEST`, `GET`/`POST`/`PUT`/`PATCH`/`DELETE`/`HEAD`, `BODY`/`DATA`, `FILE`, `GRAPHQL`, `HEADER`/`HEADERS`, `QUERY`, `PARAM`, `AUTH`, `EXPECT`, `SAVE`, `WAIT`, `SET`, `CALL`

HTTP `OPTIONS` is written as `REQUEST: OPTIONS /path` so it does not collide with suite-level `OPTIONS: {...}` JSON. `HEAD: /x` is a request alias like `GET:`.

`SETUP` / `TEARDOWN` name a `HELPER` or `TEST`. Prefer `HELPER:` for procedures that should not appear as cases. A `TEST` named only as setup is still treated as a helper (legacy).

`DEPENDS: Create User` (comma-separated for several names) keeps both tests as primaries. SnapAPI reorders so named tests run first; unrelated tests keep file order. If a named test failed, skipped, or was not selected (for example `-k`), the dependent test is skipped with that reason. Cycles and unknown names are parse errors. `DEPENDS` cannot target a `HELPER`. This is not `SETUP:` — setup helpers always run first and fail the dependent test when they fail.

`IMPORT: other.sapi` pulls tests from another file (paths are relative to the current file).

`SET: orderId ${uuid()}` assigns an interpolated value (including helpers) without an HTTP call. It may appear at suite, test, or step level.

`WAIT: json $.status == "ready" TIMEOUT 10s BACKOFF 0.5s` reissues the current request until the check passes or the timeout expires. `EXPECT: json $.status == "ready" RETRY 20 BACKOFF 0.5s` also retries when the HTTP status is already 200.

`AUTH: bearer ${TOKEN}` sets `Authorization: Bearer ${TOKEN}`. Explicit `HEADER` lines still work.

`AUTH: oauth2 grant=client_credentials token_url=... client_id=...` and `grant=password username=... password=...` fetch a token (cached). If the token response includes `refresh_token`, a 401 retries once after refresh.

`AUTH: oauth2 grant=authorization_code token_url=... auth_url=... client_id=... redirect_uri=... code=${AUTH_CODE} pkce=true` exchanges an authorization code. SnapAPI does not open a browser; supply `${AUTH_CODE}` from the environment. With `pkce=true` the token request includes S256 `code_verifier` / `code_challenge` fields.

`AUTH: digest user:pass` uses `requests` HTTP Digest Auth.

`QUERY: page=2&limit=10` and `PARAM: page 2` attach query parameters to the current request (they merge with any query string already in the path).

`OPTIONS: {"OPENAPI": "spec.yaml"}` validates JSON responses (and request bodies/required params) against the matching path+method schema when present. Missing path/schema is skipped by default. Strict mode fails instead:

```
OPTIONS: {"OPENAPI": "spec.yaml", "OPENAPI-STRICT": true}
EXPECT: openapi ./spec.yaml strict
```

CLI `--contract-strict` is the same switch. Partial path match (`/users/{id}` vs `/users/1`) is allowed.

### Checks

Check types are case-insensitive. Preferred:

```
EXPECT: status == 200
EXPECT: status != 500
EXPECT: status == 200 RETRY 5 ON 5xx BACKOFF 1s
EXPECT: body contains userId
EXPECT: body not contains stack
EXPECT: json $.email matches ^.+@example\\.com$
EXPECT: json $.email not matches @tempmail
EXPECT: json $.items length == 3
EXPECT: json $.items empty
EXPECT: json $.items not empty
EXPECT: json $.id type string
EXPECT: json $.status in ["open","pending"]
EXPECT: json $.email starts-with "ada@"
EXPECT: json $.email ends-with "@example.com"
EXPECT: json $.tags contains-all ["a","b"]
EXPECT: json $.tags contains-only ["a","b"]
EXPECT: json $.tags contains-any ["admin","owner"]
EXPECT: json $.ids unique
EXPECT: json $.count between 1 10
EXPECT: json $.score close-to 0.33 delta 0.01
EXPECT: json $.items[*].id contains 3
EXPECT: json $.items[?(@.status=="open")].id contains 3
EXPECT: json $.items each $.status == "active"
EXPECT: json $.ok == true BECAUSE "login should succeed"
EXPECT: status == 400 OR status == 401
EXPECT: json $.success == false AND body contains error
EXPECT: (status == 400 OR status == 401) AND json $.success == false
EXPECT: schema ./schemas/user.json
EXPECT: duration < 200ms
EXPECT: header Content-Type contains json
EXPECT: header Content-Type starts-with application
EXPECT: body empty
EXPECT: body starts-with {"ok"
EXPECT: openapi ./openapi.yaml
EXPECT: openapi ./openapi.yaml strict
EXPECT: xpath //Order/@id == "1"
```

Also accepted:

```
EXPECT: STATUS 200
EXPECT: CONTAINS userId
EXPECT: JSON $.data.email == "jane@example.com"
EXPECT: HEADER Content-Type CONTAINS json
```

JSONPath is a small subset: `$.a.b`, `$.items.0.id`, `$.items[0].id`, `$.items[*].id`, and equality filters `$.items[?(@.status=="open")]` / `$.items[?(@.id==1)]`.

`AND` / `OR` combine checks on one line (`AND` binds tighter than `OR`; parentheses group). Quote a value if it contains those words. Multiple `EXPECT` lines on the same request all run; if more than one fails, every failure is reported together. `BECAUSE "reason"` on an EXPECT line is prepended to that check's failure message.

XPath uses stdlib `xml.etree` (descendant tags and `/@attr`). Axes, namespaces, and functions are not implemented.

### Variables

`${NAME}` is expanded in URLs, paths, headers, data, and expect values.

Lookup order: process environment, then `--env` / auto-discovered suite env file, then `SET` / `SAVE` / `CALL` values.

Built-in functions: `${uuid()}`, `${now()}`, `${random.int(min,max)}`. Custom functions use `CALL: signature = crypto.generate_signature(${PAYLOAD}, ${SECRET})` with modules in `extensions/`. They take explicit arguments and cannot skip steps or make HTTP.

## Sample suite

`tests/recommended.snaptest` shows the current DSL against a local mock server (pytest injects `BASE_URL`). `tests/test_suite.snaptest` is a classic-syntax example against [reqres.in](https://reqres.in) and needs network access. Automated tests in `tests/test_*.py` use a local mock HTTP server and do not call reqres. CI replays `tests/fixtures/offline.snaptest` from a checked-in cassette.

HTML reports include redacted request/response bodies. VCR cassette keys include method, path, and (by default) sorted query string, `Content-Type`/`Accept`, and body. `OPTIONS: {"VCR-MATCH": ["query","body","accept","authorization"]}` or `--vcr-match authorization,query` replaces that default. Replay restores `Set-Cookie` onto the session.

`snapapi mock tests/fixtures/mock.json --port 0` serves JSON routes. Routes may use path templates (`/users/{id}`), optional `match.query` / `match.body` subsets, and `delay_ms`. Exact paths win over templates. There is no language server, gRPC, or WebSocket support. Python plugins are CLI-only (not the playground).

### pytest plugin

Install with `pip install -e ".[dev]"`. Then:

```python
def test_suite(snapapi_run):
    result = snapapi_run("tests/foo.sapi")
    assert result.ok

@pytest.mark.snapapi("tests/foo.sapi")
def test_marked(snapapi_run, request):
    snapapi_run(request.node.get_closest_marker("snapapi").args[0])
```

`snapapi_run(path, **engine_kwargs)` returns `SuiteResult` and fails the pytest case when the suite is not ok. Pass `plugins={"hmac": hmac}` if you are not using `extensions/`.

## Project layout

```
snapapi/
├── snapapi/              # Python package
│   ├── parser.py         # .sapi DSL parser
│   ├── engine.py         # runner, checks, setup/teardown
│   ├── api_client.py     # requests wrapper
│   └── cli.py            # snapapi command
├── tests/                # pytest + sample .sapi / .snaptest suites
├── examples/             # hello GET, users POST/SAVE/AUTH/HELPER, plugins
├── docs/                 # User guide + in-browser playground
├── snapapi-language/     # VS Code grammar / run command
├── pyproject.toml
└── README.md
```

## Running the tests

```bash
pytest
```

## VS Code

The `snapapi-language` extension is a language pack for `.sapi` files: syntax highlighting, snippets, completions, lightweight diagnostics (unknown keywords, unknown SETUP names, `HEAD:` / `REQUEST: OPTIONS`), and **SnapAPI: Run current file** / **Run test at cursor**. See [snapapi-language/README.md](snapapi-language/README.md). There is no separate language-server process.

## License

MIT — see [LICENSE](LICENSE).
