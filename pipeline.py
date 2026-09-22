"""
Scrape → clean → persist → CSV.

Used by the web app and the command-line entry point.
"""

from __future__ import annotations

import logging
from typing import Any

from config import DATABASE, EXPORT, SEARCH
from exporter import export_to_csv
from filters import remove_duplicates
from job_store import JobStore
from normalizer import normalize_jobs
from scraper import ScrapeError, get_last_scrape_stats, scrape_career_page
from url_safety import is_safe_public_url

logger = logging.getLogger(__name__)

SOURCE_NAME = "careers"
SOURCE_LABEL = "Company careers page"


def collect_jobs(
    url: str,
    *,
    company_name: str = "",
    keyword: str = "",
    max_pages: int | None = None,
    export_path: str | None = None,
    store: JobStore | None = None,
) -> dict[str, Any]:
    """
    Run a full collection and return a JSON-serialisable result dict.
    """
    ok, reason = is_safe_public_url(url)
    if not ok:
        return {
            "ok": False,
            "blocked": False,
            "message": reason,
            "jobs": [],
            "run_id": None,
            "new_count": 0,
            "exported": False,
        }

    pages = max_pages or SEARCH["default_max_pages"]
    use_store = DATABASE.get("enabled", True)
    store = store if store is not None else (JobStore() if use_store else None)
    run_id = None
    scrape_stats = {}

    if store:
        run_id = store.start_run(
            SOURCE_NAME,
            keyword=keyword or "(all)",
            location=url.strip(),
            max_pages=pages,
            display_name=SOURCE_LABEL,
        )

    try:
        raw_jobs = scrape_career_page(
            url.strip(),
            company_name=company_name,
            keyword=keyword,
            max_pages=pages,
        )
        scrape_stats = get_last_scrape_stats()
        cleaned = normalize_jobs(raw_jobs)
        unique = remove_duplicates(cleaned)

        if company_name.strip():
            hint = company_name.strip()
            for job in unique:
                if not job.get("company_name") or job["company_name"] == EXPORT["missing_value"]:
                    job["company_name"] = hint

        persist_stats = {"new": 0, "seen_again": 0, "total": 0}
        if store and run_id is not None:
            persist_stats = store.persist_jobs(run_id, SOURCE_NAME, unique)

        exported = False
        output = export_path or EXPORT["output_file"]
        if unique:
            exported = export_to_csv(unique, output_path=output)

        if store and run_id is not None:
            store.finish_run(
                run_id,
                status="completed" if unique else "failed",
                raw_count=len(raw_jobs),
                unique_count=len(unique),
                new_count=persist_stats.get("new", 0),
                exported_count=len(unique) if exported else 0,
                failure_reason=None if unique else "no jobs found",
                pages_attempted=scrape_stats.get("pages_attempted"),
                pages_completed=scrape_stats.get("pages_completed"),
                notes=scrape_stats.get("source_kind"),
            )

        if not unique:
            return {
                "ok": False,
                "blocked": False,
                "message": (
                    "No job listings were found on that page. "
                    "Use the company's careers or jobs URL, not the homepage, "
                    "or a Greenhouse / Lever / Ashby board link."
                ),
                "jobs": [],
                "run_id": run_id,
                "new_count": 0,
                "exported": False,
                "source_kind": scrape_stats.get("source_kind"),
            }

        return {
            "ok": True,
            "blocked": False,
            "message": f"Found {len(unique)} job listing(s).",
            "jobs": unique,
            "run_id": run_id,
            "new_count": persist_stats.get("new", 0),
            "exported": exported,
            "export_path": output if exported else None,
            "source_kind": scrape_stats.get("source_kind"),
        }

    except ScrapeError as exc:
        scrape_stats = get_last_scrape_stats()
        logger.warning("Collection stopped: %s", exc)
        if store and run_id is not None:
            store.finish_run(
                run_id,
                status="failed",
                failure_reason=str(exc),
                notes=str(exc),
                pages_attempted=scrape_stats.get("pages_attempted"),
                pages_completed=scrape_stats.get("pages_completed"),
            )
        return {
            "ok": False,
            "blocked": exc.blocked,
            "message": str(exc),
            "jobs": [],
            "run_id": run_id,
            "new_count": 0,
            "exported": False,
        }
    except Exception as exc:
        scrape_stats = get_last_scrape_stats()
        logger.exception("Collection failed")
        if store and run_id is not None:
            store.finish_run(
                run_id,
                status="failed",
                failure_reason=str(exc),
                notes=str(exc),
                pages_attempted=scrape_stats.get("pages_attempted"),
                pages_completed=scrape_stats.get("pages_completed"),
            )
        return {
            "ok": False,
            "blocked": False,
            "message": f"Something went wrong while collecting jobs: {exc}",
            "jobs": [],
            "run_id": run_id,
            "new_count": 0,
            "exported": False,
        }
