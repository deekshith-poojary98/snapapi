from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import base64
import hashlib
import secrets
from concurrent.futures import FIRST_COMPLETED, CancelledError, ThreadPoolExecutor, wait
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from colorama import Fore, Style, init
from requests.auth import HTTPDigestAuth

from snapapi.api_client import APIClient, open_files
from snapapi.cassette import cassette_key, load_cassettes, parse_vcr_match, save_cassette
from snapapi.exceptions import JsonPathError, SnapAPIError, XPathError
from snapapi.listeners import notify
from snapapi.openapi import collect_parameters, load_spec, match_operation, request_body_schema, response_schema
from snapapi.parser import walk_expect_checks
from snapapi import jsonpath, xpath
from snapapi.redact import redact_body, redact_headers, redact_saved
from snapapi.safety import assert_public_url
from snapapi.select import eval_keyword_expr, eval_tag_expr
from snapapi.variables import VAR_PATTERN, interpolate

init(autoreset=True)

RETRY_BACKOFF_SECONDS = 0.05
REQUEST_COL_WIDTH = 32


def format_duration(ms):
    if ms is None:
        return "0ms"
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.2f}s"


def _should_color(stream, color=None):
    if color is False or os.environ.get("NO_COLOR"):
        return False
    if color is True:
        return True
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
        if test.get("kind") == "helper":
            names.add(test["name"])
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
    def __init__(self, name, source=None, tests=None, duration_ms=0, error=None, error_name=None):
        self.name = name
        self.source = source
        self.tests = list(tests or [])
        self.duration_ms = duration_ms
        self.error = error
        self.error_name = error_name

    @property
    def ok(self):
        if self.error:
            return False
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
        payload = {
            "name": self.name,
            "source": self.source,
            "ok": self.ok,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "duration_ms": round(self.duration_ms, 3),
            "tests": [test.to_dict() for test in self.tests],
        }
        if self.error:
            payload["error"] = self.error
            payload["error_name"] = self.error_name
        return payload


