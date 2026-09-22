"""
job_store.py  —  v2.1
---------------------
High-level persistence API: scrape runs, deduplicated jobs, queries, CSV import.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import DATABASE, EXPORT
from database import Database, utc_now

logger = logging.getLogger(__name__)

_NA = EXPORT["missing_value"]

# Canonical job catalog. Live collections persist here so historical
# careers rows keep matching on (source_id, dedupe_key). Adapter kind
# is recorded on the scrape run, not by splitting the jobs table.
JOB_CATALOG_SOURCE = "careers"

# Operator-friendly source aliases
SOURCE_ALIASES: dict[str, str] = {
    "careers": "careers",
    "career": "careers",
    "company": "careers",
    "html": "careers",
    "greenhouse": "greenhouse",
    "lever": "lever",
    "ashby": "ashby",
    "smartrecruiters": "smartrecruiters",
    "csv": "csv",
}

MERGE_FIELDS = (
    "company_name",
    "job_title",
    "job_location",
    "posting_date",
    "salary",
    "job_url",
)


def is_missing_value(value: Any) -> bool:
    """True when a field carries no usable information."""
    if value is None:
        return True
    text = str(value).strip()
    return text == "" or text.upper() == _NA.upper()


def choose_better_value(existing: Any, incoming: Any) -> Any:
    """
    Field merge policy (deterministic):

    1. Incoming missing (None / blank / N/A) never overwrites a stored value.
    2. Existing missing + incoming present => accept incoming.
    3. Both present => incoming wins (latest observation).
    4. Both missing => keep existing (do not invent a placeholder).
    """
    if is_missing_value(incoming):
        return existing
    if is_missing_value(existing):
        return incoming
    return incoming


def resolve_source_name(name: str) -> str:
    key = name.strip().lower()
    return SOURCE_ALIASES.get(key, key)


def build_dedupe_key(job: dict) -> str:
    """
    Stable key for cross-run deduplication.
    Prefer normalized URL; fall back to title + company.
    """
    url = (job.get("job_url") or "").strip()
    if url and url != _NA:
        return f"url:{url}"

    title = (job.get("job_title") or "").strip().lower()
    company = (job.get("company_name") or "").strip().lower()
    return f"tc:{title}|{company}"


def file_fingerprint(path: Path) -> str:
    """Hash path + size + mtime so the same file is not imported twice."""
    stat = path.stat()
    payload = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    return hashlib.sha256(payload.encode()).hexdigest()


class JobStore:
    """Operator-facing persistence layer over SQLite."""

    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()
        if DATABASE.get("auto_init", True):
            self.db.initialize()

    def start_run(
        self,
        source_name: str,
        *,
        keyword: str,
        location: str,
        max_pages: int,
        display_name: str | None = None,
        target_key: str | None = None,
        adapter_kind: str | None = None,
    ) -> int:
        source_name = resolve_source_name(source_name)
        source_id = self.db.ensure_source(source_name, display_name)
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO scrape_runs (
                    source_id, keyword, location, max_pages,
                    started_at, status,
                    pages_attempted, pages_completed,
                    target_key, adapter_kind
                ) VALUES (?, ?, ?, ?, ?, 'running', 0, 0, ?, ?)
                """,
                (
                    source_id,
                    keyword,
                    location,
                    max_pages,
                    utc_now(),
                    target_key,
                    adapter_kind,
                ),
            )
            run_id = int(cur.lastrowid)
        logger.info(
            "[STORE] Started run id=%s source=%s adapter=%s target=%s",
            run_id,
            source_name,
            adapter_kind,
            target_key,
        )
        return run_id

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        raw_count: int = 0,
        unique_count: int = 0,
        new_count: int = 0,
        exported_count: int = 0,
        notes: str | None = None,
        failure_reason: str | None = None,
        pages_attempted: int | None = None,
        pages_completed: int | None = None,
    ) -> None:
        finished = utc_now()
        duration_seconds = None

        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT started_at FROM scrape_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row and row["started_at"]:
                try:
                    started = datetime.fromisoformat(row["started_at"])
                    ended = datetime.fromisoformat(finished)
                    duration_seconds = round((ended - started).total_seconds(), 2)
                except ValueError:
                    pass

            resolved_failure = failure_reason
            if resolved_failure is None and status in ("failed", "export_failed"):
                resolved_failure = notes

            updates = {
                "finished_at": finished,
                "status": status,
                "raw_count": raw_count,
                "unique_count": unique_count,
                "new_count": new_count,
                "exported_count": exported_count,
                "notes": notes,
                "failure_reason": resolved_failure,
                "duration_seconds": duration_seconds,
            }
            if pages_attempted is not None:
                updates["pages_attempted"] = pages_attempted
            if pages_completed is not None:
                updates["pages_completed"] = pages_completed

            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE scrape_runs SET {set_clause} WHERE id = ?",
                (*updates.values(), run_id),
            )

        logger.info(
            "[STORE] Finished run id=%s status=%s duration=%ss new=%s",
            run_id,
            status,
            duration_seconds,
            new_count,
        )

    def persist_jobs(
        self,
        run_id: int,
        source_name: str,
        jobs: list[dict],
    ) -> dict[str, int]:
        if not jobs:
            return {"total": 0, "new": 0, "seen_again": 0}

        source_name = resolve_source_name(source_name)
        source_id = self.db.ensure_source(source_name)
        now = utc_now()
        new_count = 0
        seen_again = 0
        processed = 0

        with self.db.connect() as conn:
            seen_in_batch: set[str] = set()
            for job in jobs:
                dedupe_key = build_dedupe_key(job)
                if dedupe_key in seen_in_batch:
                    logger.debug(
                        "[STORE] Skipping duplicate in same batch: %s", dedupe_key[:40]
                    )
                    continue
                seen_in_batch.add(dedupe_key)
                existing = conn.execute(
                    """
                    SELECT id, times_seen, company_name, job_title, job_location,
                           posting_date, salary, job_url
                    FROM jobs
                    WHERE source_id = ? AND dedupe_key = ?
                    """,
                    (source_id, dedupe_key),
                ).fetchone()

                if existing:
                    job_id = int(existing["id"])
                    times_seen = int(existing["times_seen"]) + 1
                    merged = {
                        field: choose_better_value(existing[field], job.get(field))
                        for field in MERGE_FIELDS
                    }
                    conn.execute(
                        """
                        UPDATE jobs
                        SET company_name = ?, job_title = ?, job_location = ?,
                            posting_date = ?, salary = ?, job_url = ?,
                            last_seen_at = ?, times_seen = ?
                        WHERE id = ?
                        """,
                        (
                            merged["company_name"],
                            merged["job_title"],
                            merged["job_location"],
                            merged["posting_date"],
                            merged["salary"],
                            merged["job_url"],
                            now,
                            times_seen,
                            job_id,
                        ),
                    )
                    is_new = 0
                    seen_again += 1
                else:
                    cur = conn.execute(
                        """
                        INSERT INTO jobs (
                            source_id, dedupe_key,
                            company_name, job_title, job_location,
                            posting_date, salary, job_url,
                            first_seen_at, last_seen_at, times_seen
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            source_id,
                            dedupe_key,
                            job.get("company_name"),
                            job.get("job_title"),
                            job.get("job_location"),
                            job.get("posting_date"),
                            job.get("salary"),
                            job.get("job_url"),
                            now,
                            now,
                        ),
                    )
                    job_id = int(cur.lastrowid)
                    is_new = 1
                    new_count += 1

                conn.execute(
                    """
                    INSERT INTO run_jobs (run_id, job_id, is_new)
                    VALUES (?, ?, ?)
                    ON CONFLICT(run_id, job_id) DO UPDATE SET is_new = excluded.is_new
                    """,
                    (run_id, job_id, is_new),
                )
                processed += 1

        return {"total": processed, "new": new_count, "seen_again": seen_again}

    def is_csv_imported(self, fingerprint: str) -> bool:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT id FROM csv_imports WHERE file_fingerprint = ?",
                (fingerprint,),
            ).fetchone()
        return row is not None

    def clear_csv_import_record(self, fingerprint: str) -> None:
        """Remove import fingerprint (used by --force re-import)."""
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM csv_imports WHERE file_fingerprint = ?",
                (fingerprint,),
            )

    def record_csv_import(
        self,
        *,
        file_path: str,
        fingerprint: str,
        source_name: str,
        run_id: int,
        summary: dict[str, int],
    ) -> None:
        source_id = self.db.ensure_source(resolve_source_name(source_name))
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO csv_imports (
                    file_path, file_fingerprint, source_id, run_id,
                    imported_at, rows_read, jobs_imported,
                    jobs_duplicate, rows_skipped, rows_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_fingerprint) DO UPDATE SET
                    file_path = excluded.file_path,
                    run_id = excluded.run_id,
                    imported_at = excluded.imported_at,
                    rows_read = excluded.rows_read,
                    jobs_imported = excluded.jobs_imported,
                    jobs_duplicate = excluded.jobs_duplicate,
                    rows_skipped = excluded.rows_skipped,
                    rows_error = excluded.rows_error
                """,
                (
                    file_path,
                    fingerprint,
                    source_id,
                    run_id,
                    utc_now(),
                    summary.get("rows_read", 0),
                    summary.get("imported", 0),
                    summary.get("duplicates", 0),
                    summary.get("skipped", 0),
                    summary.get("errors", 0),
                ),
            )

    def source_exists(self, name: str) -> bool:
        resolved = resolve_source_name(name)
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM sources WHERE name = ?", (resolved,)
            ).fetchone()
        return row is not None

    def get_operational_health(self) -> dict[str, Any]:
        """Combined DB health + operational counters for ops_cli health."""
        db_health = self.db.health_check()
        stats = self.get_database_stats() if db_health["reachable"] else {}
        last = stats.get("last_run") if stats else None

        healthy = (
            db_health["reachable"]
            and db_health["tables_ok"]
            and db_health.get("schema_version") == db_health.get("expected_version")
        )

        return {
            "healthy": healthy,
            "database": db_health,
            "jobs_total": stats.get("jobs_total", 0),
            "runs_total": stats.get("runs_total", 0),
            "last_run_status": last.get("status") if last else None,
            "last_run_id": last.get("id") if last else None,
        }

    def import_jobs_from_list(
        self,
        source_name: str,
        jobs: list[dict],
        *,
        file_path: str = "manual",
    ) -> dict[str, int]:
        """
        Import jobs under a dedicated csv_import run.
        Returns summary: imported, duplicates, skipped, errors, rows_read.
        """
        source_name = resolve_source_name(source_name)
        summary = {
            "rows_read": len(jobs),
            "imported": 0,
            "duplicates": 0,
            "skipped": 0,
            "errors": 0,
        }

        run_id = self.start_run(
            source_name,
            keyword="[csv_import]",
            location=file_path,
            max_pages=0,
        )

        try:
            if jobs:
                stats = self.persist_jobs(run_id, source_name, jobs)
                summary["imported"] = stats["new"]
                summary["duplicates"] = stats["seen_again"]
            self.finish_run(
                run_id,
                status="imported",
                raw_count=summary["rows_read"],
                unique_count=len(jobs),
                new_count=summary["imported"],
                exported_count=0,
                notes=f"csv import: {file_path}",
            )
        except Exception as exc:
            self.finish_run(
                run_id,
                status="failed",
                failure_reason=str(exc),
                notes=str(exc),
            )
            raise

        return summary

    def get_platform_stats(self) -> dict[str, Any]:
        return self.get_database_stats()

    def get_database_stats(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            jobs_total = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
            runs_total = conn.execute(
                "SELECT COUNT(*) AS n FROM scrape_runs"
            ).fetchone()["n"]
            imports_total = conn.execute(
                "SELECT COUNT(*) AS n FROM csv_imports"
            ).fetchone()["n"]
            sources = conn.execute(
                "SELECT name, display_name FROM sources ORDER BY name"
            ).fetchall()
            by_source = conn.execute(
                """
                SELECT s.name, COUNT(j.id) AS job_count
                FROM sources s
                LEFT JOIN jobs j ON j.source_id = s.id
                GROUP BY s.id
                ORDER BY job_count DESC
                """
            ).fetchall()
            recurring = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE times_seen > 1"
            ).fetchone()["n"]
            avg_seen = conn.execute(
                "SELECT ROUND(AVG(times_seen), 2) AS v FROM jobs"
            ).fetchone()["v"]
            last_run = conn.execute(
                """
                SELECT r.id, s.name AS source, r.keyword, r.location,
                       r.status, r.started_at, r.finished_at,
                       r.duration_seconds, r.failure_reason,
                       r.pages_attempted, r.pages_completed,
                       r.new_count, r.exported_count
                FROM scrape_runs r
                JOIN sources s ON s.id = r.source_id
                ORDER BY r.started_at DESC, r.id DESC
                LIMIT 1
                """
            ).fetchone()

        return {
            "db_path": str(self.db.db_path),
            "jobs_total": jobs_total,
            "runs_total": runs_total,
            "imports_total": imports_total,
            "recurring_jobs": recurring or 0,
            "avg_times_seen": avg_seen or 0,
            "sources": [dict(row) for row in sources],
            "jobs_by_source": [dict(row) for row in by_source],
            "last_run": dict(last_run) if last_run else None,
        }

    def list_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.id, s.name AS source, r.keyword, r.location,
                       r.max_pages, r.status, r.started_at, r.finished_at,
                       r.duration_seconds, r.failure_reason,
                       r.pages_attempted, r.pages_completed,
                       r.raw_count, r.unique_count, r.new_count, r.exported_count,
                       r.target_key, r.adapter_kind
                FROM scrape_runs r
                JOIN sources s ON s.id = r.source_id
                ORDER BY r.started_at DESC, r.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_jobs_for_run(self, run_id: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT j.id, s.name AS source, j.job_title, j.company_name,
                       j.job_location, j.posting_date, j.salary, j.job_url,
                       j.first_seen_at, j.last_seen_at, j.times_seen
                FROM run_jobs rj
                JOIN jobs j ON j.id = rj.job_id
                JOIN sources s ON s.id = j.source_id
                WHERE rj.run_id = ?
                ORDER BY j.job_title COLLATE NOCASE
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_jobs(
        self,
        *,
        source_name: str | None = None,
        new_only: bool = False,
        run_id: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self._query_jobs(
            source_name=source_name,
            new_only=new_only,
            run_id=run_id,
            limit=limit,
        )

    def list_recent_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._query_jobs(order_by="j.last_seen_at DESC", limit=limit)

    def search_jobs(self, query: str, limit: int = 30) -> list[dict[str, Any]]:
        pattern = f"%{query.strip().lower()}%"
        return self._query_jobs(
            where_extra="""
                (
                    LOWER(j.job_title) LIKE ?
                    OR LOWER(j.company_name) LIKE ?
                    OR LOWER(j.job_location) LIKE ?
                )
            """,
            extra_params=[pattern, pattern, pattern],
            limit=limit,
        )

    def list_jobs_by_source(self, source_name: str, limit: int = 30) -> list[dict[str, Any]]:
        return self._query_jobs(
            source_name=resolve_source_name(source_name),
            limit=limit,
        )

    def top_companies(self, limit: int = 15) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT company_name, COUNT(*) AS job_count,
                       MAX(last_seen_at) AS last_seen
                FROM jobs
                WHERE company_name IS NOT NULL AND company_name != ?
                GROUP BY LOWER(company_name)
                ORDER BY job_count DESC, last_seen DESC
                LIMIT ?
                """,
                (_NA, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_recurring_jobs(self, min_times_seen: int = 2, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT j.id, s.name AS source, j.job_title, j.company_name,
                       j.first_seen_at, j.last_seen_at, j.times_seen
                FROM jobs j
                JOIN sources s ON s.id = j.source_id
                WHERE j.times_seen >= ?
                ORDER BY j.times_seen DESC, j.last_seen_at DESC
                LIMIT ?
                """,
                (min_times_seen, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def _job_filter_clauses(
        self,
        *,
        source_name: str | None = None,
        company: str | None = None,
        search_query: str | None = None,
        new_only: bool = False,
        run_id: int | None = None,
        where_extra: str | None = None,
        extra_params: list[Any] | None = None,
    ) -> tuple[list[str], list[Any]]:
        clauses = ["1=1"]
        params: list[Any] = list(extra_params or [])

        if source_name:
            clauses.append("j.source_id = (SELECT id FROM sources WHERE name = ?)")
            params.append(resolve_source_name(source_name))

        if company:
            clauses.append("LOWER(j.company_name) = LOWER(?)")
            params.append(company.strip())

        if search_query and search_query.strip():
            pattern = f"%{search_query.strip().lower()}%"
            clauses.append(
                """
                (
                    LOWER(j.job_title) LIKE ?
                    OR LOWER(j.company_name) LIKE ?
                    OR LOWER(j.job_location) LIKE ?
                )
                """
            )
            params.extend([pattern, pattern, pattern])

        if where_extra:
            clauses.append(where_extra)

        if new_only and run_id is not None:
            clauses.append(
                """
                j.id IN (
                    SELECT job_id FROM run_jobs
                    WHERE run_id = ? AND is_new = 1
                )
                """
            )
            params.append(run_id)

        return clauses, params

    def count_jobs(
        self,
        *,
        source_name: str | None = None,
        company: str | None = None,
        search_query: str | None = None,
    ) -> int:
        clauses, params = self._job_filter_clauses(
            source_name=source_name,
            company=company,
            search_query=search_query,
        )
        sql = f"""
            SELECT COUNT(*) AS n
            FROM jobs j
            JOIN sources s ON s.id = j.source_id
            WHERE {' AND '.join(clauses)}
        """
        with self.db.connect() as conn:
            return int(conn.execute(sql, params).fetchone()["n"])

    def list_jobs_filtered(
        self,
        *,
        source_name: str | None = None,
        company: str | None = None,
        search_query: str | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        clauses, params = self._job_filter_clauses(
            source_name=source_name,
            company=company,
            search_query=search_query,
        )
        params.extend([limit, offset])
        sql = f"""
            SELECT j.id, s.name AS source, j.job_title, j.company_name,
                   j.job_location, j.posting_date, j.salary, j.job_url,
                   j.first_seen_at, j.last_seen_at, j.times_seen
            FROM jobs j
            JOIN sources s ON s.id = j.source_id
            WHERE {' AND '.join(clauses)}
            ORDER BY j.last_seen_at DESC
            LIMIT ? OFFSET ?
        """
        with self.db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def list_all_jobs_for_export(
        self,
        *,
        source_name: str | None = None,
        company: str | None = None,
        search_query: str | None = None,
        max_rows: int = 50_000,
    ) -> list[dict[str, Any]]:
        """Return jobs in EXPORT column shape for CSV download."""
        rows = self.list_jobs_filtered(
            source_name=source_name,
            company=company,
            search_query=search_query,
            offset=0,
            limit=max_rows,
        )
        columns = EXPORT["csv_columns"]
        return [{col: row.get(col, _NA) for col in columns} for row in rows]

    def _query_jobs(
        self,
        *,
        source_name: str | None = None,
        new_only: bool = False,
        run_id: int | None = None,
        where_extra: str | None = None,
        extra_params: list[Any] | None = None,
        order_by: str = "j.last_seen_at DESC",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses, params = self._job_filter_clauses(
            source_name=source_name,
            new_only=new_only,
            run_id=run_id,
            where_extra=where_extra,
            extra_params=extra_params,
        )
        params.append(limit)
        sql = f"""
            SELECT j.id, s.name AS source, j.job_title, j.company_name,
                   j.job_location, j.posting_date, j.salary, j.job_url,
                   j.first_seen_at, j.last_seen_at, j.times_seen
            FROM jobs j
            JOIN sources s ON s.id = j.source_id
            WHERE {' AND '.join(clauses)}
            ORDER BY {order_by}
            LIMIT ?
        """

        with self.db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
