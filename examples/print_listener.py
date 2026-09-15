"""Example SnapAPI listener — print each test as it finishes.

    snapapi examples/hello/hello.sapi \\
      --listener examples/print_listener.py:PrintListener
"""


class PrintListener:
    def start_suite(self, suite):
        print(f"listener: start suite {suite.get('name')}")

    def end_test(self, suite, test):
        print(f"listener: {test.status} {test.name}")

    def end_suite(self, result):
        print(f"listener: end suite {result.name} ({result.passed} passed, {result.failed} failed)")

    def report_written(self, kind, path):
        print(f"listener: wrote {kind} {path}")

    def close(self):
        print("listener: close")
