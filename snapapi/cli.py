from __future__ import annotations

import argparse
import sys
from pathlib import Path

from snapapi.engine import Engine
from snapapi.exceptions import ParseError, SnapAPIError
from snapapi.parser import TestParser
from snapapi.reports import build_report_payload, write_json_report, write_junit_report
from snapapi.variables import base_variables


def build_parser():
    parser = argparse.ArgumentParser(
        prog="snapapi",
        description="SnapAPI — lightweight DSL for HTTP API testing",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="One or more .snaptest files or directories",
    )
    parser.add_argument(
        "--tag",
        action="append",
        dest="tags",
        metavar="TAG",
        default=None,
        help="Only run tests that have this tag (repeatable; all tags must match)",
    )
    parser.add_argument(
        "--env",
        dest="env_file",
        help="KEY=VALUE env file used for ${VAR} interpolation",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="HTTP timeout in seconds (overrides OPTIONS TIMEOUT)",
    )
    parser.add_argument(
        "--report",
        action="append",
        default=[],
        metavar="KIND:PATH",
        help="Write a report: json:path or junit:path (repeatable)",
    )
    parser.add_argument(
        "--stop-on-failure",
        action="store_true",
        help='Stop each suite on first failure (overrides OPTIONS)',
    )
    return parser


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
            raise SnapAPIError(f"Invalid --report value {spec!r} (expected json:path or junit:path)")
        kind, _, path = spec.partition(":")
        kind = kind.strip().lower()
        path = path.strip()
        if kind not in ("json", "junit") or not path:
            raise SnapAPIError(f"Invalid --report value {spec!r} (expected json:path or junit:path)")
        reports.append((kind, path))
    return reports


def run_suites(files, *, tags=None, env_file=None, timeout=None, stop_on_failure=None, retry_backoff=None):
    parser = TestParser()
    variables = base_variables(env_file=env_file)
    results = []
    for file_path in files:
        suite = parser.parse(file_path)
        engine = Engine(
            suite,
            variables=dict(variables),
            timeout=timeout,
            tags=tags,
            stop_on_failure=True if stop_on_failure else None,
            retry_backoff=retry_backoff,
        )
        results.append(engine.run())
    return results


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        files = collect_files(args.paths)
        report_specs = parse_report_specs(args.report)
        results = run_suites(
            files,
            tags=args.tags,
            env_file=args.env_file,
            timeout=args.timeout,
            stop_on_failure=args.stop_on_failure or None,
        )
        if report_specs:
            payload = build_report_payload(results)
            for kind, path in report_specs:
                if kind == "json":
                    write_json_report(payload, path)
                else:
                    write_junit_report(payload, path)
        failed = any(not result.ok for result in results)
        return 1 if failed else 0
    except (ParseError, SnapAPIError, OSError, ValueError) as exc:
        print(f"snapapi: {exc}", file=sys.stderr)
        return 2


def entry():
    sys.exit(main())
