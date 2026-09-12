from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from colorama import Fore, Style, init

from snapapi.api_client import APIClient, open_files
from snapapi.cassette import cassette_key, load_cassettes, save_cassette
from snapapi.exceptions import JsonPathError, SnapAPIError
from snapapi import jsonpath
from snapapi.redact import redact_body, redact_headers
from snapapi.safety import assert_public_url
from snapapi.variables import interpolate

init(autoreset=True)

RETRY_BACKOFF_SECONDS = 0.05
REQUEST_COL_WIDTH = 32


def format_duration(ms):
    if ms is None:
        return "0ms"
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.2f}s"


def _should_color(stream):
    if os.environ.get("NO_COLOR"):
        return False
    target = sys.stdout if stream is None else stream
    if hasattr(target, "isatty") and target.isatty():
        return True
    return stream is None and bool(os.environ.get("FORCE_COLOR"))


def _as_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def helper_names(suite):
    names = set()
    if suite.get("setup"):
        names.add(suite["setup"])
    if suite.get("teardown"):
        names.add(suite["teardown"])
    for test in suite.get("tests") or []:
        if test.get("setup"):
            names.add(test["setup"])
        if test.get("teardown"):
            names.add(test["teardown"])
    return names


class RequestResult:
    def __init__(
        self,
        method,
        url,
        status_code=None,
        duration_ms=0,
        error=None,
        request_headers=None,
        request_body=None,
        response_headers=None,
        response_body=None,
    ):
        self.method = method
        self.url = url
        self.status_code = status_code
        self.duration_ms = duration_ms
        self.error = error
        self.request_headers = request_headers or {}
        self.request_body = request_body
        self.response_headers = response_headers or {}
        self.response_body = response_body

    def to_dict(self):
        return {
            "method": self.method,
            "url": self.url,
            "status_code": self.status_code,
            "duration_ms": round(self.duration_ms, 3),
            "error": self.error,
            "request_headers": redact_headers(self.request_headers),
            "request_body": redact_body(self.request_body),
            "response_headers": redact_headers(self.response_headers),
            "response_body": redact_body(self.response_body),
        }


class TestResult:
    def __init__(self, name, tags=None, status="passed", duration_ms=0, error=None, requests=None):
        self.name = name
        self.tags = list(tags or [])
        self.status = status
        self.duration_ms = duration_ms
        self.error = error
        self.requests = list(requests or [])

    def to_dict(self):
        return {
            "name": self.name,
            "tags": self.tags,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 3),
            "error": self.error,
            "requests": [item.to_dict() if hasattr(item, "to_dict") else item for item in self.requests],
        }


class SuiteResult:
    def __init__(self, name, source=None, tests=None, duration_ms=0):
        self.name = name
        self.source = source
        self.tests = list(tests or [])
        self.duration_ms = duration_ms

    @property
    def ok(self):
        return all(test.status != "failed" for test in self.tests)

    @property
    def passed(self):
        return sum(1 for test in self.tests if test.status == "passed")

    @property
    def failed(self):
        return sum(1 for test in self.tests if test.status == "failed")

    @property
    def skipped(self):
        return sum(1 for test in self.tests if test.status == "skipped")

    @property
    def total(self):
        return len(self.tests)

    def to_dict(self):
        return {
            "name": self.name,
            "source": self.source,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "duration_ms": round(self.duration_ms, 3),
            "tests": [test.to_dict() for test in self.tests],
        }