class Engine:
    def __init__(
        self,
        suite,
        variables=None,
        env_file=None,
        timeout=None,
        tags=None,
        names=None,
        stop_on_failure=None,
        retry_backoff=None,
        stream=None,
        grep=None,
        keyword_expr=None,
        tag_expr=None,
        exclude_tags=None,
        include_skipped=False,
        include_quarantine=False,
        workers=1,
        dump_on_fail=True,
        on_fail=None,
        mode=None,
        cassette_dir=None,
        safe_url=False,
        isolate_variables=None,
        last_failed=None,
        failed_first=False,
        prefer_failed=None,
        verify=True,
        cert=None,
        proxies=None,
        record_on_miss=False,
        contract_strict=None,
        vcr_match=None,
        reruns=None,
        listeners=None,
        verbosity=1,
        maxfail=None,
        color=None,
    ):
        self.suite = suite
        self.variables = dict(variables or {})
        self.env_file = env_file
        options = suite.get("options") or {}
        if timeout is not None:
            self.timeout = float(timeout)
        elif options.get("TIMEOUT") is not None:
            self.timeout = float(options["TIMEOUT"])
        else:
            self.timeout = 30.0
        self.tags = list(tags or [])
        self.exclude_tags = list(exclude_tags or [])
        self.names = list(names or [])
        self.grep = grep
        self.keyword_expr = keyword_expr
        self.tag_expr = tag_expr
        self.include_skipped = include_skipped
        self.include_quarantine = include_quarantine
        self.workers = max(1, int(workers or 1))
        self.dump_on_fail = dump_on_fail
        self.on_fail = list(on_fail or [])
        self.mode = (mode or options.get("MODE") or "live").lower()
        self.cassette_dir = cassette_dir or options.get("CASSETTE_DIR") or ".snapapi/cassettes"
        self.safe_url = safe_url or _as_bool(options.get("SAFE-URL"), False)
        self.last_failed = list(last_failed or [])
        self.failed_first = bool(failed_first)
        self.prefer_failed = list(prefer_failed or [])
        self.verify = verify
        self.cert = cert
        self.proxies = dict(proxies or {})
        self.record_on_miss = bool(record_on_miss) or self.mode == "record-on-miss"
        if self.mode == "record-on-miss":
            self.mode = "replay"
        if isolate_variables is None:
            isolate_variables = self.workers > 1
        self.isolate_variables = isolate_variables
        self.stop_on_failure = bool(stop_on_failure)
        self.retry_backoff = RETRY_BACKOFF_SECONDS if retry_backoff is None else retry_backoff
        self.stream = stream
        self.verbosity = 1 if verbosity is None else int(verbosity)
        self.maxfail = None if maxfail is None else max(1, int(maxfail))
        self._color = _should_color(stream, color)
        self.failures = []
        self.success = []
        self._stack = []
        self._helpers = helper_names(suite)
        self._print_lock = threading.Lock()
        self._oauth_cache = {}
        self._cassettes = load_cassettes(self.cassette_dir) if self.mode in ("replay", "record-on-miss") else {}
        self._cassette_lock = threading.Lock()
        self._last_duration = 0
        self._client = None
        self._openapi_spec = self._load_openapi_spec(options.get("OPENAPI"))
        if contract_strict is None:
            self.contract_strict = _as_bool(options.get("OPENAPI-STRICT"), False)
        else:
            self.contract_strict = bool(contract_strict)
        self.vcr_match = parse_vcr_match(vcr_match if vcr_match is not None else options.get("VCR-MATCH"))
        self.reruns = max(0, int(options.get("RERUNS") or 0) if reruns is None else int(reruns))
        self.listeners = list(listeners or [])
        self._dep_status = {}

    def run(self):
        started = time.perf_counter()
        name = self.suite.get("name") or "suite"
        self._print(f"{self._paint('SnapAPI', Fore.CYAN, Style.BRIGHT)}  {name}")
        if self.verbosity >= 1 and self.suite.get("description"):
            self._print(self._paint(self.suite["description"], Style.DIM))
        if self.verbosity >= 1 and self.env_file:
            self._print(self._paint(f"  env {self.env_file}", Style.DIM))
        self._apply_sets(self.suite.get("sets"))
        self._notify("start_suite", self._suite_info())

        results = []
        if self.suite.get("setup"):
            setup_result = self._run_test(self.suite["setup"], role="setup")
            if setup_result.status == "failed":
                setup_name = f"SUITE-SETUP ({self.suite['setup']})"
                reason = f"suite setup {self.suite['setup']!r} failed"
                results.extend(self._skip_primaries(reason))
                suite_result = SuiteResult(
                    name=self.suite.get("name"),
                    source=self.suite.get("source"),
                    tests=results,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    error=setup_result.error or "failed",
                    error_name=setup_name,
                )
                self.print_summary(suite_result)
                self._notify("end_suite", suite_result)
                return suite_result

        primaries = self._primary_tests()
        use_parallel = self.workers > 1
        if use_parallel:
            deps = self._sibling_save_deps(primaries)
            if deps:
                self._print(
                    self._paint(
                        "  warning: tests share SAVE values across primaries; running sequentially",
                        Fore.YELLOW,
                    )
                )
                use_parallel = False
                self.isolate_variables = False
            elif any(test.get("depends") for test in primaries):
                use_parallel = False
        if use_parallel:
            results.extend(self._run_parallel(primaries))
        else:
            for test in primaries:
                skip_reason = self._skip_reason(test)
                if skip_reason:
                    results.append(
                        TestResult(name=test["name"], tags=test.get("tags") or [], status="skipped", error=skip_reason)
                    )
                    self._record_dep_status(test["name"], "skipped")
                    self._notify("end_test", self._suite_info(), results[-1])
                    continue
                if not self._matches_filter(test):
                    results.append(TestResult(name=test["name"], tags=test.get("tags") or [], status="skipped"))
                    self._notify("end_test", self._suite_info(), results[-1])
                    continue
                dep_reason = self._depends_reason(test)
                if dep_reason:
                    self._print_skip(test, dep_reason)
                    results.append(
                        TestResult(name=test["name"], tags=test.get("tags") or [], status="skipped", error=dep_reason)
                    )
                    self._record_dep_status(test["name"], "skipped")
                    self._notify("end_test", self._suite_info(), results[-1])
                    continue
                self._notify("start_test", self._suite_info(), test["name"], test.get("tags") or [])
                batch = self._run_examples(test)
                self._record_dep_batch(test["name"], batch)
                results.extend(batch)
                for item in batch:
                    self._notify("end_test", self._suite_info(), item)
                if self._should_stop(results):
                    hint = (
                        "omit -x / --stop-on-failure to continue"
                        if self.stop_on_failure
                        else "stopped by --maxfail"
                    )
                    self._print(
                        self._paint(
                            f"  stopped after {test['name']!r}  ({hint})",
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
        self._notify("end_suite", suite_result)
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
            if suite_result.error:
                failed_rows.insert(0, (suite_result.error_name or "SUITE-SETUP", suite_result.error))
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
        rows = self._selected_example_rows(test, rows)
        isolate_rows = self.isolate_variables or bool(test.get("examples"))
        results = []
        for row in rows:
            snapshot = dict(self.variables)
            if row:
                self.variables.update(row)
            start_vars = dict(self.variables)
            max_attempts = 1 + max(0, int(self.reruns or 0))
            result = None
            for attempt in range(max_attempts):
                if attempt:
                    self.variables = dict(start_vars)
                result = self._run_test(
                    test["name"],
                    role="test",
                    record=False,
                    announce=(attempt == 0),
                )
                if result.status != "failed":
                    if result.status == "passed":
                        self._print_outcome(True, result.duration_ms)
                        self.success.append(test["name"])
                    break
            else:
                self._print_outcome(False, result.duration_ms)
                self.failures.append((test["name"], result.error))
            if row:
                label = next((value for value in row.values() if value), "row")
                result.name = f"{test['name']} [{label}]"
            results.append(result)
            if isolate_rows:
                self.variables = snapshot
        return results

    def _selected_example_rows(self, test, rows):
        if not self.last_failed:
            return rows
        wanted = []
        whole = False
        for item in self.last_failed:
            if not self._identity_matches(item, test):
                continue
            name = item["name"] if isinstance(item, dict) else item
            if name == test["name"]:
                whole = True
                break
            wanted.append(name)
        if whole or not wanted:
            return rows
        selected = []
        for row in rows:
            if not row:
                if test["name"] in wanted:
                    selected.append(row)
                continue
            label = next((value for value in row.values() if value), "row")
            if f"{test['name']} [{label}]" in wanted:
                selected.append(row)
        return selected or rows

    def _run_parallel(self, primaries):
        results = []
        runnable = []
        for test in primaries:
            skip_reason = self._skip_reason(test)
            if skip_reason:
                results.append(
                    TestResult(name=test["name"], tags=test.get("tags") or [], status="skipped", error=skip_reason)
                )
                self._notify("end_test", self._suite_info(), results[-1])
                continue
            if not self._matches_filter(test):
                results.append(TestResult(name=test["name"], tags=test.get("tags") or [], status="skipped"))
                self._notify("end_test", self._suite_info(), results[-1])
                continue
            runnable.append(test)
        if not runnable:
            return self._sort_parallel_results(results, primaries)

        stop_scheduling = threading.Event()
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            iterator = iter(runnable)
            in_flight = {}

            def submit_next():
                if stop_scheduling.is_set():
                    return False
                try:
                    test = next(iterator)
                except StopIteration:
                    return False
                in_flight[pool.submit(self._run_isolated, test)] = test
                return True

            for _ in range(min(self.workers, len(runnable))):
                if not submit_next():
                    break

            while in_flight:
                done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for future in done:
                    in_flight.pop(future, None)
                    try:
                        batch, output = future.result()
                    except CancelledError:
                        continue
                    if output:
                        self._print(output.rstrip("\n"))
                    batch = batch if isinstance(batch, list) else [batch]
                    results.extend(batch)
                    for item in batch:
                        self._notify("end_test", self._suite_info(), item)
                    if self._should_stop(results):
                        stop_scheduling.set()
                        for pending in list(in_flight):
                            pending.cancel()
                if not stop_scheduling.is_set():
                    submit_next()

        return self._sort_parallel_results(results, primaries)

    def _sort_parallel_results(self, results, primaries):
        order = {test["name"]: index for index, test in enumerate(primaries)}
        results.sort(key=lambda item: (order.get(item.name.split(" [")[0], 0), item.name))
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
            keyword_expr=self.keyword_expr,
            tag_expr=self.tag_expr,
            exclude_tags=self.exclude_tags,
            include_skipped=self.include_skipped,
            include_quarantine=self.include_quarantine,
            workers=1,
            dump_on_fail=self.dump_on_fail,
            on_fail=self.on_fail,
            mode=self.mode,
            cassette_dir=self.cassette_dir,
            safe_url=self.safe_url,
            isolate_variables=True,
            last_failed=self.last_failed,
            failed_first=False,
            verify=self.verify,
            cert=self.cert,
            proxies=self.proxies,
            record_on_miss=self.record_on_miss,
            contract_strict=self.contract_strict,
            vcr_match=self.vcr_match,
            reruns=self.reruns,
            verbosity=self.verbosity,
            color=self._color,
        )
        child._oauth_cache = self._oauth_cache
        child._cassettes = self._cassettes
        child._cassette_lock = self._cassette_lock
        child._helpers = self._helpers
        child._openapi_spec = self._openapi_spec
        results = child._run_examples(test)
        output = child.stream.getvalue() if child.stream else ""
        return results, output

    def _run_test(self, name, role="test", record=True, announce=True):
        test = self.suite["test_map"][name]
        started = time.perf_counter()
        collected = []
        error = None

        if name in self._stack:
            cycle = " -> ".join(self._stack + [name])
            raise SnapAPIError(f"SETUP/TEARDOWN cycle detected: {cycle}")

        self._stack.append(name)
        try:
            self._apply_sets(test.get("sets"))
            if announce:
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
                if record:
                    if role == "test":
                        self._print_outcome(False, duration_ms)
                        self.failures.append((name, error))
                    elif announce:
                        self._print_outcome(False, duration_ms)
                return TestResult(
                    name=name,
                    tags=test.get("tags") or [],
                    status="failed",
                    duration_ms=duration_ms,
                    error=error,
                    requests=collected,
                )

            if role == "test" and record:
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
        client = self._new_client(base_url, follow_redirects=follow)
        self._client = client
        oauth = test.get("oauth2") or self.suite.get("oauth2")
        if oauth:
            try:
                token = self._oauth_token(oauth)
            except SnapAPIError as exc:
                return False, str(exc), []
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
            self._apply_sets(step.get("sets"))
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
            digest = step.get("digest") or test.get("digest") or self.suite.get("digest")
            auth = None
            if digest:
                auth = HTTPDigestAuth(
                    interpolate(digest.get("username") or "", self.variables),
                    interpolate(digest.get("password") or "", self.variables),
                )
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
        try:
            self._validate_request_contracts(
                method,
                url_preview,
                headers,
                raw_body if raw_body is not None else data,
                step.get("checks") or [],
            )
        except AssertionError as exc:
            self._print_error(str(exc), under_request=False)
            return False, str(exc), None

        checks = step.get("checks") or []
        wait = step.get("wait")
        attempts = max((check.get("retry") or 1) for check in checks) if checks else 1
        retry_on = next((check.get("retry_on") for check in checks if check.get("retry_on")), None)
        retry_backoff = next((check.get("retry_backoff") for check in checks if check.get("retry_backoff")), None)
        last_error = None
        recorded = None
        body_type = step.get("body_type") or "json"
        follow = step.get("follow_redirects")
        oauth = step.get("oauth2") or test.get("oauth2") or self.suite.get("oauth2")
        wait_deadline = time.time() + wait["timeout"] if wait else None
        if wait:
            attempts = max(attempts, 10000)

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
                        auth=auth,
                    )
                    if (
                        getattr(response, "status_code", None) == 401
                        and oauth
                        and self._oauth_can_refresh(oauth)
                    ):
                        token = self._oauth_token(oauth, force_refresh=True)
                        headers["Authorization"] = f"Bearer {token}"
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
                            auth=auth,
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
                    if wait:
                        self._execute_check(wait["check"], response, duration_ms, method, url)
                    for check in checks:
                        self._execute_check(check, response, duration_ms, method, url)
                    if self._openapi_spec is not None:
                        self._validate_openapi(
                            self._openapi_spec, method, url, response, strict=self.contract_strict
                        )
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

                if wait and wait_deadline is not None and time.time() + wait["backoff"] <= wait_deadline:
                    time.sleep(wait["backoff"])
                    continue
                if wait:
                    if last_error:
                        self._print_error(last_error)
                        if self.dump_on_fail and recorded:
                            self._print_dump(recorded)
                        self._emit_on_fail(test, recorded)
                    return False, last_error, recorded
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
        auth = kwargs.get("auth")
        url = client._build_url(endpoint)
        body = raw_body if raw_body is not None else data
        if self.mode == "replay":
            key = cassette_key(method, url, body, headers=headers, match=self.vcr_match)
            record = self._cassettes.get(key)
            if not record:
                if self.record_on_miss:
                    response = self._live_request(
                        client,
                        method,
                        endpoint,
                        data=data,
                        raw_body=raw_body,
                        headers=headers,
                        files=files,
                        body_type=body_type,
                        content_type=content_type,
                        follow=follow,
                        auth=auth,
                    )
                    self._store_cassette(key, method, url, response)
                    return response
                raise SnapAPIError(f"No cassette for {method} {url}")
            return _fake_response(record, session=getattr(client, "session", None))
        response = self._live_request(
            client,
            method,
            endpoint,
            data=data,
            raw_body=raw_body,
            headers=headers,
            files=files,
            body_type=body_type,
            content_type=content_type,
            follow=follow,
            auth=auth,
        )
        if self.mode == "record":
            key = cassette_key(method, url, body, headers=headers, match=self.vcr_match)
            self._store_cassette(key, method, url, response)
        return response

    def _live_request(self, client, method, endpoint, **kwargs):
        return client.request(
            method,
            endpoint,
            json=kwargs.get("data") if kwargs.get("body_type") in (None, "json", "graphql") else None,
            data=kwargs.get("data") if kwargs.get("body_type") == "form" else None,
            raw=kwargs.get("raw_body"),
            headers=kwargs.get("headers"),
            files=kwargs.get("files"),
            body_type=kwargs.get("body_type"),
            content_type=kwargs.get("content_type"),
            follow_redirects=kwargs.get("follow"),
            auth=kwargs.get("auth"),
        )

    def _store_cassette(self, key, method, url, response):
        payload = {
            "method": method,
            "url": url,
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": response.text,
        }
        with self._cassette_lock:
            save_cassette(self.cassette_dir, key, payload)
            self._cassettes[key] = dict(payload)
            self._cassettes[key]["key"] = key

    def _execute_check(self, check, response, duration_ms=0, method=None, url=None):
        check_type = check["type"]
        if check_type == "AND":
            for term in check.get("terms") or []:
                self._execute_check(term, response, duration_ms, method, url)
            return
        if check_type == "OR":
            errors = []
            for term in check.get("terms") or []:
                try:
                    self._execute_check(term, response, duration_ms, method, url)
                    return
                except AssertionError as exc:
                    errors.append(str(exc))
            detail = "; ".join(errors) if errors else "no alternatives"
            raise AssertionError(f"OR expected at least one check to pass: {detail}")
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
        elif check_type == "OPENAPI":
            spec = self._load_openapi_spec(check.get("path"))
            if spec is None:
                raise AssertionError("EXPECT openapi requires a spec path")
            strict = bool(check.get("strict")) or self.contract_strict
            self._validate_openapi(spec, method, url or getattr(response, "url", None), response, strict=strict)
        elif check_type == "XPATH":
            self._check_xpath(check, response)
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
        if operator.upper() == "EACH":
            if not isinstance(actual, list):
                raise AssertionError(f"JSON {path} each requires an array, got {type(actual).__name__}")
            subpath = check.get("each_path") or "$"
            if not str(subpath).startswith("$"):
                subpath = "$." + str(subpath)
            sub_op = check.get("each_operator") or "=="
            for index, item in enumerate(actual):
                try:
                    item_value = jsonpath.extract(item, subpath)
                except JsonPathError as exc:
                    raise AssertionError(f"JSON {path}[{index}] {exc}") from exc
                self._assert_json_value(item_value, sub_op, expected, f"JSON {path}[{index}] {subpath}")
            return
        if operator.upper() == "CONTAINS-ALL":
            if not isinstance(actual, (list, tuple, set)):
                raise AssertionError(f"JSON {path} contains-all requires an array, got {actual!r}")
            missing = [item for item in _as_list(expected) if item not in actual]
            assert not missing, f"JSON {path} value {actual!r} does not contain all of {expected!r} (missing {missing!r})"
            return
        self._assert_json_value(actual, operator, expected, f"JSON {path}")

    def _assert_json_value(self, actual, operator, expected, label):
        if operator == "==":
            assert actual == expected, f"{label} expected {expected!r}, got {actual!r}"
        elif operator == "!=":
            assert actual != expected, f"{label} expected not {expected!r}, got {actual!r}"
        elif operator.upper() == "CONTAINS":
            if isinstance(actual, (list, tuple, set)):
                assert expected in actual, f"{label} value {actual!r} does not contain {expected!r}"
            else:
                assert str(expected) in str(actual), f"{label} value {actual!r} does not contain {expected!r}"
        elif operator.upper() == "MATCHES":
            assert re.search(str(expected), str(actual)), f"{label} value {actual!r} does not match {expected!r}"
        elif operator in (">", ">=", "<", "<="):
            self._compare(actual, operator, expected, label)
        else:
            raise AssertionError(f"Unknown JSON operator {operator}")

    def _check_xpath(self, check, response):
        path = interpolate(check["path"], self.variables)
        try:
            actual = xpath.extract(_response_text(response), path)
        except XPathError as exc:
            raise AssertionError(str(exc)) from exc
        expected = interpolate(check["value"], self.variables)
        operator = check.get("operator") or "=="
        if operator == "==":
            assert actual == expected, f"XPath {path} expected {expected!r}, got {actual!r}"
        elif operator == "!=":
            assert actual != expected, f"XPath {path} expected not {expected!r}, got {actual!r}"
        elif operator.upper() == "CONTAINS":
            assert str(expected) in str(actual), f"XPath {path} value {actual!r} does not contain {expected!r}"
        else:
            raise AssertionError(f"Unknown XPath operator {operator}")

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
        shown = redact_saved(save["name"], value)
        self._print(self._paint(f"{indent}saved {save['name']}={shown}", Style.DIM))

    def _oauth_token(self, spec, force_refresh=False):
        token_url = interpolate(spec.get("token_url"), self.variables)
        client_id = interpolate(spec.get("client_id"), self.variables)
        secret = interpolate(spec.get("client_secret") or "", self.variables)
        username = interpolate(spec.get("username") or "", self.variables)
        key = (token_url, client_id, username)
        cached = self._oauth_cache.get(key)
        if isinstance(cached, str):
            cached = {"access_token": cached}
        if cached and cached.get("access_token") and not force_refresh:
            return cached["access_token"]
        grant = (spec.get("grant") or spec.get("grant_type") or "client_credentials").lower()
        if force_refresh and cached and cached.get("refresh_token"):
            data = {
                "grant_type": "refresh_token",
                "refresh_token": cached["refresh_token"],
                "client_id": client_id,
                "client_secret": secret,
            }
        elif grant == "password":
            data = {
                "grant_type": "password",
                "client_id": client_id,
                "client_secret": secret,
                "username": username,
                "password": interpolate(spec.get("password") or "", self.variables),
            }
        elif grant in ("authorization_code", "authorization-code"):
            code = interpolate(spec.get("code") or "", self.variables)
            if not code:
                raise SnapAPIError(
                    "OAuth2 authorization_code requires code=${AUTH_CODE} "
                    "(SnapAPI does not open a browser for PKCE)"
                )
            data = {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": secret,
                "redirect_uri": interpolate(spec.get("redirect_uri") or "", self.variables),
            }
            if _as_bool(spec.get("pkce"), False):
                verifier, challenge = _pkce_s256()
                data["code_verifier"] = verifier
                data["code_challenge"] = challenge
                data["code_challenge_method"] = "S256"
        else:
            data = {
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": secret,
            }
        client = self._new_client()
        response = client.request(
            "POST",
            token_url,
            data=data,
            body_type="form",
        )
        if response.status_code >= 400:
            raise SnapAPIError(f"OAuth2 token request failed: {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise SnapAPIError("OAuth2 token response is not JSON") from exc
        token = payload.get("access_token")
        if not token:
            raise SnapAPIError("OAuth2 token response missing access_token")
        self._oauth_cache[key] = {
            "access_token": token,
            "refresh_token": payload.get("refresh_token") or (cached or {}).get("refresh_token"),
        }
        return token

    def _oauth_can_refresh(self, spec):
        token_url = interpolate(spec.get("token_url"), self.variables)
        client_id = interpolate(spec.get("client_id"), self.variables)
        username = interpolate(spec.get("username") or "", self.variables)
        cached = self._oauth_cache.get((token_url, client_id, username))
        if isinstance(cached, dict) and cached.get("refresh_token"):
            return True
        return False

    def _apply_sets(self, sets):
        for item in sets or []:
            self.variables[item["name"]] = interpolate(item["value"], self.variables)

    def _load_openapi_spec(self, spec_path):
        if not spec_path:
            return None
        path = Path(interpolate(str(spec_path), self.variables))
        if not path.is_absolute():
            path = Path(self._base_dir()) / path
        return load_spec(path)

    def _validate_request_contracts(self, method, url, headers, body, checks):
        specs = []
        if self._openapi_spec is not None:
            specs.append((self._openapi_spec, self.contract_strict))
        for check in checks or []:
            for item in walk_expect_checks(check):
                if item.get("type") != "OPENAPI":
                    continue
                spec = self._load_openapi_spec(item.get("path"))
                if spec is None:
                    continue
                specs.append((spec, bool(item.get("strict")) or self.contract_strict))
        seen = set()
        for spec, strict in specs:
            marker = id(spec)
            if marker in seen:
                continue
            seen.add(marker)
            self._validate_openapi_request(spec, method, url, headers, body, strict=strict)

    def _validate_openapi_request(self, spec, method, url, headers, body, strict=None):
        if spec is None:
            return
        fail = self.contract_strict if strict is None else bool(strict)
        path = urlsplit(url or "").path or url or ""
        op = match_operation(spec, method, path)
        if op is None:
            self._contract_issue(f"OpenAPI: unmatched path/method {method} {path}", fail)
            return
        params = collect_parameters(spec, method, path)
        query = dict(parse_qsl(urlsplit(url or "").query, keep_blank_values=True))
        header_map = {str(key).lower(): value for key, value in (headers or {}).items()}
        for param in params:
            if not isinstance(param, dict) or not param.get("required"):
                continue
            name = param.get("name")
            if not name:
                continue
            location = param.get("in")
            if location == "query" and str(name) not in query:
                self._contract_issue(f"OpenAPI: missing required query parameter {name}", fail)
            elif location == "header" and str(name).lower() not in header_map:
                self._contract_issue(f"OpenAPI: missing required header {name}", fail)
        schema, required = request_body_schema(spec, method, path)
        if required and body is None:
            self._contract_issue(f"OpenAPI: missing required request body for {method} {path}", fail)
            return
        if schema is None or body is None:
            return
        instance = body
        if isinstance(body, (bytes, str)):
            try:
                instance = json.loads(body)
            except (TypeError, ValueError):
                self._contract_issue(f"OpenAPI: request body is not JSON for {method} {path}", fail)
                return
        try:
            import jsonschema
        except ImportError as exc:
            raise AssertionError("jsonschema is required for OpenAPI request validation") from exc
        try:
            jsonschema.validate(instance, schema)
        except jsonschema.ValidationError as exc:
            self._contract_issue(f"OpenAPI request schema validation failed: {exc.message}", fail)

    def _contract_issue(self, message, fail):
        if fail:
            raise AssertionError(message)
        self._print(self._paint(f"{self._spaces(1)}warning: {message}", Fore.YELLOW))

    def _validate_openapi(self, spec, method, url, response, strict=None):
        if response is None:
            return
        fail = self.contract_strict if strict is None else bool(strict)
        path = urlsplit(url or "").path or url or ""
        op = match_operation(spec, method, path)
        if op is None:
            self._contract_issue(f"OpenAPI: unmatched path/method {method} {path}", fail)
            return
        schema = response_schema(spec, method, path, getattr(response, "status_code", None))
        if schema is None:
            self._contract_issue(
                f"OpenAPI: missing response schema for {method} {path} {getattr(response, 'status_code', '')}",
                fail,
            )
            return
        try:
            import jsonschema
        except ImportError as exc:
            raise AssertionError("jsonschema is required for OpenAPI response validation") from exc
        try:
            instance = response.json()
        except ValueError as exc:
            raise AssertionError(f"Response is not JSON: {exc}") from exc
        try:
            jsonschema.validate(instance, schema)
        except jsonschema.ValidationError as exc:
            raise AssertionError(f"OpenAPI schema validation failed: {exc.message}") from exc

    def _new_client(self, base_url="", follow_redirects=True):
        return APIClient(
            base_url,
            timeout=self.timeout,
            follow_redirects=follow_redirects,
            verify=self.verify,
            cert=self.cert,
            proxies=self.proxies or None,
        )

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
        selected = only or primaries
        if self.last_failed:
            selected = [test for test in selected if self._matches_last_failed(test)]
        if self.failed_first:
            selected = sorted(
                selected,
                key=lambda test: 0 if self._matches_failed_list(self.prefer_failed or self.last_failed, test) else 1,
            )
        return self._order_by_depends(selected)

    def _order_by_depends(self, tests):
        if not any(test.get("depends") for test in tests):
            return tests
        by_name = {test["name"]: test for test in tests}
        index = {test["name"]: i for i, test in enumerate(tests)}
        seen = set()
        visiting = set()
        ordered = []

        def visit(name):
            if name in seen or name not in by_name:
                return
            if name in visiting:
                return
            visiting.add(name)
            test = by_name[name]
            deps = [dep for dep in (test.get("depends") or []) if dep in by_name]
            deps.sort(key=lambda dep: index[dep])
            for dep in deps:
                visit(dep)
            visiting.remove(name)
            seen.add(name)
            ordered.append(test)

        for test in tests:
            visit(test["name"])
        return ordered

    def matching_tests(self):
        tests = []
        for test in self._primary_tests():
            if self._skip_reason(test):
                continue
            if not self._matches_filter(test):
                continue
            tests.append(test)
        return tests

    def _matches_last_failed(self, test):
        return self._matches_failed_list(self.last_failed, test)

    def _matches_failed_list(self, items, test):
        return any(self._identity_matches(item, test) for item in items or [])

    def _identity_matches(self, item, test):
        if isinstance(item, str):
            item = {"name": item}
        source = self.suite.get("source")
        if item.get("file") and source and source != "<string>":
            if not _same_file(item["file"], source):
                return False
        if item.get("suite") and self.suite.get("name") and item["suite"] != self.suite.get("name"):
            return False
        name = item.get("name") or ""
        if name == test["name"]:
            return True
        return name.startswith(test["name"] + " [") and name.endswith("]")

    def _sibling_save_deps(self, primaries):
        saves = {}
        for test in primaries:
            for name in _saves_in_test(test):
                saves.setdefault(name, set()).add(test["name"])
        deps = []
        for test in primaries:
            allowed = _saves_in_test(test) | self._setup_saves(test) | set(self.variables)
            for var in _vars_used_in_test(test):
                owners = saves.get(var, set()) - {test["name"]}
                if owners and var not in allowed:
                    deps.append((test["name"], var, sorted(owners)))
        return deps

    def _setup_saves(self, test):
        names = set()
        seen = set()
        current = test.get("setup")
        test_map = self.suite.get("test_map") or {}
        while current and current not in seen:
            seen.add(current)
            helper = test_map.get(current)
            if not helper:
                break
            names.update(_saves_in_test(helper))
            current = helper.get("setup")
        return names

    def _skip_reason(self, test):
        if test.get("skip") and not self.include_skipped:
            return test.get("skip") or "skipped"
        if test.get("quarantine") and not self.include_quarantine:
            return test.get("quarantine") or "quarantine"
        return None

    def _depends_reason(self, test):
        for name in test.get("depends") or []:
            status = self._dep_status.get(name)
            if status is None:
                return f"depends on {name!r} which has not run"
            if status == "failed":
                return f"depends on {name!r} which failed"
            if status == "skipped":
                return f"depends on {name!r} which was skipped"
        return None

    def _record_dep_status(self, name, status):
        self._dep_status[name] = status

    def _record_dep_batch(self, name, batch):
        statuses = [item.status for item in batch]
        if any(status == "failed" for status in statuses):
            self._dep_status[name] = "failed"
        elif statuses and all(status == "skipped" for status in statuses):
            self._dep_status[name] = "skipped"
        elif any(status == "passed" for status in statuses):
            self._dep_status[name] = "passed"
        else:
            self._dep_status[name] = "skipped"

    def _skip_primaries(self, reason):
        results = []
        for test in self._primary_tests():
            self._print_skip(test, reason)
            item = TestResult(name=test["name"], tags=test.get("tags") or [], status="skipped", error=reason)
            results.append(item)
            self._notify("end_test", self._suite_info(), item)
        return results

    def _print_skip(self, test, reason):
        if self.verbosity < 1:
            return
        tags = test.get("tags") or []
        tag_part = self._paint(f"  [{', '.join(tags)}]", Style.DIM) if tags else ""
        self._print()
        self._print(f"{test['name']}{tag_part}")
        self._print(f"  {self._paint('SKIP', Fore.YELLOW, Style.BRIGHT)}  {self._paint(reason, Style.DIM)}")

    def _matches_filter(self, test):
        if self.tags:
            test_tags = set(test.get("tags") or [])
            if not all(tag in test_tags for tag in self.tags):
                return False
        if self.exclude_tags:
            test_tags = {str(tag).lower() for tag in test.get("tags") or []}
            if any(str(tag).lower() in test_tags for tag in self.exclude_tags):
                return False
        if self.tag_expr is not None and not eval_tag_expr(self.tag_expr, test.get("tags") or []):
            return False
        if self.grep:
            blob = f"{test.get('name') or ''} {test.get('description') or ''}"
            if not re.search(self.grep, blob, re.IGNORECASE):
                return False
        if self.keyword_expr is not None:
            blob = " ".join(
                [
                    str(test.get("name") or ""),
                    str(test.get("description") or ""),
                    " ".join(test.get("tags") or []),
                ]
            )
            if not eval_keyword_expr(self.keyword_expr, blob):
                return False
        return True

    def _should_stop(self, results):
        failed = sum(1 for item in results if item.status == "failed")
        if failed <= 0:
            return False
        if self.stop_on_failure:
            return True
        if self.maxfail is not None and failed >= self.maxfail:
            return True
        return False

    def _matches_tags(self, test):
        return self._matches_filter(test)

    def _announce(self, test, role):
        if self.verbosity < 1:
            return
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
        if self.verbosity < 1:
            return
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
        if self.verbosity < 1:
            return
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

    def _suite_info(self):
        return {
            "name": self.suite.get("name"),
            "source": self.suite.get("source"),
            "description": self.suite.get("description"),
        }

    def _notify(self, method, *args):
        notify(self.listeners, method, *args, on_error=self._listener_error)

    def _listener_error(self, listener, method, exc):
        name = getattr(listener, "__class__", type(listener)).__name__
        self._print(self._paint(f"  warning: listener {name}.{method} failed: {exc}", Fore.YELLOW))

    def _base_dir(self):
        source = self.suite.get("source")
        if source and source != "<string>":
            return str(Path(source).parent)
        return str(Path.cwd())


def _as_list(value):
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _pkce_s256():
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def merge_query(endpoint, params):
    """Merge PARAM/QUERY values into an endpoint, overriding same-named path query keys."""
    if not params:
        return endpoint
    parts = urlsplit(endpoint)
    merged = dict(parse_qsl(parts.query, keep_blank_values=True))
    for key, value in params.items():
        merged[str(key)] = "" if value is None else str(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(merged), parts.fragment))


