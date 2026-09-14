"""Curl → SnapAPI converter.

Pipeline: raw curl → tokens → normalized request → .sapi text.
Translates intent (AUTH, QUERY, BODY) rather than dumping every header verbatim.
"""

from __future__ import annotations

import base64
import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlparse, urlunparse

from snapapi.exceptions import SnapAPIError

SHELL_SUB_RE = re.compile(r"(?<!\\)\$\(|(?<!\\)`")
SHELL_VAR_RE = re.compile(r"(?<!\\)\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
CURL_START_RE = re.compile(r"(?:^|\n)\s*curl\b", re.IGNORECASE)
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}

# Flags that take a value (consumed as next token).
VALUE_FLAGS = {
    "-X",
    "--request",
    "-H",
    "--header",
    "-d",
    "--data",
    "--data-raw",
    "--data-binary",
    "--data-urlencode",
    "--json",
    "-F",
    "--form",
    "--form-string",
    "-u",
    "--user",
    "-b",
    "--cookie",
    "-A",
    "--user-agent",
    "-e",
    "--referer",
    "--url",
    "-T",
    "--upload-file",
    "-o",
    "--output",
    "-w",
    "--write-out",
    "-x",
    "--proxy",
    "--cacert",
    "--cert",
    "--key",
    "--connect-timeout",
    "--max-time",
    "-m",
    "--max-redirs",
    "-E",
    "--pinnedpubkey",
    "--resolve",
    "--interface",
    "--unix-socket",
    "-K",
    "--config",
}

# Ignored noiseless curl UX flags (no SnapAPI equivalent needed).
IGNORED_FLAGS = {
    "-s",
    "--silent",
    "-S",
    "--show-error",
    "-v",
    "--verbose",
    "-i",
    "--include",
    "-f",
    "--fail",
    "--fail-with-body",
    "-n",
    "--netrc",
    "--netrc-optional",
    "--compressed",
    "--http1.1",
    "--http2",
    "--http2-prior-knowledge",
    "--http3",
    "-4",
    "--ipv4",
    "-6",
    "--ipv6",
    "-#",
    "--progress-bar",
    "-N",
    "--no-buffer",
    "--no-progress-meter",
    "-q",
    "--disable",
    "--path-as-is",
    "--remote-name",
    "-O",
    "--remote-header-name",
    "-J",
    "--create-dirs",
    "--globoff",
    "-g",
}

UNSUPPORTED_VALUE_FLAGS = {
    "-T": "Upload file (-T / --upload-file)",
    "--upload-file": "Upload file (-T / --upload-file)",
    "-x": "Proxy (-x / --proxy)",
    "--proxy": "Proxy (-x / --proxy)",
    "--cacert": "Custom CA (--cacert)",
    "--cert": "Client certificate (--cert)",
    "--key": "Client key (--key)",
    "-K": "Curl config file (-K / --config)",
    "--config": "Curl config file (-K / --config)",
    "--unix-socket": "Unix socket (--unix-socket)",
    "--resolve": "DNS override (--resolve)",
}


@dataclass
class ConvertWarning:
    feature: str
    detail: str = ""
    action: str = ""

    def format(self):
        lines = [f"Unsupported Curl feature: {self.feature}"]
        if self.detail:
            lines.append(f"  {self.detail}")
        if self.action:
            lines.append(f"  Manual action required: {self.action}")
        return "\n".join(lines)


@dataclass
class CurlRequest:
    method: str = "GET"
    url: str = ""
    headers: list[tuple[str, str]] = field(default_factory=list)
    query: list[tuple[str, str]] = field(default_factory=list)
    body: str | None = None
    body_kind: str | None = None  # json | form | raw
    auth_scheme: str | None = None  # bearer | basic
    auth_value: str | None = None
    follow_redirects: bool | None = None
    warnings: list[ConvertWarning] = field(default_factory=list)


