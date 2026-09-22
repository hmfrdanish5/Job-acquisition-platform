"""
Scrape → clean → persist → optional CSV.

Operators and the dashboard call collect_jobs / collect_all.
Adapters live under sources/; this module does not import Greenhouse/Lever/etc.
"""

from __future__ import annotations

import logging
from typing import Any

from config import DATABASE, EXPORT, SEARCH
from exporter import export_to_csv
from filters import apply_keyword_filter, remove_duplicates
from job_store import JOB_CATALOG_SOURCE, JobStore
from normalizer import normalize_jobs
from scraper import ScrapeError, get_last_scrape_stats, scrape_career_page

logger = logging.getLogger(__name__)

SOURCE_LABEL = "Company careers page"

STATUS_COMPLETED = "completed"
STATUS_EXPORT_FAILED = "export_failed"
STATUS_EMPTY = "empty"
STATUS_FAILED = "failed"
STATUS_IMPORTED = "imported"
STATUS_SKIPPED = "skipped"


def collect_jobs(
    url: str,
    *,
    company_name: str = "",
    keyword: str = "",
    max_pages: int | None = None,
    export_path: str | None = None,
    write_export: bool = True,
    store: JobStore | None = None,
    target_key: str | None = None,
) -> dict[str, Any]:
    """
    Run a full collection and return a JSON-serialisable result dict.

    Run statuses:
      completed      jobs persisted and CSV export succeeded (or export skipped)
      export_failed  jobs persisted, CSV write failed
      empty          scrape succeeded, zero listings
      failed         blocked page, unsafe URL, or exception
    """
    from url_safety import is_safe_public_url
    from sources.registry import select_source

    ok, reason = is_safe_public_url(url)
    if not ok:
        return _result(
            ok=False,
            status=STATUS_FAILED,
            message=reason,
        )

    pages = max_pages or SEARCH["default_max_pages"]
    use_store = DATABASE.get("enabled", True)
    store = store if store is not None else (JobStore() if use_store else None)
    run_id = None
    adapter_kind = select_source(url).name
    scrape_stats: dict[str, Any] = {}

    if store:
        run_id = store.start_run(
            JOB_CATALOG_SOURCE,
            keyword=keyword or "(all)",
            location=url.strip(),
            max_pages=pages,
            display_name=SOURCE_LABEL,
            target_key=target_key,
            adapter_kind=adapter_kind,
        )

    try:
        raw_jobs = scrape_career_page(
            url.strip(),
            company_name=company_name,
            keyword="",
            max_pages=pages,
        )
        scrape_stats = get_last_scrape_stats()
        adapter_kind = scrape_stats.get("source_kind") or adapter_kind
        cleaned = normalize_jobs(raw_jobs)
        unique = remove_duplicates(cleaned)
        unique = apply_keyword_filter(unique, keyword)

        if company_name.strip():
            hint = company_name.strip()
            for job in unique:
                if not job.get("company_name") or job["company_name"] == EXPORT["missing_value"]:
                    job["company_name"] = hint

        persist_stats = {"new": 0, "seen_again": 0, "total": 0}
        if store and run_id is not None:
            persist_stats = store.persist_jobs(run_id, JOB_CATALOG_SOURCE, unique)

        exported = False
        output = export_path or EXPORT["output_file"]
        export_error = None
        if unique and write_export:
            exported = export_to_csv(unique, output_path=output)
            if not exported:
                export_error = "csv export failed"

        if unique and write_export and not exported:
            status = STATUS_EXPORT_FAILED
            failure_reason = export_error
            message = f"Saved {len(unique)} job(s) but CSV export failed."
            ok_flag = True
        elif unique:
            status = STATUS_COMPLETED
            failure_reason = None
            message = f"Found {len(unique)} job listing(s)."
            ok_flag = True
        else:
            status = STATUS_EMPTY
            failure_reason = None
            message = (
                "No job listings were found on that page. "
                "Use the company's careers or jobs URL, not the homepage, "
                "or a Greenhouse / Lever / Ashby / SmartRecruiters board link."
            )
            ok_flag = False

        if store and run_id is not None:
            store.finish_run(
                run_id,
                status=status,
                raw_count=len(raw_jobs),
                unique_count=len(unique),
                new_count=persist_stats.get("new", 0),
                exported_count=len(unique) if exported else 0,
                failure_reason=failure_reason,
                pages_attempted=scrape_stats.get("pages_attempted"),
                pages_completed=scrape_stats.get("pages_completed"),
                notes=adapter_kind if status != STATUS_EMPTY else "no jobs found",
            )

        return _result(
            ok=ok_flag,
            status=status,
            message=message,
            jobs=unique,
            run_id=run_id,
            new_count=persist_stats.get("new", 0),
            exported=exported,
            export_path=output if exported else None,
            source_kind=adapter_kind,
            blocked=False,
        )

    except ScrapeError as exc:
        scrape_stats = get_last_scrape_stats()
        logger.warning("Collection stopped: %s", exc)
        reason = str(exc)
        if exc.blocked and not reason.lower().startswith("blocked"):
            reason = f"blocked: {exc}"
        if store and run_id is not None:
            store.finish_run(
                run_id,
                status=STATUS_FAILED,
                failure_reason=reason,
                notes=str(exc),
                pages_attempted=scrape_stats.get("pages_attempted"),
                pages_completed=scrape_stats.get("pages_completed"),
            )
        return _result(
            ok=False,
            status=STATUS_FAILED,
            message=str(exc),
            run_id=run_id,
            blocked=exc.blocked,
            source_kind=scrape_stats.get("source_kind"),
        )
    except Exception as exc:
        scrape_stats = get_last_scrape_stats()
        logger.exception("Collection failed")
        if store and run_id is not None:
            store.finish_run(
                run_id,
                status=STATUS_FAILED,
                failure_reason=str(exc),
                notes=str(exc),
                pages_attempted=scrape_stats.get("pages_attempted"),
                pages_completed=scrape_stats.get("pages_completed"),
            )
        return _result(
            ok=False,
            status=STATUS_FAILED,
            message=f"Something went wrong while collecting jobs: {exc}",
            run_id=run_id,
            source_kind=scrape_stats.get("source_kind"),
        )


