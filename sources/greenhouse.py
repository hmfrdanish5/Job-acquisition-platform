"""Greenhouse public board API."""

from __future__ import annotations

import logging

import requests

from extractors import detect_ats, jobs_from_greenhouse_payload
from sources.base import CollectionRequest, CollectionResult
from sources.http_client import fetch_json

logger = logging.getLogger(__name__)


class GreenhouseSource:
    name = "greenhouse"
    display_name = "Greenhouse"

    def matches(self, url: str) -> bool:
        kind, slug = detect_ats(url)
        return kind == "greenhouse" and bool(slug)

    def collect(self, request: CollectionRequest) -> CollectionResult:
        _kind, slug = detect_ats(request.url)
        result = CollectionResult(source_kind=self.name, pages_attempted=1)
        if not slug:
            result.failure_reason = "missing greenhouse board slug"
            return result
        api = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        try:
            payload = fetch_json(api)
        except requests.RequestException as exc:
            logger.warning("[ATS] greenhouse API failed: %s", exc)
            result.failure_reason = f"greenhouse api: {exc}"
            return result
        result.jobs = jobs_from_greenhouse_payload(payload, request.company_name)
        result.pages_completed = 1
        logger.info("[ATS] greenhouse/%s — %s job(s)", slug, len(result.jobs))
        return result
