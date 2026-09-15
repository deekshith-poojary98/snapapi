"""Example SnapAPI listener — POST each result to a generic webhook.

Wire this to any TMS, Slack incoming webhook, or internal collector.
SnapAPI stays tool-agnostic; you map fields on the receiving side.

    export SNAPAPI_WEBHOOK_URL=https://example.com/hooks/snapapi
    snapapi tests/ --listener examples/webhook_listener.py:WebhookListener

Optional:

    SNAPAPI_WEBHOOK_TOKEN   Bearer token (Authorization header)
    SNAPAPI_WEBHOOK_TIMEOUT seconds (default 5)

Payload (JSON)::

    {
      "event": "end_test",
      "suite": "Users",
      "source": "users.sapi",
      "test": {
        "name": "Create User",
        "tags": ["smoke"],
        "status": "passed",
        "duration_ms": 42.0,
        "error": null,
        "requests": [ ... ]   # redacted via TestResult.to_dict()
      }
    }

On suite end, ``event`` is ``end_suite`` and ``suite_result`` is SuiteResult.to_dict().
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class WebhookListener:
    def __init__(self, url=None, token=None, timeout=None):
        self.url = url or os.environ.get("SNAPAPI_WEBHOOK_URL", "").strip()
        self.token = token if token is not None else os.environ.get("SNAPAPI_WEBHOOK_TOKEN", "").strip()
        raw_timeout = timeout if timeout is not None else os.environ.get("SNAPAPI_WEBHOOK_TIMEOUT", "5")
        try:
            self.timeout = float(raw_timeout)
        except (TypeError, ValueError):
            self.timeout = 5.0
        self._warned = False

    def end_test(self, suite, test):
        self._post(
            {
                "event": "end_test",
                "suite": suite.get("name"),
                "source": suite.get("source"),
                "test": test.to_dict() if hasattr(test, "to_dict") else {"name": getattr(test, "name", None)},
            }
        )

    def end_suite(self, result):
        self._post(
            {
                "event": "end_suite",
                "suite_result": result.to_dict() if hasattr(result, "to_dict") else {"name": getattr(result, "name", None)},
            }
        )

    def _post(self, payload):
        if not self.url:
            if not self._warned:
                print("snapapi: WebhookListener: set SNAPAPI_WEBHOOK_URL to enable posting", flush=True)
                self._warned = True
            return
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "snapapi-webhook-listener"},
        )
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # notify() already treats listener errors as warnings; re-raise so that path runs.
            raise RuntimeError(f"webhook post failed: {exc}") from exc
