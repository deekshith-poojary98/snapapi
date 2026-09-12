import pytest

pytest_plugins = ["snapapi.pytest_plugin"]

from tests.http_server import MockHTTPServer


@pytest.fixture
def http_server():
    with MockHTTPServer() as server:
        yield server