@dataclass
class ConvertResult:
    text: str
    warnings: list[ConvertWarning] = field(default_factory=list)
    requests: list[CurlRequest] = field(default_factory=list)

    @property
    def ok(self):
        return bool(self.text.strip()) and bool(self.requests)


def convert_curl(
    source,
    *,
    suite_name=None,
    test_name=None,
    output=None,
    expect_status=None,
):
    """Convert one or more curl commands into SnapAPI DSL text."""
    text = _load_source(source)
    result = curl_to_sapi(
        text,
        suite_name=suite_name,
        test_name=test_name,
        expect_status=expect_status,
    )
    if output:
        Path(output).write_text(result.text, encoding="utf-8")
    return result


def curl_to_sapi(text, *, suite_name=None, test_name=None, expect_status=None):
    commands = split_curl_commands(text)
    if not commands:
        raise SnapAPIError("No curl command found (pass a curl string or a file containing curl)")

    requests = []
    warnings = []
    for index, command in enumerate(commands):
        req = parse_curl(command)
        if len(commands) > 1 and test_name:
            # Only apply an explicit test name to a single-command convert.
            pass
        elif test_name and len(commands) == 1:
            req._test_name = test_name  # type: ignore[attr-defined]
        else:
            req._test_name = _default_test_name(req, index)  # type: ignore[attr-defined]
        requests.append(req)
        warnings.extend(req.warnings)

    dsl = render_sapi(requests, suite_name=suite_name, expect_status=expect_status)
    return ConvertResult(text=dsl, warnings=warnings, requests=requests)


def split_curl_commands(text):
    raw = (text or "").strip()
    if not raw:
        return []
    # Allow pasting a bare URL as a GET.
    if not CURL_START_RE.search(raw) and _looks_like_url(raw.split()[0]):
        raw = f"curl {raw}"
    starts = [match.start() for match in CURL_START_RE.finditer("\n" + raw)]
    if not starts:
        return []
    # Adjust for the leading newline we prepended.
    starts = [max(0, pos - 1) for pos in starts]
    chunks = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(raw)
        chunk = raw[start:end].strip()
        if chunk:
            chunks.append(chunk)
    return chunks


