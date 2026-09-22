"""Acquisition contract. Keep this small — one request, one result, one adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class CollectionRequest:
    url: str
    company_name: str = ""
    max_pages: int = 3


@dataclass
class CollectionResult:
    jobs: list[dict] = field(default_factory=list)
    source_kind: str = "html"
    pages_attempted: int = 0
    pages_completed: int = 0
    blocked: bool = False
    error: str | None = None
    failure_reason: str | None = None


class JobSource(Protocol):
    """One acquisition adapter.

    matches(url)  — whether this adapter should handle the URL
    collect(...)  — fetch raw job dicts in the shared field shape
    """

    name: str
    display_name: str

    def matches(self, url: str) -> bool: ...

    def collect(self, request: CollectionRequest) -> CollectionResult: ...
