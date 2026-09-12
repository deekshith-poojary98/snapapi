from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from snapapi.exceptions import SnapAPIError

METADATA_HOSTS = {"169.254.169.254", "metadata.google.internal"}


def assert_public_url(url):
    host = (urlsplit(url).hostname or "").strip().lower()
    if not host:
        raise SnapAPIError(f"URL has no host: {url}")
    if host in METADATA_HOSTS or host.endswith(".internal"):
        raise SnapAPIError(f"Blocked private/metadata host: {host}")
    for address in _resolve(host):
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise SnapAPIError(f"Blocked private/link-local host: {host} ({address})")


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