def parse_curl(command):
    req = CurlRequest()
    normalized, pre_warnings = _normalize_curl_text(command)
    req.warnings.extend(pre_warnings)

    try:
        tokens = shlex.split(normalized, posix=True)
    except ValueError as exc:
        raise SnapAPIError(f"Could not parse curl command: {exc}") from exc

    if not tokens:
        raise SnapAPIError("Empty curl command")
    if tokens[0].lower() != "curl":
        raise SnapAPIError(f"Expected curl command, got: {tokens[0]!r}")

    data_parts = []
    data_as_query = False
    form_parts = []
    json_body = None
    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token in ("-G", "--get"):
            data_as_query = True
            i += 1
            continue
        if token in ("-I", "--head"):
            req.method = "HEAD"
            i += 1
            continue
        if token in ("-L", "--location"):
            req.follow_redirects = True
            i += 1
            continue
        if token in ("-k", "--insecure"):
            req.warnings.append(
                ConvertWarning(
                    "Insecure TLS (-k)",
                    detail="SnapAPI has --insecure on the CLI, not in the DSL.",
                    action="Run with snapapi --insecure when needed",
                )
            )
            i += 1
            continue
        if token in IGNORED_FLAGS:
            i += 1
            continue
        if token.startswith("-") and token in UNSUPPORTED_VALUE_FLAGS:
            feature = UNSUPPORTED_VALUE_FLAGS[token]
            value = tokens[i + 1] if i + 1 < len(tokens) else ""
            req.warnings.append(
                ConvertWarning(
                    feature,
                    detail=f"Value: {value}" if value else "",
                    action="Handle outside the generated suite",
                )
            )
            i += 2 if i + 1 < len(tokens) else 1
            continue
        if token.startswith("-") and "=" in token and token.split("=", 1)[0] in VALUE_FLAGS | set(
            UNSUPPORTED_VALUE_FLAGS
        ):
            flag, _, value = token.partition("=")
            tokens[i : i + 1] = [flag, value]
            continue
        # curl allows glued short flags: -XPOST, -HContent-Type: application/json
        glued = _split_glued_flag(token)
        if glued is not None:
            tokens[i : i + 1] = list(glued)
            continue
        combined = _expand_combined_shorts(token)
        if combined is not None:
            tokens[i : i + 1] = combined
            continue
        if token in VALUE_FLAGS:
            if i + 1 >= len(tokens):
                raise SnapAPIError(f"curl flag {token} requires a value")
            value = tokens[i + 1]
            if token == "--json":
                json_body = value_to_snap_vars(value)
                i += 2
                continue
            _apply_flag(req, token, value, data_parts=data_parts, form_parts=form_parts)
            i += 2
            continue
        if token.startswith("-"):
            req.warnings.append(
                ConvertWarning(
                    f"Unrecognized curl flag ({token})",
                    action="Review manually; it was skipped",
                )
            )
            # Skip a following value if it does not look like a flag/URL.
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("-") and not _looks_like_url(tokens[i + 1]):
                i += 2
            else:
                i += 1
            continue
        # Positional URL
        if not req.url:
            req.url = value_to_snap_vars(token)
        else:
            req.warnings.append(
                ConvertWarning(
                    "Multiple URLs",
                    detail=token,
                    action="Only the first URL was kept",
                )
            )
        i += 1

    if json_body is not None:
        _ensure_header(req, "Content-Type", "application/json")
        _ensure_header(req, "Accept", "application/json")
        if json_body.startswith("@"):
            req.warnings.append(
                ConvertWarning(
                    "Body from file (@path)",
                    detail=json_body,
                    action="Inline the JSON body or use FILE: / BODY raw",
                )
            )
            req.body = "${FILE_BODY}"
            req.body_kind = "raw"
        else:
            req.body = json_body
            req.body_kind = "json"
        if req.method == "GET":
            req.method = "POST"

    if form_parts:
        has_file = any("=" in part and part.split("=", 1)[1].startswith("@") for part in form_parts)
        req.body_kind = "form"
        req.body = "&".join(form_parts)
        if req.method == "GET":
            req.method = "POST"
        req.warnings.append(
            ConvertWarning(
                "Multipart / form (-F)",
                detail="Emitted as BODY form; file fields need FILE: manually"
                if has_file
                else "Emitted as BODY form (multipart boundary not preserved)",
                action="Replace file parts with FILE: field FROM ./path" if has_file else "Review BODY form fields",
            )
        )

    if data_parts:
        payload = "".join(data_parts) if len(data_parts) == 1 else "&".join(data_parts)
        payload = value_to_snap_vars(payload)
        if any(part.startswith("@") for part in data_parts):
            req.warnings.append(
                ConvertWarning(
                    "Body from file (@path)",
                    detail=payload,
                    action="Inline the body or use FILE: / BODY raw",
                )
            )
            req.body = "${FILE_BODY}"
            req.body_kind = "raw"
        elif data_as_query:
            for key, val in parse_qsl(payload, keep_blank_values=True):
                req.query.append((key, val))
        else:
            req.body = payload
            req.body_kind = _infer_body_kind(payload, req.headers)
            if req.method == "GET":
                req.method = "POST"

    _extract_auth_from_headers(req)
    _split_url_query(req)
    _warn_duplicate_query(req)

    if not req.url:
        raise SnapAPIError("curl command has no URL")
    return req


