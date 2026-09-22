"""Shared HTTP GET for public ATS JSON APIs."""

from __future__ import annotations

from typing import Any

import requests

from config import BROWSER
from url_safety import is_safe_public_url

_HEADERS = {
    "User-Agent": BROWSER["context"]["user_agent"],
    "Accept": "application/json, text/html;q=0.9",
}


def fetch_json(url: str, *, timeout: int = 20) -> Any:
    ok, reason = is_safe_public_url(url)
    if not ok:
        raise requests.RequestException(reason)
    response = requests.get(url, headers=_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.json()
