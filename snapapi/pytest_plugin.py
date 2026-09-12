from __future__ import annotations

import io
from pathlib import Path

import pytest

from snapapi.engine import Engine, SuiteResult
from snapapi.parser import TestParser
from snapapi.variables import base_variables, resolve_env_file


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "snapapi(path): run a SnapAPI .sapi suite via the snapapi_run fixture",
    )


@pytest.fixture
def snapapi_run():
    def _run(path, **engine_kwargs):
        suite = TestParser().parse(path)
        env_file = engine_kwargs.pop("env_file", None)
        if "variables" in engine_kwargs:
            variables = dict(engine_kwargs.pop("variables") or {})
        else:
            variables = base_variables(env_file=resolve_env_file(env_file, path))
        stream = engine_kwargs.pop("stream", io.StringIO())
        engine = Engine(suite, variables=variables, stream=stream, **engine_kwargs)
        result = engine.run()
        if not result.ok:
            failed = [item for item in result.tests if item.status == "failed"]
            details = "; ".join(f"{item.name}: {item.error}" for item in failed) or "suite failed"
            pytest.fail(f"SnapAPI suite failed ({result.failed} failed): {details}")
        return result

    return _run


def pytest_runtest_setup(item):
    marker = item.get_closest_marker("snapapi")
    if marker is None or not marker.args:
        return
    path = marker.args[0]
    if "snapapi_run" not in item.fixturenames:
        return
    item.user_properties.append(("snapapi_path", str(Path(path))))


__all__ = ["Engine", "SuiteResult", "snapapi_run"]
