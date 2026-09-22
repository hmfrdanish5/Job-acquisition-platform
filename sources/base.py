"""Minimal source contract."""

from __future__ import annotations

from typing import Protocol


class JobSource(Protocol):
    name: str
    display_name: str
