"""In-run duplicate removal."""

import logging

from job_store import build_dedupe_key

logger = logging.getLogger(__name__)


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
