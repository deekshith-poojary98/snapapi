class SnapAPIError(Exception):
    """Base error for SnapAPI runtime and parse failures."""


class ParseError(SnapAPIError):
    def __init__(self, message, filename=None, lineno=None):
        self.filename = filename
        self.lineno = lineno
        location = ""
        if filename and lineno is not None:
            location = f"{filename}:{lineno}: "
        elif filename:
            location = f"{filename}: "
        super().__init__(f"{location}{message}")


class JsonPathError(SnapAPIError):
    """Raised when a JSONPath-lite expression cannot be resolved."""


class XPathError(SnapAPIError):
    """Raised when an XPath-lite expression cannot be resolved."""