def collect_target(
    target_id: str,
    *,
    store: JobStore | None = None,
    write_export: bool = True,
    max_pages: int | None = None,
) -> dict[str, Any]:
    from targets import get_target

    target = get_target(target_id)
    if target is None:
        return _result(
            ok=False,
            status=STATUS_FAILED,
            message=f"Unknown target '{target_id}'.",
        )
    err = target.validation_error()
    if err:
        return _result(ok=False, status=STATUS_FAILED, message=err)
    return collect_jobs(
        target.url,
        company_name=target.company,
        keyword=target.keywords,
        max_pages=max_pages,
        write_export=write_export,
        store=store,
        target_key=target.id,
    )


def collect_all(
    *,
    store: JobStore | None = None,
    write_export: bool = False,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """
    Collect every enabled target sequentially.
    One failure does not stop the batch.
    """
    from targets import load_targets

    store = store if store is not None else JobStore()
    configured = load_targets(enabled_only=False)
    enabled = [t for t in configured if t.enabled]
    rows: list[dict[str, Any]] = []

    for target in enabled:
        result = collect_jobs(
            target.url,
            company_name=target.company,
            keyword=target.keywords,
            max_pages=max_pages,
            write_export=write_export,
            store=store,
            target_key=target.id,
        )
        rows.append(
            {
                "target_id": target.id,
                "target_name": target.name,
                "status": result.get("status"),
                "ok": result.get("ok"),
                "jobs": len(result.get("jobs") or []),
                "new_count": result.get("new_count") or 0,
                "run_id": result.get("run_id"),
                "message": result.get("message"),
                "blocked": result.get("blocked"),
            }
        )

    successful = sum(
        1 for r in rows if r["status"] in (STATUS_COMPLETED, STATUS_EXPORT_FAILED)
    )
    failed = sum(1 for r in rows if r["status"] == STATUS_FAILED)
    empty = sum(1 for r in rows if r["status"] == STATUS_EMPTY)
    new_jobs = sum(int(r["new_count"]) for r in rows)
    return {
        "rows": rows,
        "targets": len(enabled),
        "skipped_disabled": len(configured) - len(enabled),
        "successful": successful,
        "failed": failed,
        "empty": empty,
        "new_jobs": new_jobs,
    }


def _result(
    *,
    ok: bool,
    status: str,
    message: str,
    jobs: list | None = None,
    run_id: int | None = None,
    new_count: int = 0,
    exported: bool = False,
    export_path: str | None = None,
    source_kind: str | None = None,
    blocked: bool = False,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "status": status,
        "blocked": blocked,
        "message": message,
        "jobs": jobs or [],
        "run_id": run_id,
        "new_count": new_count,
        "exported": exported,
        "export_path": export_path,
        "source_kind": source_kind,
    }
