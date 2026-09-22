"""SmartRecruiters public Posting API (no authentication)."""

from __future__ import annotations

import logging

import requests

from extractors import detect_ats, jobs_from_smartrecruiters_payload
from sources.base import CollectionRequest, CollectionResult
from sources.http_client import fetch_json

logger = logging.getLogger(__name__)

_PAGE_SIZE = 100
_MAX_PAGES = 5


class SmartRecruitersSource:
    name = "smartrecruiters"
    display_name = "SmartRecruiters"

    def matches(self, url: str) -> bool:
        kind, slug = detect_ats(url)
        return kind == "smartrecruiters" and bool(slug)

    def collect(self, request: CollectionRequest) -> CollectionResult:
        _kind, slug = detect_ats(request.url)
        result = CollectionResult(source_kind=self.name)
        if not slug:
            result.failure_reason = "missing SmartRecruiters company identifier"
            return result

        jobs: list[dict] = []
        offset = 0
        for page_index in range(_MAX_PAGES):
            result.pages_attempted = page_index + 1
            api = (
                f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
                f"?limit={_PAGE_SIZE}&offset={offset}"
            )
            try:
                payload = fetch_json(api)
            except requests.RequestException as exc:
                logger.warning("[ATS] smartrecruiters API failed: %s", exc)
                result.failure_reason = f"smartrecruiters api: {exc}"
                break
            batch = jobs_from_smartrecruiters_payload(payload, request.company_name)
            result.pages_completed = page_index + 1
            if not batch:
                break
            jobs.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE

        result.jobs = jobs
        logger.info("[ATS] smartrecruiters/%s — %s job(s)", slug, len(jobs))
        return result
