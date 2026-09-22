"""Reject private/internal URLs before any HTTP or browser navigation."""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

_BLOCKED_HOSTS = {"localhost", "localhost.localdomain"}


def _resolve_host(host: str) -> list[str]:
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, None)
    return list({item[4][0] for item in infos})


def is_safe_public_url(url: str, *, resolve_host=_resolve_host) -> tuple[bool, str]:
    """
    Return (ok, reason). Only http(s) URLs with a public hostname are allowed.

    resolve_host(host) -> list[str] is injectable for tests.
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

    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            addresses = resolve_host(host)
        except socket.gaierror:
            return False, "Could not resolve that hostname."
        except OSError as exc:
            return False, f"Could not resolve that hostname ({exc})."

    if not addresses:
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


def join_and_validate(base_url: str, href: str, *, resolve_host=_resolve_host) -> tuple[str | None, str]:
    """Resolve a possibly relative href against base_url, then apply URL safety."""
    href = (href or "").strip()
    if not href:
        return None, "Empty link."
    absolute = urljoin(base_url, href)
    ok, reason = is_safe_public_url(absolute, resolve_host=resolve_host)
    if not ok:
        logger.warning("[URL] Skipping unsafe navigation target %s (%s)", absolute, reason)
        return None, reason
    return absolute, ""
