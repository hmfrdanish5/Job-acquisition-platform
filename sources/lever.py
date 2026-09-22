"""Lever public postings API."""

from __future__ import annotations

import logging

import requests

from extractors import detect_ats, jobs_from_lever_payload
from sources.base import CollectionRequest, CollectionResult
from sources.http_client import fetch_json

logger = logging.getLogger(__name__)


class LeverSource:
    name = "lever"
    display_name = "Lever"

    def matches(self, url: str) -> bool:
        kind, slug = detect_ats(url)
        return kind == "lever" and bool(slug)

    def collect(self, request: CollectionRequest) -> CollectionResult:
        _kind, slug = detect_ats(request.url)
        result = CollectionResult(source_kind=self.name, pages_attempted=1)
        if not slug:
            result.failure_reason = "missing lever company slug"
            return result
        api = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        try:
            payload = fetch_json(api)
        except requests.RequestException as exc:
            logger.warning("[ATS] lever API failed: %s", exc)
            result.failure_reason = f"lever api: {exc}"
            return result
        result.jobs = jobs_from_lever_payload(payload, request.company_name)
        result.pages_completed = 1
        logger.info("[ATS] lever/%s — %s job(s)", slug, len(result.jobs))
        return result
