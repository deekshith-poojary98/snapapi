from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from snapapi.engine import Engine
from snapapi.exceptions import ParseError, SnapAPIError
from snapapi.fmt import format_file
from snapapi.history import append_history, format_history, read_history, read_last_failed, write_last_run
from snapapi.lint import format_issues, lint_files
from snapapi.openapi import generate_smoke
from snapapi.parser import TestParser
from snapapi.profiles import load_profile
from snapapi.reports import build_report_payload, write_html_report, write_json_report, write_junit_report
from snapapi.variables import base_variables


def build_parser():
    parser = argparse.ArgumentParser(
        prog="snapapi",
        description="SnapAPI — lightweight DSL for HTTP API testing",
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Run .snaptest suites (default)")
    _add_run_args(run)

    lint = sub.add_parser("lint", help="Parse and validate suites without HTTP")
    lint.add_argument("paths", nargs="+", help="Files or directories")
    lint.add_argument("--env", dest="env_file")
    lint.add_argument("--profile")
    lint.add_argument("--strict", action="store_true", help="Treat unused SAVE as errors")

    fmt = sub.add_parser("fmt", help="Format .snaptest files")
    fmt.add_argument("paths", nargs="+")
    fmt.add_argument("--check", action="store_true")

    openapi = sub.add_parser("openapi", help="Generate smoke tests from an OpenAPI spec")
    openapi.add_argument("spec")
    openapi.add_argument("--base-url")
    openapi.add_argument("-o", "--output")

    history = sub.add_parser("history", help="Show recent run history from .snapapi/history.jsonl")
    history.add_argument("--failed", action="store_true")
    history.add_argument("--since", help="Only rows newer than 7d, 24h, or 30m")

    mock = sub.add_parser("mock", help="Serve routes from a JSON mock file")
    mock.add_argument("spec", help="JSON file with a routes array")
    mock.add_argument("--port", type=int, default=8765)

    watch = sub.add_parser("watch", help="Re-run suites when .snaptest files change")
    _add_run_args(watch)
    watch.add_argument("--interval", type=float, default=0.5)

    # Default positional paths so `snapapi file.snaptest` still works.
    parser.add_argument("paths", nargs="*", help=argparse.SUPPRESS)
    _add_run_args(parser, optional=True)
    return parser


def _add_run_args(parser, optional=False):
    if not optional:
        parser.add_argument("paths", nargs="+", help="One or more .snaptest files or directories")
    parser.add_argument("--tag", action="append", dest="tags", metavar="TAG", default=None)
    parser.add_argument("--name", action="append", dest="names", metavar="TEST", default=None)
    parser.add_argument("--grep", help="Regex filter on test name/description")
    parser.add_argument("--env", dest="env_file", help="KEY=VALUE env file used for ${VAR} interpolation")
    parser.add_argument("--profile", help="Load environments/<name>.env, .snapapi/<name>.env, or <name>.env")
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--report", action="append", default=[], metavar="KIND:PATH")
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--include-skipped", action="store_true")
    parser.add_argument("--include-quarantine", action="store_true")
    parser.add_argument("--last-failed", action="store_true")
    parser.add_argument("--mode", choices=["live", "record", "replay", "record-on-miss"])
    parser.add_argument("--on-fail", action="append", default=[], metavar="curl|har:DIR")
    parser.add_argument("--no-dump", action="store_true", help="Do not print request/response on failure")
    parser.add_argument("--safe-url", action="store_true", help="Block private/metadata URLs")
    parser.add_argument("--allow-private-urls", action="store_true")
    parser.add_argument("--proxy", metavar="URL", help="HTTP/HTTPS proxy URL")
    parser.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification")
    parser.add_argument("--cert", metavar="PATH", help="Client certificate for TLS")
    parser.add_argument("--cacert", metavar="PATH", help="CA bundle used to verify TLS")
    parser.add_argument("--record-on-miss", action="store_true", help="In replay mode, record missing cassettes")
    parser.add_argument("--contract-strict", action="store_true", help="Fail on unmatched OpenAPI path/schema")
    parser.add_argument("--vcr-match", dest="vcr_match", help="Cassette match fields, e.g. query,body,accept,authorization")
    parser.add_argument("--reruns", type=int, default=None, metavar="N", help="Re-run failed tests up to N times")


def collect_files(paths):
    files = []
    seen = set()
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            raise SnapAPIError(f"No such file or directory: {raw}")
        candidates = []
        if path.is_dir():
            candidates = sorted(item for item in path.rglob("*.snaptest") if item.is_file())
            if not candidates:
                raise SnapAPIError(f"No .snaptest files found in directory: {raw}")
        else:
            candidates = [path]
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(candidate)
    return files


def parse_report_specs(specs):
    reports = []
    for spec in specs:
        if ":" not in spec:
            raise SnapAPIError(f"Invalid --report value {spec!r} (expected json:path, junit:path, or html:path)")
        kind, _, path = spec.partition(":")
        kind = kind.strip().lower()
        path = path.strip()
        if kind not in ("json", "junit", "html") or not path:
            raise SnapAPIError(f"Invalid --report value {spec!r} (expected json:path, junit:path, or html:path)")
        reports.append((kind, path))
    return reports


def run_suites(
    files,
    *,
    tags=None,
    names=None,
    env_file=None,
    extra_vars=None,
    timeout=None,
    stop_on_failure=None,
    retry_backoff=None,
    grep=None,
    include_skipped=False,
    include_quarantine=False,
    workers=1,
    dump_on_fail=True,
    on_fail=None,
    mode=None,
    safe_url=False,
    last_failed=None,
    verify=True,
    cert=None,
    proxies=None,
    record_on_miss=False,
    contract_strict=None,
    vcr_match=None,
    reruns=None,
):
    parser = TestParser()
    variables = base_variables(env_file=env_file, extra=extra_vars)
    results = []
    for file_path in files:
        suite = parser.parse(file_path)
        engine = Engine(
            suite,
            variables=dict(variables),
            timeout=timeout,
            tags=tags,
            names=names,
            stop_on_failure=True if stop_on_failure else None,
            retry_backoff=retry_backoff,
            grep=grep,
            include_skipped=include_skipped,
            include_quarantine=include_quarantine,
            workers=workers,
            dump_on_fail=dump_on_fail,
            on_fail=on_fail,
            mode=mode,
            safe_url=safe_url,
            last_failed=last_failed,
            verify=verify,
            cert=cert,
            proxies=proxies,
            record_on_miss=record_on_miss,
            contract_strict=contract_strict,
            vcr_match=vcr_match,
            reruns=reruns,
        )
        results.append(engine.run())
    return results


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("lint", "fmt", "openapi", "run", "history", "mock", "watch"):
        command = argv[0]
        rest = argv[1:]
    else:
        command = "run"
        rest = argv

    try:
        if command == "lint":
            return _cmd_lint(rest)
        if command == "fmt":
            return _cmd_fmt(rest)
        if command == "openapi":
            return _cmd_openapi(rest)
        if command == "history":
            return _cmd_history(rest)
        if command == "mock":
            return _cmd_mock(rest)
        if command == "watch":
            return _cmd_watch(rest)
        return _cmd_run(rest)
    except (ParseError, SnapAPIError, OSError, ValueError) as exc:
        print(f"snapapi: {exc}", file=sys.stderr)
        return 2


def _cmd_run(argv):
    parser = argparse.ArgumentParser(prog="snapapi")
    _add_run_args(parser)
    args = parser.parse_args(argv)
    return _run_from_args(args)


def _run_from_args(args):
    files = collect_files(args.paths)
    report_specs = parse_report_specs(args.report)
    extra = {}
    if args.profile:
        extra.update(load_profile(args.profile))
    names = list(args.names or [])
    last_failed = None
    if args.last_failed:
        last_failed = read_last_failed()
        if not last_failed:
            raise SnapAPIError("No last-failed tests recorded")
    tls = _tls_options(args)
    mode = args.mode
    record_on_miss = bool(getattr(args, "record_on_miss", False))
    if mode == "record-on-miss":
        mode = "replay"
        record_on_miss = True
    results = run_suites(
        files,
        tags=args.tags,
        names=names or None,
        env_file=args.env_file,
        extra_vars=extra or None,
        timeout=args.timeout,
        stop_on_failure=args.stop_on_failure or None,
        grep=args.grep,
        include_skipped=args.include_skipped,
        include_quarantine=args.include_quarantine,
        workers=args.workers,
        dump_on_fail=not args.no_dump,
        on_fail=args.on_fail,
        mode=mode,
        safe_url=args.safe_url and not args.allow_private_urls,
        last_failed=last_failed,
        verify=tls["verify"],
        cert=tls["cert"],
        proxies=tls["proxies"],
        record_on_miss=record_on_miss,
        contract_strict=getattr(args, "contract_strict", False) or None,
        vcr_match=getattr(args, "vcr_match", None),
        reruns=getattr(args, "reruns", None),
    )
    write_last_run(results)
    append_history(results)
    if report_specs:
        payload = build_report_payload(results)
        for kind, path in report_specs:
            if kind == "json":
                write_json_report(payload, path)
            elif kind == "html":
                write_html_report(payload, path)
            else:
                write_junit_report(payload, path)
    failed = any(not result.ok for result in results)
    return 1 if failed else 0


def _cmd_lint(argv):
    parser = argparse.ArgumentParser(prog="snapapi lint")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--env", dest="env_file")
    parser.add_argument("--profile")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    files = collect_files(args.paths)
    extra = {}
    if args.profile:
        extra.update(load_profile(args.profile))
    variables = base_variables(env_file=args.env_file, extra=extra or None)
    issues = lint_files(files, variables=variables, strict=args.strict)
    text = format_issues(issues)
    if text:
        print(text)
    errors = [issue for issue in issues if issue["level"] == "error"]
    return 2 if errors else 0


def _cmd_fmt(argv):
    parser = argparse.ArgumentParser(prog="snapapi fmt")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    files = collect_files(args.paths)
    changed = False
    for path in files:
        if format_file(path, check=args.check):
            changed = True
            print(("would reformat " if args.check else "reformatted ") + str(path))
    if args.check and changed:
        return 1
    return 0


def _cmd_history(argv):
    parser = argparse.ArgumentParser(prog="snapapi history")
    parser.add_argument("--failed", action="store_true")
    parser.add_argument("--since", help="Only rows newer than 7d, 24h, or 30m")
    args = parser.parse_args(argv)
    text = format_history(read_history(), failed=args.failed, since=args.since)
    print(text)
    return 0


def _cmd_mock(argv):
    parser = argparse.ArgumentParser(prog="snapapi mock")
    parser.add_argument("spec")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    from snapapi.mock import MockServer, load_mock_routes

    server = MockServer(load_mock_routes(args.spec), port=args.port)
    print(server.url, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


def _cmd_watch(argv):
    parser = argparse.ArgumentParser(prog="snapapi watch")
    _add_run_args(parser)
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args(argv)
    from snapapi.watch import snapshot_mtimes, try_watchdog_observer

    files = collect_files(args.paths)
    previous, _ = snapshot_mtimes(files)
    pending = {"changed": False}

    def mark_changed():
        pending["changed"] = True

    observer = try_watchdog_observer(files, mark_changed)
    code = _run_from_args(args)
    interval = max(0.05, float(args.interval or 0.5))
    try:
        while True:
            time.sleep(interval)
            files = collect_files(args.paths)
            previous, changed = snapshot_mtimes(files, previous)
            if observer is not None:
                changed = list(changed) or (["watchdog"] if pending["changed"] else [])
                pending["changed"] = False
            if changed:
                code = _run_from_args(args)
    except KeyboardInterrupt:
        return code
    finally:
        if observer is not None:
            observer.stop()
            observer.join(timeout=2)


def _cmd_openapi(argv):
    parser = argparse.ArgumentParser(prog="snapapi openapi")
    parser.add_argument("spec")
    parser.add_argument("--base-url")
    parser.add_argument("-o", "--output")
    args = parser.parse_args(argv)
    text = generate_smoke(args.spec, base_url=args.base_url, output=args.output)
    if not args.output:
        print(text, end="")
    return 0


def _tls_options(args):
    verify = True
    if getattr(args, "insecure", False):
        verify = False
    elif getattr(args, "cacert", None):
        verify = args.cacert
    proxies = None
    proxy = getattr(args, "proxy", None)
    if proxy:
        proxies = {"http": proxy, "https": proxy}
    return {
        "verify": verify,
        "cert": getattr(args, "cert", None),
        "proxies": proxies,
    }


def entry():
    sys.exit(main())
