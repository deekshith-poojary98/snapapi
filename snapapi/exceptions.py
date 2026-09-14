class SnapAPIError(Exception):
    """Base error for SnapAPI runtime and parse failures."""


class CallError(SnapAPIError):
    """A plugin function failed, returned nothing, or returned a disallowed type."""

    def __init__(self, function, error, test=None):
        self.function = function
        self.error = error
        self.test = test
        super().__init__(self.format())

    def format(self):
        lines = ["CALL FAILED", f"  Function: {self.function}"]
        if self.test:
            lines.append(f"  Test: {self.test}")
        lines.append(f"  Error: {self.error}")
        return "\n".join(lines)

    def with_test(self, test):
        if not test or self.test == test:
            return self
        return CallError(self.function, self.error, test=test)


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
