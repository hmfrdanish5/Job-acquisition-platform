"""Acquisition adapters. Pipeline depends on this package, not ATS internals."""

from __future__ import annotations

from sources.base import CollectionRequest, CollectionResult, JobSource
from sources.errors import AcquisitionError
from sources.registry import collect_from_url, list_adapters, select_source

__all__ = [
    "AcquisitionError",
    "CollectionRequest",
    "CollectionResult",
    "JobSource",
    "collect_from_url",
    "list_adapters",
    "select_source",
]
