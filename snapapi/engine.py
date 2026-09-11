from __future__ import annotations

import time

import requests
from colorama import Fore, Style, init

from snapapi.api_client import APIClient
from snapapi.exceptions import JsonPathError, SnapAPIError
from snapapi import jsonpath
from snapapi.variables import interpolate

init(autoreset=True)

RETRY_BACKOFF_SECONDS = 0.05


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
    for test in suite.get("tests") or []:
        if test.get("setup"):
            names.add(test["setup"])
        if test.get("teardown"):
            names.add(test["teardown"])
    return names


class RequestResult:
    def __init__(self, method, url, status_code=None, duration_ms=0, error=None):
        self.method = method
        self.url = url
        self.status_code = status_code
        self.duration_ms = duration_ms
        self.error = error

    def to_dict(self):
        return {
            "method": self.method,
            "url": self.url,
            "status_code": self.status_code,
            "duration_ms": round(self.duration_ms, 3),
            "error": self.error,
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
        stop_on_failure=None,
        retry_backoff=None,
        stream=None,
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
        if stop_on_failure is not None:
            self.stop_on_failure = bool(stop_on_failure)
        else:
            self.stop_on_failure = _as_bool(options.get("STOP-ON-FAILURE"), True)
        self.retry_backoff = RETRY_BACKOFF_SECONDS if retry_backoff is None else retry_backoff
        self.stream = stream
        self.failures = []
        self.success = []
        self._stack = []
        self._helpers = helper_names(suite)

    def run(self):
        started = time.perf_counter()
        self._print(f"\n{'-' * 15} SnapAPI Running {'-' * 15}")
        self._print(f"\n{Fore.CYAN}Running test suite:{Fore.RESET} {self.suite.get('name')}")
        if self.suite.get("description"):
            self._print(f"{Fore.CYAN}Description:{Fore.RESET} {self.suite['description']}")

        results = []
        primaries = [test for test in self.suite["tests"] if test["name"] not in self._helpers]
        for test in primaries:
            if not self._matches_tags(test):
                results.append(TestResult(name=test["name"], tags=test.get("tags") or [], status="skipped"))
                continue
            result = self._run_test(test["name"], role="test")
            results.append(result)
            if result.status == "failed" and self.stop_on_failure:
                self._print(f"\n{Fore.RED}Test suite stopped due to failure in test: {test['name']}")
                self._print(
                    f"{Fore.GREEN}Note:{Fore.RESET} Set {Fore.BLUE}"
                    f'OPTIONS: {{"STOP-ON-FAILURE": false}}{Fore.RESET} to continue on failure'
                )
                break

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
            total_tests = len([t for t in self.suite["tests"] if t["name"] not in self._helpers])
            failed_tests = len(self.failures)
            passed_tests = len(self.success)
            skipped_tests = 0
            duration = None
        else:
            total_tests = suite_result.total
            failed_tests = suite_result.failed
            passed_tests = suite_result.passed
            skipped_tests = suite_result.skipped
            duration = suite_result.duration_ms
        extra = ""
        if skipped_tests:
            extra += f", {Fore.YELLOW}Skipped:{Fore.RESET} {skipped_tests}"
        if duration is not None:
            extra += f", {Fore.CYAN}Duration:{Fore.RESET} {duration:.0f}ms"
        self._print(
            f"\n{Fore.CYAN}Test Summary: {Fore.MAGENTA}Total:{Fore.RESET} {total_tests}, "
            f"{Fore.GREEN}Passed:{Fore.RESET} {passed_tests}, {Fore.RED}Failed:{Fore.RESET} {failed_tests}{extra}"
        )
        if self.failures:
            self._print(f"{Fore.CYAN}Failed tests:")
            for test_name, error in self.failures:
                self._print(f"{Fore.RED} - {test_name}: {error}")
        self._print("\n")

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
                self._print_status(role, False, error)
                if role == "test":
                    self.failures.append((name, error))
                return TestResult(
                    name=name,
                    tags=test.get("tags") or [],
                    status="failed",
                    duration_ms=duration_ms,
                    error=error,
                    requests=collected,
                )

            self._print_status(role, True)
            if role == "test":
                self.success.append(name)
                self._print(f"\n{'- ' * 20}")
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
        client = APIClient(base_url, timeout=self.timeout)
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
        try:
            endpoint = interpolate(step["endpoint"], self.variables)
            data = interpolate(step.get("data"), self.variables) if step.get("data") is not None else None
            headers = {}
            headers.update(test.get("headers") or {})
            headers.update(step.get("headers") or {})
            headers = interpolate(headers, self.variables) if headers else None
        except SnapAPIError as exc:
            return False, str(exc), None

        checks = step.get("checks") or []
        attempts = max((check.get("retry") or 1) for check in checks) if checks else 1
        last_error = None
        recorded = None

        for attempt in range(1, attempts + 1):
            response = None
            started = time.perf_counter()
            try:
                response = client.request(method, endpoint, json=data, headers=headers)
                duration_ms = (time.perf_counter() - started) * 1000
                url = getattr(response, "url", endpoint)
                recorded = RequestResult(method, url, status_code=response.status_code, duration_ms=duration_ms)
                self._print_request(method, endpoint, response.status_code, duration_ms)
                for check in checks:
                    self._execute_check(check, response)
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
                )
            except (requests.RequestException, ValueError, SnapAPIError) as exc:
                duration_ms = (time.perf_counter() - started) * 1000
                last_error = str(exc)
                recorded = RequestResult(method, endpoint, duration_ms=duration_ms, error=last_error)

            if attempt < attempts:
                time.sleep(self.retry_backoff * attempt)

        return False, last_error, recorded

    def _execute_check(self, check, response):
        check_type = check["type"]
        if check_type == "STATUS":
            expected = int(interpolate(str(check["value"]), self.variables))
            actual = response.status_code
            assert actual == expected, f"Status code expected {expected}, got {actual}"
        elif check_type == "CONTAINS":
            expected = interpolate(str(check["value"]), self.variables)
            assert expected in response.text, f"Response does not contain {expected}"
        elif check_type == "JSON":
            self._check_json(check, response)
        elif check_type == "HEADER":
            self._check_header(check, response)
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
        if operator == "==":
            assert actual == expected, f"JSON {path} expected {expected!r}, got {actual!r}"
        elif operator == "!=":
            assert actual != expected, f"JSON {path} expected not {expected!r}, got {actual!r}"
        elif operator.upper() == "CONTAINS":
            assert str(expected) in str(actual), f"JSON {path} value {actual!r} does not contain {expected!r}"
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

    def _save_value(self, save, response):
        path = interpolate(save["path"], self.variables)
        try:
            body = response.json()
        except ValueError as exc:
            raise AssertionError(f"Cannot SAVE from non-JSON response: {exc}") from exc
        try:
            value = jsonpath.extract(body, path)
        except JsonPathError as exc:
            raise AssertionError(str(exc)) from exc
        self.variables[save["name"]] = value
        indent = "  " * len(self._stack)
        self._print(f"{indent}{Fore.MAGENTA}✔  Saved {save['name']}={value!r}{Style.RESET_ALL}")

    def _matches_tags(self, test):
        if not self.tags:
            return True
        test_tags = set(test.get("tags") or [])
        return all(tag in test_tags for tag in self.tags)

    def _announce(self, test, role):
        indent = "  " * (len(self._stack) - 1)
        labels = {
            "test": ("Running test", "Test description", "Test tag"),
            "setup": ("Running setup", "Setup description", "Setup tag"),
            "teardown": ("Running teardown", "Teardown description", "Teardown tag"),
        }
        title, desc_label, tag_label = labels.get(role, labels["test"])
        prefix = "\n" if role == "test" and len(self._stack) == 1 else ""
        self._print(f"{prefix}{indent}{Fore.YELLOW}✔  {title}:{Fore.RESET} {test['name']}")
        if test.get("description"):
            self._print(f"{indent}{Fore.YELLOW}✔  {desc_label}:{Fore.RESET} {test['description']}")
        if test.get("tags"):
            self._print(f"{indent}{Fore.YELLOW}✔  {tag_label}:{Fore.RESET} {', '.join(test['tags'])}")

    def _print_request(self, method, endpoint, status_code, duration_ms):
        indent = "  " * len(self._stack)
        color = Fore.GREEN if 200 <= int(status_code) < 400 else Fore.RED
        self._print(
            f"{indent}{Fore.BLUE}→{Fore.RESET} {method} {endpoint}  "
            f"{color}{status_code}{Fore.RESET}  {duration_ms:.0f}ms"
        )

    def _print_status(self, role, passed, error=None):
        indent = "  " * max(len(self._stack) - 1, 0)
        label = {"test": "Test status", "setup": "Setup status", "teardown": "Teardown status"}.get(role, "Test status")
        if passed:
            self._print(f"{indent}✔ {Fore.YELLOW} {label}:{Fore.GREEN} PASSED")
        else:
            self._print(
                f"{indent}❌{Fore.YELLOW} {label}:{Fore.RED} FAILED\n"
                f"{indent}{Fore.YELLOW}❌ Reason: {Fore.RED}{error}"
            )
            if role == "test":
                self._print(f"\n{'- ' * 20}")

    def _print(self, message=""):
        print(message, file=self.stream)
