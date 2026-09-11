import io

from snapapi.engine import Engine
from snapapi.parser import TestParser


def parse_dsl(text, filename="<string>"):
    return TestParser().parse_text(text, filename=filename)


def run_dsl(text, variables=None, timeout=None, tags=None, stop_on_failure=None, retry_backoff=0):
    suite = parse_dsl(text)
    stream = io.StringIO()
    engine = Engine(
        suite,
        variables=variables,
        timeout=timeout,
        tags=tags,
        stop_on_failure=stop_on_failure,
        retry_backoff=retry_backoff,
        stream=stream,
    )
    result = engine.run()
    return result, engine, stream.getvalue()
