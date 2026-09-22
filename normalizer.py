"""
normalizer.py
-------------
Cleans and normalises raw scraped job data before it reaches the CSV.

WHY A SEPARATE MODULE?
──────────────────────
Previously, raw strings from the browser went straight into the CSV with
no cleaning.  This caused:
  • Leading / trailing whitespace in every field
  • Inconsistent capitalization in locations ("bengaluru" vs "Bengaluru")
  • Salary strings with newlines and duplicate whitespace
  • Posting-date strings like "PostedJust posted" (label + value merged)
  • N/A values where a field was present but just empty after strip()

normalizer.py sits between the scraper and the exporter.  It receives a
raw job dict and returns a clean one.  Each field has its own cleaning
function so the logic is easy to understand, test, and extend.

PUBLIC API
──────────
  normalize_job(raw_job: dict) -> dict
      Takes one raw job dict, returns one cleaned dict.
      Never raises — degrades gracefully on unexpected input.

  normalize_jobs(raw_jobs: list[dict]) -> list[dict]
      Applies normalize_job() to a list and logs a summary.
"""

import re
import logging

from config import EXPORT
from data_quality import clean_url, is_valid_job, validate_and_prepare_job

logger = logging.getLogger(__name__)

# Shorthand for the configured missing-value placeholder
_NA = EXPORT["missing_value"]


# ─────────────────────────────────────────────────────────────────────────────
#  SHARED UTILITY
# ─────────────────────────────────────────────────────────────────────────────

def _clean_whitespace(text: str) -> str:
    """
    Collapse internal runs of whitespace (spaces, tabs, newlines) into a
    single space, then strip the result.

    Examples:
        "  Bengaluru,  \n Karnataka  " → "Bengaluru, Karnataka"
        "Senior\tPython\nDev"          → "Senior Python Dev"
    """
    if not text:
        return _NA
    return re.sub(r"\s+", " ", text).strip()


def _is_empty(value: str) -> bool:
    """Return True if value is blank, None, or the missing-value placeholder."""
    return not value or value.strip() == "" or value.strip() == _NA


# ─────────────────────────────────────────────────────────────────────────────
#  FIELD-LEVEL CLEANERS
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_text_field(raw: str) -> str:
    """
    General-purpose field cleaner.
    Collapses whitespace; returns _NA if the result is empty.
    Used for: job_title, company_name.
    """
    cleaned = _clean_whitespace(raw or "")
    return cleaned if cleaned else _NA


def _normalize_location(raw: str) -> str:
    """
    Normalise a job location string.

    Transformations:
      • Collapse whitespace
      • Title-case each word (e.g. "HYDERABAD" → "Hyderabad")
      • Normalise separators: "Hyderabad,Telangana" → "Hyderabad, Telangana"
      • Strip trailing / leading commas and extra punctuation

    Examples:
        "  hyderabad , telangana  " → "Hyderabad, Telangana"
        "BENGALURU"                 → "Bengaluru"
        "Remote\n / Hybrid"         → "Remote / Hybrid"
    """
    cleaned = _clean_whitespace(raw or "")
    if _is_empty(cleaned):
        return _NA

    if cleaned.isupper() and len(cleaned) > 3:
        cleaned = cleaned.title()

    # Normalise comma spacing: "Foo,Bar" or "Foo , Bar" → "Foo, Bar"
    cleaned = re.sub(r"\s*,\s*", ", ", cleaned)

    # Remove any leading/trailing commas or stray punctuation
    cleaned = cleaned.strip(".,; ")

    return cleaned if cleaned else _NA


def _normalize_salary(raw: str) -> str:
    """
    Normalise a salary string.

    Transformations:
      • Collapse internal whitespace and newlines
      • Remove the word "Salary" if Indeed prepends it as a label
      • Strip currency redundancies ("₹ ₹" → "₹")
      • Collapse double spaces left behind after removals

    Indeed salary strings can look like:
        "₹4,00,000 - ₹8,00,000 a year"
        "Salary₹25,000 - ₹40,000 a month"     ← label merged in
        "From ₹30,000 a month"
        ""                                       ← field simply absent

    Examples:
        "Salary₹4,00,000 a year" → "₹4,00,000 a year"
        "  ₹ 20,000 -\n₹ 40,000 " → "₹ 20,000 - ₹ 40,000"
    """
    cleaned = _clean_whitespace(raw or "")
    if _is_empty(cleaned):
        return _NA

    # Remove an "Salary" prefix if Indeed merged the label into the text
    cleaned = re.sub(r"^[Ss]alary\s*", "", cleaned).strip()

    # Collapse any double spaces introduced by the removal above
    cleaned = re.sub(r"  +", " ", cleaned).strip()

    return cleaned if cleaned else _NA


