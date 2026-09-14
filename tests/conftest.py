import pytest

from tests.http_server import MockHTTPServer

pytest_plugins = ["snapapi.pytest_plugin"]


@pytest.fixture
def http_server():
    with MockHTTPServer() as server:
        yield server