class Engine:
    def __init__(
        self,
        suite,
        variables=None,
        timeout=None,
        tags=None,
        names=None,
        stop_on_failure=None,
        retry_backoff=None,
        stream=None,
        grep=None,
        include_skipped=False,
        include_quarantine=False,
        workers=1,
        dump_on_fail=True,
        on_fail=None,
        mode=None,
        cassette_dir=None,
        safe_url=False,
        isolate_variables=None,
    ):
        self.suite = suite
        self.variables = dict(variables or {})
        options = suite.get("options") or {}
        if timeout is not None:
            self.timeout = float(timeout)
        elif options.get("TIMEOUT") is not None:
            self.timeout = float(options["TIMEOUT"])
        else:
            self.timeout = 30.0
        self.tags = list(tags or [])
        self.names = list(names or [])
        self.grep = grep
        self.include_skipped = include_skipped
        self.include_quarantine = include_quarantine
        self.workers = max(1, int(workers or 1))
        self.dump_on_fail = dump_on_fail
        self.on_fail = list(on_fail or [])
        self.mode = (mode or options.get("MODE") or "live").lower()
        self.cassette_dir = cassette_dir or options.get("CASSETTE_DIR") or ".snapapi/cassettes"
        self.safe_url = safe_url or _as_bool(options.get("SAFE-URL"), False)
        if isolate_variables is None:
            isolate_variables = self.workers > 1
        self.isolate_variables = isolate_variables
        if stop_on_failure is not None:
            self.stop_on_failure = bool(stop_on_failure)
        else:
            self.stop_on_failure = _as_bool(options.get("STOP-ON-FAILURE"), True)
        self.retry_backoff = RETRY_BACKOFF_SECONDS if retry_backoff is None else retry_backoff
        self.stream = stream
        self._color = _should_color(stream)
        self.failures = []
        self.success = []
        self._stack = []
        self._helpers = helper_names(suite)
        self._print_lock = threading.Lock()
        self._oauth_cache = {}
        self._cassettes = load_cassettes(self.cassette_dir) if self.mode == "replay" else {}
        self._last_duration = 0
        self._client = None

    def run(self):
        started = time.perf_counter()
        name = self.suite.get("name") or "suite"
        self._print(f"{self._paint('SnapAPI', Fore.CYAN, Style.BRIGHT)}  {name}")
        if self.suite.get("description"):
            self._print(self._paint(self.suite["description"], Style.DIM))

        results = []
        if self.suite.get("setup"):
            setup_result = self._run_test(self.suite["setup"], role="setup")
            if setup_result.status == "failed":
                results.append(
                    TestResult(
                        name=f"SUITE SETUP ({self.suite['setup']})",
                        status="failed",
                        error=setup_result.error,
                        requests=setup_result.requests,
                        duration_ms=setup_result.duration_ms,
                    )
                )
                suite_result = SuiteResult(
                    name=self.suite.get("name"),
                    source=self.suite.get("source"),
                    tests=results,
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
                self.print_summary(suite_result)
                return suite_result

        primaries = self._primary_tests()
        if self.workers > 1 and not self.stop_on_failure:
            results.extend(self._run_parallel(primaries))
        else:
            for test in primaries:
                skip_reason = self._skip_reason(test)
                if skip_reason:
                    results.append(
                        TestResult(name=test["name"], tags=test.get("tags") or [], status="skipped", error=skip_reason)
                    )
                    continue
                if not self._matches_filter(test):
                    results.append(TestResult(name=test["name"], tags=test.get("tags") or [], status="skipped"))
                    continue
                results.extend(self._run_examples(test))
                if any(item.status == "failed" for item in results) and self.stop_on_failure:
                    self._print(
                        self._paint(
                            f"  stopped after {test['name']!r}  (set STOP-ON-FAILURE: false to continue)",
                            Fore.RED,
                        )
                    )
                    break

        if self.suite.get("teardown"):
            self._run_test(self.suite["teardown"], role="teardown")

        suite_result = SuiteResult(
            name=self.suite.get("name"),
            source=self.suite.get("source"),
            tests=results,
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        self.print_summary(suite_result)
        return suite_result

    def print_summary(self, suite_result=None):
        if suite_result is None:
            failed_tests = len(self.failures)
            passed_tests = len(self.success)
            skipped_tests = 0
            duration = None
            failed_rows = list(self.failures)
        else:
            failed_tests = suite_result.failed
            passed_tests = suite_result.passed
            skipped_tests = suite_result.skipped
            duration = suite_result.duration_ms
            failed_rows = [(item.name, item.error) for item in suite_result.tests if item.status == "failed"]
        parts = [
            self._paint(f"{passed_tests} passed", Fore.GREEN if passed_tests else Style.DIM),
            self._paint(f"{failed_tests} failed", Fore.RED if failed_tests else Style.DIM),
        ]
        if skipped_tests:
            parts.append(self._paint(f"{skipped_tests} skipped", Fore.YELLOW))
        if duration is not None:
            parts.append(self._paint(format_duration(duration), Style.DIM))
        self._print()
        self._print(f"  {'  '.join(parts)}")
        if failed_rows:
            self._print(self._paint("  Failed:", Fore.RED))
            for test_name, error in failed_rows:
                self._print(
                    f"    {self._paint('- ' + test_name + ':', Fore.RED)} {self._paint(error or 'failed', Fore.RED)}"
                )
        self._print()

    def _run_examples(self, test):
        rows = test.get("examples") or [None]
        results = []
        for row in rows:
            snapshot = dict(self.variables)
            if row:
                self.variables.update(row)
            result = self._run_test(test["name"], role="test")
            if row:
                label = next((value for value in row.values() if value), "row")
                result.name = f"{test['name']} [{label}]"
            results.append(result)
            if self.isolate_variables:
                self.variables = snapshot
        return results

    def _run_parallel(self, primaries):
        jobs = []
        for test in primaries:
            skip_reason = self._skip_reason(test)
            if skip_reason:
                jobs.append(TestResult(name=test["name"], tags=test.get("tags") or [], status="skipped", error=skip_reason))
                continue
            if not self._matches_filter(test):
                jobs.append(TestResult(name=test["name"], tags=test.get("tags") or [], status="skipped"))
                continue
            jobs.append(test)
        results = []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {}
            for item in jobs:
                if isinstance(item, TestResult):
                    results.append(item)
                    continue
                futures[pool.submit(self._run_isolated, item)] = item
            for future in as_completed(futures):
                result, output = future.result()
                if output:
                    self._print(output.rstrip("\n"))
                results.append(result)
        order = {test["name"]: index for index, test in enumerate(primaries)}
        results.sort(key=lambda item: order.get(item.name.split(" [")[0], 0))
        return results

    def _run_isolated(self, test):
        import io

        child = Engine(
            self.suite,
            variables=dict(self.variables),
            timeout=self.timeout,
            tags=self.tags,
            names=self.names,
            stop_on_failure=False,
            retry_backoff=self.retry_backoff,
            stream=io.StringIO(),
            grep=self.grep,
            include_skipped=self.include_skipped,
            include_quarantine=self.include_quarantine,
            workers=1,
            dump_on_fail=self.dump_on_fail,
            on_fail=self.on_fail,
            mode=self.mode,
            cassette_dir=self.cassette_dir,
            safe_url=self.safe_url,
            isolate_variables=True,
        )
        child._oauth_cache = self._oauth_cache
        child._cassettes = self._cassettes
        child._helpers = self._helpers
        results = child._run_examples(test)
        output = child.stream.getvalue() if child.stream else ""
        return results[0] if len(results) == 1 else results[0], output

    def _run_test(self, name, role="test"):
        test = self.suite["test_map"][name]
        started = time.perf_counter()
        collected = []
        error = None

        if name in self._stack:
            cycle = " -> ".join(self._stack + [name])
            raise SnapAPIError(f"SETUP/TEARDOWN cycle detected: {cycle}")

        self._stack.append(name)
        try:
            self._announce(test, role)
            if test.get("setup"):
                setup_result = self._run_test(test["setup"], role="setup")
                collected.extend(setup_result.requests)
                if setup_result.status == "failed":
                    error = f"Setup '{test['setup']}' failed"
                    if setup_result.error:
                        error += f": {setup_result.error}"

            if error is None:
                ok, body_error, body_requests = self._run_body(test)
                collected.extend(body_requests)
                if not ok:
                    error = body_error

            if test.get("teardown"):
                teardown_result = self._run_test(test["teardown"], role="teardown")
                collected.extend(teardown_result.requests)
                if teardown_result.status == "failed" and error is None:
                    error = f"Teardown '{test['teardown']}' failed"
                    if teardown_result.error:
                        error += f": {teardown_result.error}"

            duration_ms = (time.perf_counter() - started) * 1000
            if error:
                if role == "test":
                    self._print_outcome(False, duration_ms)
                    self.failures.append((name, error))
                return TestResult(
                    name=name,
                    tags=test.get("tags") or [],
                    status="failed",
                    duration_ms=duration_ms,
                    error=error,
                    requests=collected,
                )

            if role == "test":
                self._print_outcome(True, duration_ms)
                self.success.append(name)
            return TestResult(
                name=name,
                tags=test.get("tags") or [],
                status="passed",
                duration_ms=duration_ms,
                requests=collected,
            )
        finally:
            self._stack.pop()

    def _run_body(self, test):
        base_url = interpolate(test.get("base_url") or self.suite.get("base_url") or "", self.variables)
        follow = test.get("follow_redirects")
        if follow is None:
            follow = self.suite.get("follow_redirects")
        if follow is None:
            follow = True
        client = APIClient(base_url, timeout=self.timeout, follow_redirects=follow)
        self._client = client
        oauth = test.get("oauth2") or self.suite.get("oauth2")
        if oauth:
            token = self._oauth_token(oauth)
            test.setdefault("headers", {})
            test["headers"]["Authorization"] = f"Bearer {token}"
        requests_log = []
        for step in test.get("steps") or []:
            ok, error, recorded = self._execute_step(client, step, test)
            if recorded:
                requests_log.append(recorded)
            if not ok:
                return False, error, requests_log
        return True, None, requests_log

    def _execute_step(self, client, step, test):
        method = step["action"]
        handles = []
        try:
            endpoint = interpolate(step["endpoint"], self.variables)
            query = interpolate(step.get("query") or {}, self.variables)
            endpoint = merge_query(endpoint, query)
            data = interpolate(step.get("data"), self.variables) if step.get("data") is not None else None
            raw_body = interpolate(step.get("raw_body"), self.variables) if step.get("raw_body") is not None else None
            headers = {}
            headers.update(test.get("headers") or {})
            headers.update(step.get("headers") or {})
            headers = interpolate(headers, self.variables) if headers else {}
            if step.get("oauth2"):
                headers["Authorization"] = f"Bearer {self._oauth_token(step['oauth2'])}"
            files, handles = open_files(step.get("files"), self._base_dir())
        except SnapAPIError as exc:
            self._print_error(str(exc), under_request=False)
            return False, str(exc), None

        url_preview = client._build_url(endpoint)
        if self.safe_url:
            try:
                assert_public_url(url_preview)
            except SnapAPIError as exc:
                self._print_error(str(exc), under_request=False)
                return False, str(exc), None

        checks = step.get("checks") or []
        attempts = max((check.get("retry") or 1) for check in checks) if checks else 1
        retry_on = next((check.get("retry_on") for check in checks if check.get("retry_on")), None)
        retry_backoff = next((check.get("retry_backoff") for check in checks if check.get("retry_backoff")), None)
        last_error = None
        recorded = None
        body_type = step.get("body_type") or "json"
        follow = step.get("follow_redirects")

        try:
            for attempt in range(1, attempts + 1):
                response = None
                started = time.perf_counter()
                try:
                    response = self._dispatch(
                        client,
                        method,
                        endpoint,
                        data=data,
                        raw_body=raw_body,
                        headers=headers,
                        files=files or None,
                        body_type=body_type,
                        content_type=step.get("content_type"),
                        follow_redirects=follow,
                    )
                    duration_ms = (time.perf_counter() - started) * 1000
                    self._last_duration = duration_ms
                    url = getattr(response, "url", endpoint)
                    recorded = RequestResult(
                        method,
                        url,
                        status_code=response.status_code,
                        duration_ms=duration_ms,
                        request_headers=headers,
                        request_body=raw_body if raw_body is not None else data,
                        response_headers=dict(getattr(response, "headers", {}) or {}),
                        response_body=_response_text(response),
                    )
                    self._print_request(method, endpoint, response.status_code, duration_ms)
                    for check in checks:
                        self._execute_check(check, response, duration_ms)
                    for save in step.get("saves") or []:
                        self._save_value(save, response)
                    return True, None, recorded
                except AssertionError as exc:
                    duration_ms = (time.perf_counter() - started) * 1000
                    last_error = str(exc).strip()
                    recorded = RequestResult(
                        method,
                        endpoint,
                        status_code=None if response is None else response.status_code,
                        duration_ms=duration_ms,
                        error=last_error,
                        request_headers=headers,
                        request_body=raw_body if raw_body is not None else data,
                        response_headers=dict(getattr(response, "headers", {}) or {}) if response is not None else {},
                        response_body=_response_text(response) if response is not None else None,
                    )
                    retryable = _is_retryable(retry_on, response, network=False)
                except (requests.RequestException, ValueError, SnapAPIError) as exc:
                    duration_ms = (time.perf_counter() - started) * 1000
                    last_error = str(exc)
                    recorded = RequestResult(method, endpoint, duration_ms=duration_ms, error=last_error)
                    self._print_request(method, endpoint, None, duration_ms)
                    retryable = _is_retryable(retry_on, None, network=True)

                if attempt < attempts and retryable:
                    delay = retry_backoff if retry_backoff is not None else self.retry_backoff * attempt
                    time.sleep(delay)
                    continue

                if last_error:
                    self._print_error(last_error)
                    if self.dump_on_fail and recorded:
                        self._print_dump(recorded)
                    self._emit_on_fail(test, recorded)
                return False, last_error, recorded
        finally:
            for handle in handles:
                handle.close()

    def _dispatch(self, client, method, endpoint, **kwargs):
        data = kwargs.get("data")
        raw_body = kwargs.get("raw_body")
        headers = kwargs.get("headers")
        files = kwargs.get("files")
        body_type = kwargs.get("body_type")
        content_type = kwargs.get("content_type")
        follow = kwargs.get("follow_redirects")
        url = client._build_url(endpoint)
        if self.mode == "replay":
            key = cassette_key(method, url, raw_body if raw_body is not None else data)
            record = self._cassettes.get(key)
            if not record:
                raise SnapAPIError(f"No cassette for {method} {url}")
            return _fake_response(record)
        response = client.request(
            method,
            endpoint,
            json=data if body_type in (None, "json", "graphql") else None,
            data=data if body_type == "form" else None,
            raw=raw_body,
            headers=headers,
            files=files,
            body_type=body_type,
            content_type=content_type,
            follow_redirects=follow,
        )
        if self.mode == "record":
            key = cassette_key(method, url, raw_body if raw_body is not None else data)
            save_cassette(
                self.cassette_dir,
                key,
                {
                    "method": method,
                    "url": url,
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                    "body": response.text,
                },
            )
        return response

    def _execute_check(self, check, response, duration_ms=0):
        check_type = check["type"]
        if check_type == "STATUS":
            expected = int(interpolate(str(check["value"]), self.variables))
            actual = response.status_code
            operator = check.get("operator") or "=="
            if operator == "!=":
                assert actual != expected, f"Status code expected not {expected}, got {actual}"
            else:
                assert actual == expected, f"Status code expected {expected}, got {actual}"
        elif check_type == "CONTAINS":
            expected = interpolate(str(check["value"]), self.variables)
            present = expected in response.text
            if check.get("negated"):
                assert not present, f"Response unexpectedly contains {expected}"
            else:
                assert present, f"Response does not contain {expected}"
        elif check_type == "JSON":
            self._check_json(check, response)
        elif check_type == "HEADER":
            self._check_header(check, response)
        elif check_type == "SCHEMA":
            self._check_schema(check, response)
        elif check_type == "DURATION":
            self._compare(duration_ms, check.get("operator") or "<", float(check["value"]), "duration")
        else:
            raise AssertionError(f"Unknown check type {check_type}")

    def _check_json(self, check, response):
        path = interpolate(check["path"], self.variables)
        try:
            body = response.json()
        except ValueError as exc:
            raise AssertionError(f"Response is not JSON: {exc}") from exc
        try:
            actual = jsonpath.extract(body, path)
        except JsonPathError as exc:
            raise AssertionError(str(exc)) from exc
        expected = interpolate(check["value"], self.variables)
        operator = check["operator"]
        if operator.startswith("length "):
            cmp_op = operator.split(" ", 1)[1]
            self._compare(len(actual), cmp_op, expected, f"JSON {path} length")
            return
        if operator == "==":
            assert actual == expected, f"JSON {path} expected {expected!r}, got {actual!r}"
        elif operator == "!=":
            assert actual != expected, f"JSON {path} expected not {expected!r}, got {actual!r}"
        elif operator.upper() == "CONTAINS":
            if isinstance(actual, (list, tuple, set)):
                assert expected in actual, f"JSON {path} value {actual!r} does not contain {expected!r}"
            else:
                assert str(expected) in str(actual), f"JSON {path} value {actual!r} does not contain {expected!r}"
        elif operator.upper() == "MATCHES":
            assert re.search(str(expected), str(actual)), f"JSON {path} value {actual!r} does not match {expected!r}"
        elif operator in (">", ">=", "<", "<="):
            self._compare(actual, operator, expected, f"JSON {path}")
        else:
            raise AssertionError(f"Unknown JSON operator {operator}")

    def _check_header(self, check, response):
        name = interpolate(check["name"], self.variables)
        expected = interpolate(str(check["value"]), self.variables)
        actual = response.headers.get(name)
        if actual is None:
            raise AssertionError(f"Header {name} missing")
        operator = check["operator"]
        if operator == "==":
            assert actual == expected, f"Header {name} expected {expected!r}, got {actual!r}"
        elif operator == "!=":
            assert actual != expected, f"Header {name} expected not {expected!r}, got {actual!r}"
        elif operator.upper() == "CONTAINS":
            assert expected in actual, f"Header {name} value {actual!r} does not contain {expected!r}"
        else:
            raise AssertionError(f"Unknown HEADER operator {operator}")

    def _check_schema(self, check, response):
        try:
            import jsonschema
        except ImportError as exc:
            raise AssertionError("jsonschema is required for EXPECT schema") from exc
        try:
            instance = response.json()
        except ValueError as exc:
            raise AssertionError(f"Response is not JSON: {exc}") from exc
        if check.get("mode") == "inline":
            schema = check.get("schema")
        else:
            path = Path(interpolate(check["path"], self.variables))
            if not path.is_absolute():
                path = Path(self._base_dir()) / path
            try:
                schema = json.loads(path.read_text(encoding="utf-8"))
            except OSError as exc:
                raise AssertionError(f"Schema file not found: {path}") from exc
        try:
            jsonschema.validate(instance, schema)
        except jsonschema.ValidationError as exc:
            raise AssertionError(f"Schema validation failed: {exc.message}") from exc

    def _compare(self, actual, operator, expected, label):
        try:
            left = float(actual)
            right = float(expected)
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"{label} cannot compare {actual!r} and {expected!r}") from exc
        ok = {
            ">": left > right,
            ">=": left >= right,
            "<": left < right,
            "<=": left <= right,
            "==": left == right,
            "!=": left != right,
        }.get(operator)
        if ok is None:
            raise AssertionError(f"Unknown operator {operator}")
        assert ok, f"{label} expected {operator} {right}, got {left}"

    def _save_value(self, save, response):
        source = (save.get("source") or "json").lower()
        selector = interpolate(save["path"], self.variables)
        if source == "header":
            value = response.headers.get(selector)
            if value is None:
                raise AssertionError(f"Cannot SAVE header {selector}: missing")
        elif source == "cookie":
            value = response.cookies.get(selector)
            if value is None and hasattr(self._client, "session"):
                value = self._client.session.cookies.get(selector)
            if value is None:
                raise AssertionError(f"Cannot SAVE cookie {selector}: missing")
        else:
            try:
                body = response.json()
            except ValueError as exc:
                raise AssertionError(f"Cannot SAVE from non-JSON response: {exc}") from exc
            try:
                value = jsonpath.extract(body, selector)
            except JsonPathError as exc:
                raise AssertionError(str(exc)) from exc
        self.variables[save["name"]] = value
        indent = self._spaces(1)
        self._print(self._paint(f"{indent}saved {save['name']}={value}", Style.DIM))

    def _oauth_token(self, spec):
        token_url = interpolate(spec.get("token_url"), self.variables)
        client_id = interpolate(spec.get("client_id"), self.variables)
        secret = interpolate(spec.get("client_secret") or "", self.variables)
        key = (token_url, client_id)
        if key in self._oauth_cache:
            return self._oauth_cache[key]
        client = APIClient(timeout=self.timeout)
        response = client.request(
            "POST",
            token_url,
            data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret},
            body_type="form",
        )
        if response.status_code >= 400:
            raise SnapAPIError(f"OAuth2 token request failed: {response.status_code}")
        try:
            token = response.json().get("access_token")
        except ValueError as exc:
            raise SnapAPIError("OAuth2 token response is not JSON") from exc
        if not token:
            raise SnapAPIError("OAuth2 token response missing access_token")
        self._oauth_cache[key] = token
        return token

    def _primary_tests(self):
        tests = self.suite.get("tests") or []
        known = self.suite.get("test_map") or {}
        if self.names:
            missing = [name for name in self.names if name not in known]
            if missing:
                raise SnapAPIError("Unknown test name: " + ", ".join(missing))
            selected = set(self.names)
            return [test for test in tests if test["name"] in selected]
        primaries = [test for test in tests if test["name"] not in self._helpers]
        only = [test for test in primaries if test.get("only")]
        return only or primaries

    def _skip_reason(self, test):
        if test.get("skip") and not self.include_skipped:
            return test.get("skip") or "skipped"
        if test.get("quarantine") and not self.include_quarantine:
            return test.get("quarantine") or "quarantine"
        return None

    def _matches_filter(self, test):
        if self.tags:
            test_tags = set(test.get("tags") or [])
            if not all(tag in test_tags for tag in self.tags):
                return False
        if self.grep:
            blob = f"{test.get('name') or ''} {test.get('description') or ''}"
            if not re.search(self.grep, blob, re.IGNORECASE):
                return False
        return True

    def _matches_tags(self, test):
        return self._matches_filter(test)

    def _announce(self, test, role):
        if role == "test" and len(self._stack) == 1:
            self._print()
        indent = self._spaces(0)
        if role == "test":
            tags = test.get("tags") or []
            tag_part = self._paint(f"  [{', '.join(tags)}]", Style.DIM) if tags else ""
            self._print(f"{indent}{test['name']}{tag_part}")
            return
        self._print(self._paint(f"{indent}{role} {test['name']}", Style.DIM))

    def _print_request(self, method, endpoint, status_code, duration_ms):
        indent = self._spaces(1)
        request = f"{method} {endpoint}"
        if len(request) < REQUEST_COL_WIDTH:
            request = f"{request:<{REQUEST_COL_WIDTH}}"
        request = self._paint(request, Style.DIM)
        if status_code is None:
            status = self._paint("ERR", Fore.RED)
        elif 200 <= int(status_code) < 300:
            status = self._paint(str(status_code), Fore.GREEN)
        else:
            status = self._paint(str(status_code), Fore.RED)
        duration = self._paint(format_duration(duration_ms), Style.DIM)
        self._print(f"{indent}{request}  {status}  {duration}")

    def _print_error(self, error, under_request=True):
        extra = 2 if under_request else 1
        self._print(f"{self._spaces(extra)}{self._paint(error, Fore.RED)}")

    def _print_dump(self, recorded):
        indent = self._spaces(2)
        self._print(self._paint(f"{indent}request:", Style.DIM))
        self._print(self._paint(f"{indent}  {recorded.method} {recorded.url}", Style.DIM))
        for key, value in redact_headers(recorded.request_headers).items():
            self._print(self._paint(f"{indent}  {key}: {value}", Style.DIM))
        if recorded.request_body is not None:
            self._print(self._paint(f"{indent}  {redact_body(recorded.request_body)}", Style.DIM))
        self._print(self._paint(f"{indent}response:", Style.DIM))
        if recorded.status_code is not None:
            self._print(self._paint(f"{indent}  {recorded.status_code}", Style.DIM))
        for key, value in redact_headers(recorded.response_headers).items():
            self._print(self._paint(f"{indent}  {key}: {value}", Style.DIM))
        if recorded.response_body:
            self._print(self._paint(f"{indent}  {redact_body(recorded.response_body)}", Style.DIM))

    def _emit_on_fail(self, test, recorded):
        for spec in self.on_fail:
            if spec == "curl" or spec.startswith("curl"):
                self._print(self._spaces(2) + self._paint(_as_curl(recorded), Style.DIM))
            elif spec.startswith("har:"):
                directory = spec.split(":", 1)[1]
                Path(directory).mkdir(parents=True, exist_ok=True)
                path = Path(directory) / f"{_safe_name(test['name'])}.har"
                path.write_text(json.dumps(_as_har(recorded), indent=2) + "\n", encoding="utf-8")

    def _print_outcome(self, passed, duration_ms):
        indent = self._spaces(1)
        label = (
            self._paint("PASS", Fore.GREEN, Style.BRIGHT)
            if passed
            else self._paint("FAIL", Fore.RED, Style.BRIGHT)
        )
        duration = self._paint(format_duration(duration_ms), Style.DIM)
        self._print(f"{indent}{label}  {duration}")

    def _spaces(self, extra=0):
        return "  " * (len(self._stack) + extra)

    def _paint(self, text, *styles):
        if not self._color or not styles:
            return text
        return "".join(styles) + text + Style.RESET_ALL

    def _print(self, message=""):
        with self._print_lock:
            print(message, file=self.stream)

    def _base_dir(self):
        source = self.suite.get("source")
        if source and source != "<string>":
            return str(Path(source).parent)
        return str(Path.cwd())


