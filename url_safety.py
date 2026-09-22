"""Reject private/internal URLs before fetching user-supplied career pages."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_HOSTS = {"localhost", "localhost.localdomain"}


def is_safe_public_url(url: str) -> tuple[bool, str]:
    """
    Return (ok, reason). Only http(s) URLs with a public hostname are allowed.
    """
    raw = (url or "").strip()
    if not raw:
        return False, "Enter a careers page URL."

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return False, "URL must start with http:// or https://."
    if not parsed.netloc:
        return False, "URL is missing a hostname."

    host = parsed.hostname or ""
    if not host:
        return False, "URL is missing a hostname."
    if host.lower() in _BLOCKED_HOSTS:
        return False, "Local addresses are not allowed."

    addresses: list[str] = []
    try:
        ipaddress.ip_address(host)
        addresses.append(host)
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
            addresses = list({item[4][0] for item in infos})
        except socket.gaierror:
            return False, "Could not resolve that hostname."

    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            return False, "Private or local network addresses are not allowed."

    return True, ""
