from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from urllib3.exceptions import LocationParseError
from urllib3.util.connection import _DEFAULT_TIMEOUT, _set_socket_options, allowed_gai_family

from snapapi.exceptions import SnapAPIError

METADATA_HOSTS = {"169.254.169.254", "metadata.google.internal"}

# Carrier-grade NAT / shared-address space — not publicly routable.
BLOCKED_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),
)


def assert_public_url(url):
    host = (urlsplit(url).hostname or "").strip().lower()
    if not host:
        raise SnapAPIError(f"URL has no host: {url}")
    if host in METADATA_HOSTS or host.endswith(".internal"):
        raise SnapAPIError(f"Blocked private/metadata host: {host}")
    for address in _resolve(host):
        assert_public_ip(address, host=host)


def assert_public_ip(address, host=None):
    """Raise if ``address`` is not allowed as a TCP peer under ``--safe-url``."""
    ip = ipaddress.ip_address(address)
    if _is_blocked_ip(ip):
        label = host or str(address)
        raise SnapAPIError(f"Blocked private/link-local host: {label} ({address})")


def _is_blocked_ip(ip):
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return _is_blocked_ip(ip.ipv4_mapped)
    if any(ip in network for network in BLOCKED_NETWORKS):
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    )


def create_safe_connection(
    address,
    timeout=_DEFAULT_TIMEOUT,
    source_address=None,
    socket_options=None,
):
    """Connect after validating the exact sockaddr from a single DNS lookup.

    DNS is resolved once. Each candidate address is checked against the safe-url
    policy, then ``connect()`` uses that sockaddr — there is no second lookup
    between validation and connect (which would reintroduce TOCTOU/rebinding).
    """
    host, port = address
    if host.startswith("["):
        host = host.strip("[]")
    err = None
    family = allowed_gai_family()

    try:
        host.encode("idna")
    except UnicodeError as exc:
        raise LocationParseError(f"'{host}', label empty or too long") from exc

    try:
        candidates = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SnapAPIError(f"Cannot resolve host {host}: {exc}") from exc

    if not candidates:
        raise OSError("getaddrinfo returns an empty list")

    blocked = None
    for af, socktype, proto, _canonname, sa in candidates:
        try:
            assert_public_ip(sa[0], host=host)
        except SnapAPIError as exc:
            blocked = exc
            continue
        sock = None
        try:
            sock = socket.socket(af, socktype, proto)
            _set_socket_options(sock, socket_options)
            if timeout is not _DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sa)
            err = None
            return sock
        except OSError as exc:
            err = exc
            if sock is not None:
                sock.close()

    if blocked is not None and err is None:
        raise blocked
    if err is not None:
        try:
            raise err
        finally:
            err = None
    raise OSError("getaddrinfo returns an empty list")


def _resolve(host):
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SnapAPIError(f"Cannot resolve host {host}: {exc}") from exc
    return [info[4][0] for info in infos]
