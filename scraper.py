"""
Compatibility facade over sources.collect_from_url.

Prefer pipeline.collect_jobs() from operators. This module keeps the
previous scrape_career_page() name for callers and tests.
"""

from __future__ import annotations

from typing import Any

from config import SEARCH
from filters import apply_keyword_filter
from sources import AcquisitionError, CollectionRequest, collect_from_url

# Back-compat alias used by pipeline exception handling.
ScrapeError = AcquisitionError

LAST_SCRAPE_STATS: dict[str, Any] = {
    "pages_attempted": 0,
    "pages_completed": 0,
    "failure_reason": None,
    "source_kind": "html",
    "blocked": False,
}


def get_last_scrape_stats() -> dict[str, Any]:
    return dict(LAST_SCRAPE_STATS)


def scrape_career_page(
    url: str,
    *,
    company_name: str = "",
    keyword: str = "",
    max_pages: int | None = None,
) -> list[dict]:
    """
    Collect raw jobs. Keyword filtering is applied here for direct callers;
    pipeline.collect_jobs() also filters after normalisation (idempotent).
    """
    global LAST_SCRAPE_STATS
    request = CollectionRequest(
        url=url,
        company_name=company_name,
        max_pages=max_pages or SEARCH["default_max_pages"],
    )
    result = collect_from_url(request)
    LAST_SCRAPE_STATS = {
        "pages_attempted": result.pages_attempted,
        "pages_completed": result.pages_completed,
        "failure_reason": result.failure_reason,
        "source_kind": result.source_kind,
        "blocked": result.blocked,
    }
    if result.blocked or result.error:
        raise AcquisitionError(
            result.error or "Collection was blocked.",
            blocked=result.blocked,
        )
    return apply_keyword_filter(result.jobs, keyword)
