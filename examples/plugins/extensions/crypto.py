"""HMAC for SnapAPI CALL.

    snapapi mock examples/hello/mock.json --port 8765
    snapapi examples/plugins/signed.sapi -D HMAC_SECRET=dev
"""

import hashlib
import hmac as hmaclib


def generate_signature(payload, secret):
    return hmaclib.new(
        str(secret).encode("utf-8"),
        str(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
