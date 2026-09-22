"""
data_quality.py  —  v2.1
------------------------
Lightweight validation and normalization guards for job records.
Used by imports, normalizer, and deduplication helpers.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

from config import EXPORT

logger = logging.getLogger(__name__)

_NA = EXPORT["missing_value"]
_CSV_COLUMNS = EXPORT["csv_columns"]
_TRACKING_PARAMS = frozenset(
    {"jsa", "from", "fromjk", "alid", "tk", "sjdu", "vjs", "vjk", "advn"}
)


def clean_url(url: str) -> str:
    """Normalize job URL: strip tracking params, fix dangling punctuation."""
    if not url or url.strip() in ("", _NA):
        return _NA

    raw = url.strip()
    if raw.startswith("/") and not raw.startswith("//"):
        return _NA

    try:
        parsed = urlparse(raw)
    except Exception:
        return _NA

    if not parsed.scheme or not parsed.netloc:
        return _NA

    query = parse_qs(parsed.query, keep_blank_values=False)
    kept = []
    for key, values in query.items():
        if key.lower() not in _TRACKING_PARAMS and values:
            kept.append(f"{key}={values[0]}")

    new_query = "&".join(sorted(kept))
    cleaned = urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, "")
    )
    cleaned = cleaned.rstrip("?&")
    return cleaned if cleaned else _NA


def _clean_text(value: Any, max_len: int = 500) -> str:
    if value is None:
        return _NA
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text or text == _NA:
        return _NA
    if len(text) > max_len:
        text = text[: max_len - 3] + "..."
    return text


def is_valid_job(job: dict) -> tuple[bool, str]:
    """
    Return (ok, reason). A job must have at least title or company.
    """
    title = job.get("job_title", _NA)
    company = job.get("company_name", _NA)
    if title == _NA and company == _NA:
        return False, "missing title and company"
    return True, ""


def validate_and_prepare_job(raw: dict, *, source: str = "row") -> dict | None:
    """
    Validate a raw job dict (CSV row or scraper output).
    Returns a cleaned dict ready for normalize_job(), or None if unusable.
    """
    try:
        job = {col: _clean_text(raw.get(col, _NA)) for col in _CSV_COLUMNS}
        job["job_url"] = clean_url(str(raw.get("job_url", _NA)))

        ok, reason = is_valid_job(job)
        if not ok:
            logger.debug("[QUALITY] Rejected %s: %s", source, reason)
            return None
        return job
    except Exception as exc:
        logger.warning("[QUALITY] Malformed %s: %s", source, exc)
        return None


def validate_csv_row(row: dict, line_no: int) -> dict | None:
    """Map a CSV DictReader row to a validated job dict."""
    normalized_row = {
        col: row.get(col, row.get(col.replace("_", " "), _NA))
        for col in _CSV_COLUMNS
    }
    return validate_and_prepare_job(normalized_row, source=f"csv line {line_no}")