def render_sapi(requests, *, suite_name=None, expect_status=None):
    if not requests:
        raise SnapAPIError("Nothing to render")

    # Group by origin so multiple curls against one host share a SUITE/URL.
    groups = []
    for req in requests:
        origin, path = _split_origin_path(req.url)
        key = origin or ""
        if not groups or groups[-1][0] != key:
            groups.append((key, []))
        groups[-1][1].append((req, path))

    lines = []
    for group_index, (origin, items) in enumerate(groups):
        name = suite_name
        if name is None:
            if len(groups) == 1 and len(items) == 1:
                name = "Converted Request"
            else:
                name = _suite_name_from_origin(origin) if origin else f"Converted {group_index + 1}"
        elif group_index > 0:
            name = f"{suite_name} ({group_index + 1})"

        lines.append(f"SUITE: {name}")
        if origin:
            lines.append(f"URL: {origin}")
        lines.append("")

        for req, path in items:
            test_name = getattr(req, "_test_name", None) or _default_test_name(req, 0)
            lines.append(f"TEST: {test_name}")
            method = (req.method or "GET").upper()
            if method not in HTTP_METHODS:
                method = "GET"
            endpoint = path or "/"
            lines.append(f"  {method}: {endpoint}")

            if req.query:
                pairs = "&".join(f"{key}={value}" for key, value in req.query)
                lines.append(f"  QUERY: {pairs}")

            if req.follow_redirects:
                lines.append("  FOLLOW-REDIRECTS: true")

            if req.auth_scheme and req.auth_value is not None:
                lines.append(f"  AUTH: {req.auth_scheme} {req.auth_value}")

            for header_name, header_value in req.headers:
                if header_name.lower() == "authorization" and req.auth_scheme:
                    continue
                lines.append(f"  HEADER {header_name}: {header_value}")

            if req.body is not None:
                lines.extend(_render_body(req))

            if expect_status is not None:
                lines.append(f"  EXPECT: status == {expect_status}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def format_warnings(warnings):
    if not warnings:
        return "✓ Converted"
    lines = [f"✓ Converted", f"⚠ {len(warnings)} feature{'s' if len(warnings) != 1 else ''} require manual review", ""]
    for warning in warnings:
        lines.append(warning.format())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def read_clipboard():
    """Read clipboard text. Raises SnapAPIError if unavailable."""
    import platform
    import shutil
    import subprocess

    system = platform.system()
    try:
        if system == "Darwin":
            return subprocess.check_output(["pbpaste"], text=True)
        if system == "Windows":
            return subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                text=True,
            )
        if shutil.which("xclip"):
            return subprocess.check_output(["xclip", "-selection", "clipboard", "-o"], text=True)
        if shutil.which("xsel"):
            return subprocess.check_output(["xsel", "--clipboard", "--output"], text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SnapAPIError(f"Could not read clipboard: {exc}") from exc
    raise SnapAPIError("Clipboard support needs pbpaste (macOS), xclip/xsel (Linux), or PowerShell (Windows)")


def value_to_snap_vars(value):
    """Rewrite shell $VAR / ${VAR} to SnapAPI ${VAR}."""

    def repl(match):
        name = match.group(1) or match.group(2)
        return "${" + name + "}"

    return SHELL_VAR_RE.sub(repl, value)


def _load_source(source):
    if source is None:
        raise SnapAPIError("Nothing to convert")
    text = str(source)
    path = Path(text)
    # Prefer file when the path exists and does not look like an inline curl.
    if path.is_file() and not text.strip().lower().startswith("curl"):
        return path.read_text(encoding="utf-8")
    if "\n" not in text and path.suffix in {".sh", ".bash", ".zsh", ".txt", ".curl"} and path.is_file():
        return path.read_text(encoding="utf-8")
    return text


def _normalize_curl_text(command):
    warnings = []
    text = command.replace("\r\n", "\n").replace("\r", "\n")
    # Join backslash line continuations.
    text = re.sub(r"\\\n\s*", " ", text)
    if SHELL_SUB_RE.search(text):
        warnings.append(
            ConvertWarning(
                "Shell command substitution",
                detail="$(...) or backticks cannot be evaluated",
                action="Replace with a literal BODY or ${VAR}",
            )
        )
        # Neutralize so shlex can still tokenize.
        text = re.sub(r"\$\([^)]*\)", "${SHELL_OUTPUT}", text)
        text = re.sub(r"`[^`]*`", "${SHELL_OUTPUT}", text)
    return text.strip(), warnings


def _apply_flag(req, flag, value, *, data_parts, form_parts):
    value = value_to_snap_vars(value)
    if flag in ("-X", "--request"):
        req.method = value.upper()
        return
    if flag in ("-H", "--header"):
        name, _, header_value = value.partition(":")
        name = name.strip()
        header_value = header_value.strip()
        if not name:
            raise SnapAPIError(f"Invalid header: {value!r}")
        req.headers.append((name, header_value))
        return
    if flag in ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode"):
        data_parts.append(value)
        return
    if flag in ("-F", "--form", "--form-string"):
        form_parts.append(value)
        return
    if flag in ("-u", "--user"):
        req.auth_scheme = "basic"
        req.auth_value = value
        return
    if flag in ("-b", "--cookie"):
        req.headers.append(("Cookie", value))
        return
    if flag in ("-A", "--user-agent"):
        req.headers.append(("User-Agent", value))
        return
    if flag in ("-e", "--referer"):
        req.headers.append(("Referer", value))
        return
    if flag == "--url":
        req.url = value
        return
    if flag in ("-o", "--output", "-w", "--write-out", "--connect-timeout", "--max-time", "-m", "--max-redirs"):
        return
    req.warnings.append(
        ConvertWarning(
            f"Unhandled curl flag ({flag})",
            detail=value,
            action="Review manually",
        )
    )


def _extract_auth_from_headers(req):
    remaining = []
    for name, value in req.headers:
        if name.lower() != "authorization":
            remaining.append((name, value))
            continue
        scheme, _, rest = value.partition(" ")
        scheme_l = scheme.lower()
        rest = rest.strip()
        if scheme_l == "bearer" and rest:
            req.auth_scheme = "bearer"
            req.auth_value = rest
            continue
        if scheme_l == "basic" and rest:
            decoded = _try_decode_basic(rest)
            if decoded is not None:
                req.auth_scheme = "basic"
                req.auth_value = decoded
            else:
                # Keep encoded basic as AUTH basic user:pass when possible; else header.
                remaining.append((name, value))
            continue
        remaining.append((name, value))
    req.headers = remaining


def _try_decode_basic(token):
    if token.startswith("${") or "$" in token:
        return None
    try:
        raw = base64.b64decode(token.encode("ascii"), validate=True).decode("utf-8")
    except Exception:
        return None
    if ":" not in raw:
        return None
    return raw


def _split_url_query(req):
    parsed = urlparse(req.url)
    if parsed.query:
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            req.query.append((key, value))
        req.url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, "", parsed.fragment))