def merge_query(endpoint, params):
    """Merge PARAM/QUERY values into an endpoint, overriding same-named path query keys."""
    if not params:
        return endpoint
    parts = urlsplit(endpoint)
    merged = dict(parse_qsl(parts.query, keep_blank_values=True))
    for key, value in params.items():
        merged[str(key)] = "" if value is None else str(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(merged), parts.fragment))


def _response_text(response):
    if response is None:
        return None
    return getattr(response, "text", None)


def _is_retryable(retry_on, response, network=False):
    if not retry_on:
        return True
    if retry_on in ("5xx", "5XX"):
        if network:
            return True
        return response is not None and int(response.status_code) >= 500
    return True


def _fake_response(record):
    body = record.get("body") or ""
    headers = record.get("headers") or {}

    def _json():
        return json.loads(body) if body else {}

    return SimpleNamespace(
        status_code=record.get("status_code", 200),
        text=body,
        headers=headers,
        cookies={},
        url=record.get("url"),
        json=_json,
    )


def _as_curl(recorded):
    parts = ["curl", "-X", recorded.method, f"'{recorded.url}'"]
    for key, value in redact_headers(recorded.request_headers).items():
        parts.extend(["-H", f"'{key}: {value}'"])
    if recorded.request_body is not None:
        parts.extend(["--data", f"'{redact_body(recorded.request_body, limit=500)}'"])
    return " ".join(parts)


def _as_har(recorded):
    return {
        "log": {
            "version": "1.2",
            "creator": {"name": "snapapi", "version": "0.3.0"},
            "entries": [
                {
                    "request": {
                        "method": recorded.method,
                        "url": recorded.url,
                        "headers": [
                            {"name": key, "value": value}
                            for key, value in redact_headers(recorded.request_headers).items()
                        ],
                    },
                    "response": {
                        "status": recorded.status_code or 0,
                        "headers": [
                            {"name": key, "value": value}
                            for key, value in redact_headers(recorded.response_headers).items()
                        ],
                        "content": {"text": redact_body(recorded.response_body) or ""},
                    },
                }
            ],
        }
    }


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:80]