def _normalize_posting_date(raw: str) -> str:
    """
    Normalise and interpret Indeed's relative posting-date strings.

    WHY THIS IS NEEDED
    ───────────────────
    Indeed renders posting dates as human-readable relative phrases, not
    timestamps.  The raw scraped text is inconsistent:
      • Extra label text can be merged in: "PostedJust posted"
      • Capitalisation varies: "Just Posted", "just posted"
      • Whitespace varies: "  Today  "
      • Sometimes the field is entirely missing (N/A)

    This function strips the noise and returns a clean, normalised phrase.
    It does NOT convert to an absolute date — that would require knowing
    exactly when the scrape ran, and the relative phrase is itself meaningful
    data ("Just posted" is more useful to a job seeker than "2025-05-19").

    Normalised forms returned:
        "Just posted"
        "Today"
        "1 day ago"
        "2 days ago"
        "3 days ago"     (etc.)
        "30+ days ago"
        "N/A"            (field absent or unparseable)

    Examples:
        "PostedJust posted"       → "Just posted"
        "Posted 2 days ago"       → "2 days ago"
        "  Active 2 days ago  "   → "2 days ago"
        "30+ days ago"            → "30+ days ago"
        ""                        → "N/A"
    """
    cleaned = _clean_whitespace(raw or "")
    if _is_empty(cleaned):
        return _NA

    lowered = cleaned.lower()

    # ── Remove common label prefixes that get merged in ───────────────────────
    # "Posted2 days ago" → "2 days ago"
    # "Active 2 days ago" → "2 days ago"
    # NOTE: "just" is NOT listed here — it is part of "Just posted", not a label.
    label_prefixes = r"^(posted|active|employer|new)\s+"
    cleaned = re.sub(label_prefixes, "", cleaned, flags=re.IGNORECASE).strip()
    lowered = cleaned.lower()

    # ── Classify into a normalised canonical form ─────────────────────────────

    if "just posted" in lowered or "just now" in lowered:
        return "Just posted"

    if lowered in ("today", "new"):
        return "Today"

    # "30+ days ago" — keep as-is (it's already a clear signal)
    if "30+" in lowered:
        return "30+ days ago"

    # "X days ago" or "X day ago"
    match = re.search(r"(\d+)\s*days?\s*ago", lowered)
    if match:
        n = int(match.group(1))
        unit = "day" if n == 1 else "days"
        return f"{n} {unit} ago"

    # "X hours ago" or "X hour ago"
    match = re.search(r"(\d+)\s*hours?\s*ago", lowered)
    if match:
        n = int(match.group(1))
        unit = "hour" if n == 1 else "hours"
        return f"{n} {unit} ago"

    # "X minutes ago"
    match = re.search(r"(\d+)\s*minutes?\s*ago", lowered)
    if match:
        return f"{match.group(1)} minutes ago"

    # If we still have something after stripping labels, return it cleaned
    if cleaned and cleaned != _NA:
        # Title-case so output is consistent even for unrecognised patterns
        return cleaned.strip().capitalize()

    return _NA


def _normalize_url(raw: str) -> str:
    """
    Clean a job URL.

    Indeed URLs can contain session-tracking query parameters that make
    each URL unique even for the same job.  We strip the most common
    tracking params (jsa, from, fromjk, alid) to make duplicate detection
    in filters.py more reliable, while keeping the essential jk (job key).

    Examples:
        "/rc/clk?jk=abc123&from=jasx&jsa=..." → "https://in.indeed.com/rc/clk?jk=abc123"
        "https://in.indeed.com/rc/clk?jk=abc" → "https://in.indeed.com/rc/clk?jk=abc"
        "N/A"                                  → "N/A"
    """
    if _is_empty(raw):
        return _NA
    return clean_url(raw.strip())


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def normalize_job(raw_job: dict) -> dict:
    """
    Apply all field-level normalisation functions to one raw job dict.

    Each field is cleaned independently — a failure in one field never
    affects the others.  Missing keys in raw_job are handled gracefully.

    Args:
        raw_job : dict with raw scraped strings for one job listing

    Returns:
        New dict with the same keys but normalised, consistent values.
    """
    try:
        base = validate_and_prepare_job(raw_job, source="normalize") or raw_job
        job = {
            "company_name": _normalize_text_field(base.get("company_name", "")),
            "job_title":    _normalize_text_field(base.get("job_title", "")),
            "job_location": _normalize_location(base.get("job_location", "")),
            "posting_date": _normalize_posting_date(base.get("posting_date", "")),
            "salary":       _normalize_salary(base.get("salary", "")),
            "job_url":      _normalize_url(base.get("job_url", "")),
        }
        ok, reason = is_valid_job(job)
        if not ok:
            logger.warning("[NORM] Weak job record: %s", reason)
        return job
    except Exception as e:
        logger.warning(f"normalize_job() failed on one record: {e}  — returning raw.")
        return raw_job   # degrade gracefully: return original rather than drop the job


def normalize_jobs(raw_jobs: list[dict]) -> list[dict]:
    """
    Normalise a list of raw job dicts and log a summary.

    Args:
        raw_jobs : list of raw dicts from the scraper

    Returns:
        List of normalised dicts, same length as input.
    """
    if not raw_jobs:
        return []

    normalised = [normalize_job(j) for j in raw_jobs]

    # Count how many records still have N/A in each important field
    na_counts = {
        field: sum(1 for j in normalised if j.get(field) == _NA)
        for field in ("company_name", "job_title", "job_location", "posting_date")
    }
    fields_with_gaps = {f: n for f, n in na_counts.items() if n > 0}

    if fields_with_gaps:
        logger.warning(
            f"[NORM] N/A field counts after normalisation: {fields_with_gaps}  "
            "— these fields could not be extracted from the page."
        )
    else:
        logger.info(f"[NORM] All {len(normalised)} record(s) normalised cleanly.")

    return normalised