def _warn_duplicate_query(req):
    seen = set()
    dupes = set()
    for key, _value in req.query:
        if key in seen:
            dupes.add(key)
        seen.add(key)
    for key in sorted(dupes):
        req.warnings.append(
            ConvertWarning(
                "Repeated query parameter",
                detail=f"{key} appears more than once",
                action="SnapAPI QUERY keeps the last value; split into separate tests if needed",
            )
        )


def _ensure_header(req, name, value):
    lowered = name.lower()
    for existing, _ in req.headers:
        if existing.lower() == lowered:
            return
    req.headers.append((name, value))


def _split_origin_path(url):
    parsed = urlparse(url)
    if not parsed.scheme and not parsed.netloc:
        # Relative or opaque
        if url.startswith("/"):
            return "", url
        return "", "/" + url if url else "/"
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path or "/"
    if parsed.params:
        path = f"{path};{parsed.params}"
    if parsed.fragment:
        # Fragments are not sent over HTTP; drop with a soft ignore.
        pass
    return origin, path


def _infer_body_kind(payload, headers):
    content_type = ""
    for name, value in headers:
        if name.lower() == "content-type":
            content_type = value.lower()
            break
    stripped = payload.strip()
    if "application/json" in content_type or stripped.startswith("{") or stripped.startswith("["):
        try:
            json.loads(stripped)
            return "json"
        except json.JSONDecodeError:
            if "application/json" in content_type:
                return "json"
    if "application/x-www-form-urlencoded" in content_type or (
        "=" in payload and "&" in payload and not stripped.startswith("{")
    ):
        return "form"
    if "application/x-www-form-urlencoded" in content_type or ("=" in payload and not stripped.startswith("{")):
        return "form"
    return "raw"


