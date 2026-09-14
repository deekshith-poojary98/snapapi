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
from snapapi.listeners import finish_listeners, load_listeners, notify
from snapapi.openapi import generate_smoke
from snapapi.plugins import build_registry, load_plugins
from snapapi.parser import TestParser
from snapapi.profiles import load_profile
from snapapi.reports import build_report_payload, write_html_report, write_json_report, write_junit_report
from snapapi.select import parse_bool_expr
from snapapi.suites import find_suite_files
from snapapi.variables import base_variables, resolve_env_file


def build_parser():
    parser = argparse.ArgumentParser(
        prog="snapapi",
        description="SnapAPI — lightweight DSL for HTTP API testing",
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Run .sapi suites (default)")
    _add_run_args(run)

    lint = sub.add_parser("lint", help="Parse and validate suites without HTTP")
    lint.add_argument("paths", nargs="+", help="Files or directories")
    lint.add_argument("--env", dest="env_file")
    lint.add_argument("--profile")
    lint.add_argument("--strict", action="store_true", help="Treat unused SAVE as errors")
    lint.add_argument(
        "--plugin",
        action="append",
        dest="plugins",
        metavar="PATH[:name]",
        help="Extra Python extension (repeatable). Prefer extensions/ or snapapi.yaml.",
    )

    fmt = sub.add_parser("fmt", help="Format .sapi files")
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

    watch = sub.add_parser("watch", help="Re-run suites when .sapi files change")
    _add_run_args(watch)
    watch.add_argument("--interval", type=float, default=0.5)

    # Default positional paths so `snapapi file.sapi` still works.
    parser.add_argument("paths", nargs="*", help=argparse.SUPPRESS)
    _add_run_args(parser, optional=True)
    return parser


def _add_run_args(parser, optional=False):
    if not optional:
        parser.add_argument("paths", nargs="+", help="One or more .sapi files or directories")
    parser.add_argument("-k", dest="keyword", metavar="EXPR", help="Pytest-style keyword expression on name/description/tags")
    parser.add_argument("-m", dest="tag_expr", metavar="EXPR", help="Pytest-style tag expression (and/or/not)")
    parser.add_argument("--tag", action="append", dest="tags", metavar="TAG", default=None)
    parser.add_argument("--exclude", action="append", dest="exclude_tags", metavar="TAG", default=None, help="Skip tests with this tag (repeatable)")
    parser.add_argument("--name", action="append", dest="names", metavar="TEST", default=None)
    parser.add_argument("--grep", help="Regex filter on test name/description")
    parser.add_argument("-D", "--variable", action="append", dest="cli_vars", metavar="KEY=VALUE", help="Set ${VAR} (repeatable; Robot-style)")
    parser.add_argument(
        "--env",
        dest="env_file",
        help="KEY=VALUE file for ${VAR}. If omitted, loads <suite>.env, then .env, then a single sibling *.env",
    )
    parser.add_argument("--profile", help="Load environments/<name>.env, .snapapi/<name>.env, or <name>.env")
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--report", action="append", default=[], metavar="KIND:PATH")
    parser.add_argument("-x", "--exitfirst", "--stop-on-failure", dest="stop_on_failure", action="store_true")
    parser.add_argument("--maxfail", type=int, metavar="N", help="Stop after N failures (pytest --maxfail)")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--include-skipped", action="store_true")
    parser.add_argument("--include-quarantine", action="store_true")
    parser.add_argument("--lf", "--last-failed", dest="last_failed", action="store_true")
    parser.add_argument("--ff", "--failed-first", dest="failed_first", action="store_true")
    parser.add_argument("--collect-only", "--co", "--dry-run", dest="collect_only", action="store_true", help="List selected tests without HTTP")
    parser.add_argument("-q", "--quiet", action="count", default=0)
    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("--durations", type=int, metavar="N", help="Show the N slowest tests (0 = all)")
    parser.add_argument("--color", choices=["yes", "no", "auto"], default="auto")
    parser.add_argument("--no-color", action="store_true")
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
    parser.add_argument(
        "--listener",
        action="append",
        dest="listeners",
        metavar="PATH[:Class]",
        help="Python listener module or file (repeatable)",
    )
    parser.add_argument(
        "--plugin",
        action="append",
        dest="plugins",
        metavar="PATH[:name]",
        help="Extra Python extension file or module (repeatable). Prefer extensions/ or snapapi.yaml.",
    )


def collect_files(paths):
    files = []
    seen = set()
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            raise SnapAPIError(f"No such file or directory: {raw}")
        candidates = []
        if path.is_dir():
            candidates = find_suite_files(path)
            if not candidates:
                raise SnapAPIError(f"No .sapi or .snaptest files found in directory: {raw}")
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
    exclude_tags=None,
    names=None,
    env_file=None,
    extra_vars=None,
    timeout=None,
    stop_on_failure=None,
    retry_backoff=None,
    grep=None,
    keyword_expr=None,
    tag_expr=None,
    include_skipped=False,
    include_quarantine=False,
    workers=1,
    dump_on_fail=True,
    on_fail=None,
    mode=None,
    safe_url=False,
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
    plugins=None,
    close_listeners=True,
    verbosity=1,
    maxfail=None,
    color=None,
):
    parser = TestParser()
    loaded = list(listeners or [])
    results = []
    remaining = maxfail
    try:
        for file_path in files:
            resolved_env = resolve_env_file(env_file, file_path)
            variables = base_variables(env_file=resolved_env, extra=extra_vars)
            suite = parser.parse(file_path)
            engine = Engine(
                suite,
                variables=dict(variables),
                env_file=resolved_env,
                timeout=timeout,
                tags=tags,
                exclude_tags=exclude_tags,
                names=names,
                stop_on_failure=stop_on_failure,
                retry_backoff=retry_backoff,
                grep=grep,
                keyword_expr=keyword_expr,
                tag_expr=tag_expr,
                include_skipped=include_skipped,
                include_quarantine=include_quarantine,
                workers=workers,
                dump_on_fail=dump_on_fail,
                on_fail=on_fail,
                mode=mode,
                safe_url=safe_url,
                last_failed=last_failed,
                failed_first=failed_first,
                prefer_failed=prefer_failed,
                verify=verify,
                cert=cert,
                proxies=proxies,
                record_on_miss=record_on_miss,
                contract_strict=contract_strict,
                vcr_match=vcr_match,
                reruns=reruns,
                listeners=loaded,
                plugins=plugins,
                verbosity=verbosity,
                maxfail=remaining,
                color=color,
            )
            results.append(engine.run())
            if remaining is not None:
                remaining -= results[-1].failed
                if remaining <= 0:
                    break
    finally:
        if close_listeners:
            finish_listeners(loaded)
    return results


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--version", "-V"):
        from snapapi import __version__

        print(f"snapapi {__version__}")
        return 0
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
    if getattr(args, "collect_only", False):
        return _collect_from_args(args)
    files = collect_files(args.paths)
    report_specs = parse_report_specs(args.report)
    extra = {}
    if args.profile:
        extra.update(load_profile(args.profile))
    extra.update(_parse_cli_vars(getattr(args, "cli_vars", None)))
    names = list(args.names or [])
    last_failed = None
    prefer_failed = None
    if args.last_failed:
        last_failed = read_last_failed()
        if not last_failed:
            raise SnapAPIError("No last-failed tests recorded")
    if getattr(args, "failed_first", False):
        prefer_failed = read_last_failed()
    tls = _tls_options(args)
    mode = args.mode
    record_on_miss = bool(getattr(args, "record_on_miss", False))
    if mode == "record-on-miss":
        mode = "replay"
        record_on_miss = True
    listeners = load_listeners(getattr(args, "listeners", None))
    plugins = load_plugins(getattr(args, "plugins", None))
    stop_on_failure = bool(args.stop_on_failure)
    maxfail = getattr(args, "maxfail", None)
    if maxfail is not None:
        if maxfail < 1:
            raise SnapAPIError("--maxfail must be >= 1")
        if not args.stop_on_failure:
            stop_on_failure = False
    results = run_suites(
        files,
        tags=args.tags,
        exclude_tags=getattr(args, "exclude_tags", None),
        names=names or None,
        env_file=args.env_file,
        extra_vars=extra or None,
        timeout=args.timeout,
        stop_on_failure=stop_on_failure,
        grep=args.grep,
        keyword_expr=parse_bool_expr(args.keyword) if getattr(args, "keyword", None) else None,
        tag_expr=parse_bool_expr(args.tag_expr) if getattr(args, "tag_expr", None) else None,
        include_skipped=args.include_skipped,
        include_quarantine=args.include_quarantine,
        workers=args.workers,
        dump_on_fail=not args.no_dump,
        on_fail=args.on_fail,
        mode=mode,
        safe_url=args.safe_url and not args.allow_private_urls,
        last_failed=last_failed,
        failed_first=getattr(args, "failed_first", False),
        prefer_failed=prefer_failed,
        verify=tls["verify"],
        cert=tls["cert"],
        proxies=tls["proxies"],
        record_on_miss=record_on_miss,
        contract_strict=getattr(args, "contract_strict", False) or None,
        vcr_match=getattr(args, "vcr_match", None),
        reruns=getattr(args, "reruns", None),
        listeners=listeners,
        plugins=plugins or None,
        close_listeners=False,
        verbosity=_verbosity(args),
        maxfail=maxfail,
        color=_color_mode(args),
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
            resolved = Path(path).resolve()
            print(f"  Report ({kind}): {resolved}")
            notify(listeners, "report_written", kind, str(resolved))
    _print_durations(results, getattr(args, "durations", None))
    finish_listeners(listeners)
    failed = any(not result.ok for result in results)
    return 1 if failed else 0


def _cmd_lint(argv):
    parser = argparse.ArgumentParser(prog="snapapi lint")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--env", dest="env_file")
    parser.add_argument("--profile")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument(
        "--plugin",
        action="append",
        dest="plugins",
        metavar="PATH[:name]",
        help="Extra Python extension (repeatable). Prefer extensions/ or snapapi.yaml.",
    )
    args = parser.parse_args(argv)
    files = collect_files(args.paths)
    extra = {}
    if args.profile:
        extra.update(load_profile(args.profile))
    plugin_specs = getattr(args, "plugins", None)
    issues = []
    for path in files:
        variables = base_variables(
            env_file=resolve_env_file(args.env_file, path),
            extra=extra or None,
        )
        issues.extend(
            lint_files(
                [path],
                variables=variables,
                strict=args.strict,
                plugins=build_registry(path, specs=plugin_specs).names(),
            )
        )
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


def _parse_cli_vars(values):
    extra = {}
    for item in values or []:
        if "=" not in item:
            raise SnapAPIError(f"Invalid --variable {item!r} (expected KEY=VALUE)")
        key, _, value = item.partition("=")
        key = key.strip()
        if not key:
            raise SnapAPIError(f"Invalid --variable {item!r} (expected KEY=VALUE)")
        extra[key] = value
    return extra


def _verbosity(args):
    quiet = int(getattr(args, "quiet", 0) or 0)
    verbose = int(getattr(args, "verbose", 0) or 0)
    return max(0, min(2, 1 + verbose - quiet))


def _color_mode(args):
    if getattr(args, "no_color", False):
        return False
    mode = getattr(args, "color", "auto") or "auto"
    if mode == "yes":
        return True
    if mode == "no":
        return False
    return None


def _print_durations(results, limit):
    if limit is None:
        return
    rows = []
    for suite in results:
        for test in suite.tests:
            if test.status not in ("passed", "failed"):
                continue
            rows.append((test.duration_ms or 0, suite.name or "", test.name))
    rows.sort(reverse=True)
    if limit > 0:
        rows = rows[:limit]
    if not rows:
        return
    from snapapi.engine import format_duration

    print()
    print(f"  slowest {len(rows)} durations")
    for duration_ms, suite, name in rows:
        label = f"{suite}  {name}" if suite else name
        print(f"    {format_duration(duration_ms):>8}  {label}")


def _collect_from_args(args):
    files = collect_files(args.paths)
    extra = {}
    if args.profile:
        extra.update(load_profile(args.profile))
    extra.update(_parse_cli_vars(getattr(args, "cli_vars", None)))
    names = list(args.names or []) or None
    last_failed = None
    if args.last_failed:
        last_failed = read_last_failed()
        if not last_failed:
            raise SnapAPIError("No last-failed tests recorded")
    parser = TestParser()
    count = 0
    for file_path in files:
        resolved_env = resolve_env_file(args.env_file, file_path)
        variables = base_variables(env_file=resolved_env, extra=extra or None)
        suite = parser.parse(file_path)
        engine = Engine(
            suite,
            variables=dict(variables),
            env_file=resolved_env,
            tags=args.tags,
            exclude_tags=getattr(args, "exclude_tags", None),
            names=names,
            grep=args.grep,
            keyword_expr=parse_bool_expr(args.keyword) if getattr(args, "keyword", None) else None,
            tag_expr=parse_bool_expr(args.tag_expr) if getattr(args, "tag_expr", None) else None,
            include_skipped=args.include_skipped,
            include_quarantine=args.include_quarantine,
            last_failed=last_failed,
        )
        for test in engine.matching_tests():
            tags = " ".join(test.get("tags") or [])
            suffix = f"  [{tags}]" if tags else ""
            print(f"{file_path}::{test['name']}{suffix}")
            count += 1
    print(f"{count} test{'s' if count != 1 else ''} collected")
    return 0


def entry():
    sys.exit(main())