def _same_file(left, right):
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return str(left) == str(right)


def _saves_in_test(test):
    names = set()
    for step in test.get("steps") or []:
        for save in step.get("saves") or []:
            names.add(save["name"])
    return names


def _vars_used_in_test(test):
    blobs = [test.get("base_url"), test.get("headers")]
    for step in test.get("steps") or []:
        blobs.extend(
            [
                step.get("endpoint"),
                step.get("data"),
                step.get("headers"),
                step.get("query"),
                step.get("raw_body"),
            ]
        )
        for check in step.get("checks") or []:
            blobs.extend(check.values())
    names = set()
    for blob in blobs:
        names.update(_vars_in(blob))
    return names


def _vars_in(value):
    if isinstance(value, str):
        return set(VAR_PATTERN.findall(value))
    if isinstance(value, dict):
        names = set()
        for key, item in value.items():
            names.update(_vars_in(key))
            names.update(_vars_in(item))
        return names
    if isinstance(value, list):
        names = set()
        for item in value:
            names.update(_vars_in(item))
        return names
    return set()


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


def _fake_response(record, session=None):
    body = record.get("body") or ""
    headers = record.get("headers") or {}
    cookies = _apply_cassette_cookies(session, headers)

    def _json():
        return json.loads(body) if body else {}

    return SimpleNamespace(
        status_code=record.get("status_code", 200),
        text=body,
        headers=headers,
        cookies=cookies,
        url=record.get("url"),
        json=_json,
    )


def _apply_cassette_cookies(session, headers):
    from http.cookies import SimpleCookie

    raw = None
    for key, value in (headers or {}).items():
        if str(key).lower() == "set-cookie":
            raw = value
            break
    cookies = {}
    if not raw:
        return cookies
    parsed = SimpleCookie()
    try:
        if isinstance(raw, (list, tuple)):
            for item in raw:
                parsed.load(str(item))
        else:
            parsed.load(str(raw))
    except (TypeError, ValueError):
        return cookies
    for name, morsel in parsed.items():
        cookies[name] = morsel.value
        if session is not None:
            session.cookies.set(name, morsel.value)
    return cookies


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
