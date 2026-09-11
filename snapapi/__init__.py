"""SnapAPI — a lightweight DSL for HTTP API testing."""

from snapapi.engine import Engine
from snapapi.exceptions import ParseError, SnapAPIError
from snapapi.parser import TestParser

__version__ = "0.2.0"
__all__ = ["Engine", "TestParser", "ParseError", "SnapAPIError", "__version__"]
