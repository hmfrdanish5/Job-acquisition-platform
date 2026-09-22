"""In-run duplicate removal and keyword matching."""

import logging

from job_store import build_dedupe_key

logger = logging.getLogger(__name__)


def parse_keywords(keyword: str) -> list[str]:
    """
    Comma-separated OR terms, trimmed, case-insensitive, de-duplicated.

    Empty input means no filtering. A string with no commas is one term.
    """
    raw = (keyword or "").strip()
    if not raw:
        return []
    seen: set[str] = set()
    terms: list[str] = []
    for part in raw.split(","):
        term = part.strip().lower()
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def job_matches_keywords(job: dict, terms: list[str]) -> bool:
    if not terms:
        return True
    hay = " ".join(
        [
            str(job.get("job_title") or ""),
            str(job.get("job_location") or ""),
            str(job.get("company_name") or ""),
        ]
    ).lower()
    return any(term in hay for term in terms)


def apply_keyword_filter(jobs: list[dict], keyword: str) -> list[dict]:
    terms = parse_keywords(keyword)
    if not terms:
        return jobs
    kept = [job for job in jobs if job_matches_keywords(job, terms)]
    logger.info(
        "[FILTER] keyword terms %s kept %s/%s job(s)",
        terms,
        len(kept),
        len(jobs),
    )
    return kept


def remove_duplicates(jobs: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for job in jobs:
        key = build_dedupe_key(job)
        if key in seen:
            continue
        seen.add(key)
        unique.append(job)

    removed = len(jobs) - len(unique)
    if removed:
        logger.info("Removed %s duplicate job(s).", removed)
    return unique