def _render_body(req):
    lines = []
    body = req.body if req.body is not None else ""
    kind = req.body_kind or "raw"
    if kind == "json":
        try:
            parsed = json.loads(body)
            lines.append(f"  BODY: {json.dumps(parsed, ensure_ascii=False)}")
        except json.JSONDecodeError:
            # Keep as-is (may contain ${VAR})
            lines.append(f"  BODY: {body}")
    elif kind == "form":
        lines.append(f"  BODY: form {body}")
    else:
        content_type = "text/plain"
        for name, value in req.headers:
            if name.lower() == "content-type":
                content_type = value
                break
        lines.append(f"  BODY: raw {content_type} {body}")
    return lines


def _default_test_name(req, index):
    method = (req.method or "GET").title()
    _origin, path = _split_origin_path(req.url)
    parts = [part for part in path.split("/") if part and not part.startswith("{")]
    if parts:
        label = parts[-1].replace("-", " ").replace("_", " ")
        label = " ".join(word.capitalize() for word in label.split())
        # Common REST verbs
        if method == "Get":
            return f"Get {label}"
        if method == "Post":
            singular = label
            lowered = label.lower()
            if lowered.endswith("ies") and len(label) > 3:
                singular = label[:-3] + "y"
            elif lowered.endswith("s") and not lowered.endswith("ss") and len(label) > 1:
                singular = label[:-1]
            return f"Create {singular}"
        if method in ("Put", "Patch"):
            return f"Update {label}"
        if method == "Delete":
            return f"Delete {label}"
        return f"{method} {label}"
    if index:
        return f"{method} Request {index + 1}"
    return f"{method} Request"


def _suite_name_from_origin(origin):
    host = urlparse(origin).hostname or "Converted"
    # api.example.com → Example, users.api.com → Users
    parts = [part for part in host.split(".") if part and part not in ("www", "api", "com", "io", "dev", "net", "org")]
    if parts:
        return parts[0].capitalize()
    return "Converted"


def _looks_like_url(value):
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://") or value.startswith("/")


def _split_glued_flag(token):
    """Split -XPOST / -HName: value style tokens into flag + value."""
    for flag in sorted(VALUE_FLAGS, key=len, reverse=True):
        if flag.startswith("--"):
            continue
        if token.startswith(flag) and len(token) > len(flag):
            return flag, token[len(flag) :]
    return None


# Short flags that take no value and may be combined: curl -sSL ...
_BOOL_SHORT = {
    "s": "-s",
    "S": "-S",
    "v": "-v",
    "i": "-i",
    "f": "-f",
    "n": "-n",
    "L": "-L",
    "I": "-I",
    "G": "-G",
    "k": "-k",
    "g": "-g",
    "N": "-N",
    "4": "-4",
    "6": "-6",
    "q": "-q",
    "O": "-O",
    "J": "-J",
    "#": "-#",
}


def _expand_combined_shorts(token):
    if not token.startswith("-") or token.startswith("--") or len(token) < 3:
        return None
    chars = token[1:]
    if not all(char in _BOOL_SHORT for char in chars):
        return None
    return [_BOOL_SHORT[char] for char in chars]
