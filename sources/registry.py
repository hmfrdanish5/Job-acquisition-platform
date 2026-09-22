"""Pick an adapter for a URL and run collection."""

from __future__ import annotations

from sources.ashby import AshbySource
from sources.base import CollectionRequest, CollectionResult, JobSource
from sources.greenhouse import GreenhouseSource
from sources.html import HtmlSource
from sources.lever import LeverSource
from sources.smartrecruiters import SmartRecruitersSource

_SPECIALIZED: tuple[JobSource, ...] = (
    GreenhouseSource(),
    LeverSource(),
    AshbySource(),
    SmartRecruitersSource(),
)
_HTML = HtmlSource()


def list_adapters() -> list[JobSource]:
    return [*_SPECIALIZED, _HTML]


def select_source(url: str) -> JobSource:
    for adapter in _SPECIALIZED:
        if adapter.matches(url):
            return adapter
    return _HTML


def collect_from_url(request: CollectionRequest) -> CollectionResult:
    """
    Run the matching adapter. If a specialized ATS returns no jobs and is
    not blocked, fall back to HTML — same behaviour as the previous scraper.
    """
    adapter = select_source(request.url)
    result = adapter.collect(request)
    if (
        adapter.name != _HTML.name
        and not result.jobs
        and not result.blocked
        and not result.error
    ):
        html_result = _HTML.collect(request)
        if html_result.jobs or html_result.blocked or html_result.error:
            html_result.failure_reason = html_result.failure_reason or (
                f"{adapter.name}_empty_fallback_html"
            )
            return html_result
    return result
