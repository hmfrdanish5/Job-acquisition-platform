"""Service layer for the web UI."""

from __future__ import annotations

import math
from typing import Any

from config import DASHBOARD
from job_store import JobStore
from pipeline import collect_jobs


class DashboardService:
    def __init__(self) -> None:
        self._store: JobStore | None = None
        self._init_error: str | None = None

    @property
    def store(self) -> JobStore:
        if self._store is None:
            raise RuntimeError(self._init_error or "Database not initialized")
        return self._store

    def initialize(self) -> None:
        try:
            self._store = JobStore()
            self._init_error = None
        except Exception as exc:
            self._store = None
            self._init_error = str(exc)

    @property
    def ready(self) -> bool:
        return self._store is not None

    @property
    def init_error(self) -> str | None:
        return self._init_error

    def collect(
        self,
        url: str,
        *,
        company_name: str = "",
        keyword: str = "",
    ) -> dict[str, Any]:
        if not self.ready:
            self.initialize()
        return collect_jobs(
            url,
            company_name=company_name,
            keyword=keyword,
            store=self.store if self.ready else None,
        )

    def get_home_summary(self) -> dict[str, Any]:
        if not self.ready:
            return {"error": self._init_error}
        stats = self.store.get_database_stats()
        last = stats.get("last_run")
        return {
            "jobs_total": stats["jobs_total"],
            "runs_total": stats["runs_total"],
            "last_run_status": last.get("status") if last else None,
            "last_run_started": last.get("started_at") if last else None,
            "last_run_id": last.get("id") if last else None,
        }

    def get_jobs_page(
        self,
        *,
        page: int = 1,
        q: str = "",
        company: str = "",
        run_id: int | None = None,
    ) -> dict[str, Any]:
        if not self.ready:
            return {"error": self._init_error, "jobs": [], "total": 0}

        page_size = DASHBOARD["page_size"]
        page = max(1, page)
        company_name = company.strip() or None
        search = q.strip() or None

        if run_id:
            jobs = self.store.list_jobs_for_run(run_id)
            if search:
                needle = search.lower()
                jobs = [
                    job
                    for job in jobs
                    if needle in " ".join(
                        [
                            str(job.get("job_title") or ""),
                            str(job.get("company_name") or ""),
                            str(job.get("job_location") or ""),
                        ]
                    ).lower()
                ]
            if company_name:
                jobs = [
                    job
                    for job in jobs
                    if (job.get("company_name") or "").lower() == company_name.lower()
                ]
            total = len(jobs)
            total_pages = max(1, math.ceil(total / page_size)) if total else 1
            page = min(page, total_pages)
            start = (page - 1) * page_size
            jobs = jobs[start : start + page_size]
            return {
                "jobs": jobs,
                "total": total,
                "page": page,
                "total_pages": total_pages,
                "page_size": page_size,
                "q": q,
                "company": company,
                "run_id": run_id,
            }

        total = self.store.count_jobs(company=company_name, search_query=search)
        total_pages = max(1, math.ceil(total / page_size)) if total else 1
        page = min(page, total_pages)
        offset = (page - 1) * page_size
        jobs = self.store.list_jobs_filtered(
            company=company_name,
            search_query=search,
            offset=offset,
            limit=page_size,
        )
        return {
            "jobs": jobs,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "page_size": page_size,
            "q": q,
            "company": company,
            "run_id": None,
        }

    def list_company_options(self, limit: int = 100) -> list[str]:
        if not self.ready:
            return []
        rows = self.store.top_companies(limit=limit)
        return sorted({r["company_name"] for r in rows if r.get("company_name")})

    def get_runs(self, limit: int = 50) -> dict[str, Any]:
        if not self.ready:
            return {"error": self._init_error, "runs": []}
        runs = self.store.list_runs(limit=limit)
        return {"runs": runs, "total": len(runs)}

    def get_export_jobs(
        self,
        *,
        q: str = "",
        company: str = "",
        run_id: int | None = None,
    ) -> tuple[list[dict], str | None]:
        if not self.ready:
            return [], self._init_error

        if run_id:
            rows = self.store.list_jobs_for_run(run_id)
            jobs = [
                {
                    "company_name": row.get("company_name"),
                    "job_title": row.get("job_title"),
                    "job_location": row.get("job_location"),
                    "posting_date": row.get("posting_date"),
                    "salary": row.get("salary"),
                    "job_url": row.get("job_url"),
                }
                for row in rows
            ]
        else:
            jobs = self.store.list_all_jobs_for_export(
                company=company.strip() or None,
                search_query=q.strip() or None,
            )
        if not jobs:
            return [], "No jobs match the current filters."
        return jobs, None

    def list_targets(self) -> list[dict]:
        from sources.registry import select_source
        from targets import load_targets, targets_config_path

        items = []
        for t in load_targets(enabled_only=False):
            items.append(
                {
                    "id": t.id,
                    "name": t.name,
                    "url": t.url,
                    "company": t.company,
                    "enabled": t.enabled,
                    "keywords": t.keywords,
                    "notes": t.notes,
                    "adapter": select_source(t.url).name,
                    "error": t.validation_error(),
                }
            )
        return items

    def targets_path(self) -> str:
        from targets import targets_config_path

        return str(targets_config_path())

    def collect_target(self, target_id: str) -> dict:
        from pipeline import collect_target

        if not self.ready:
            self.initialize()
        return collect_target(
            target_id,
            store=self.store if self.ready else None,
            write_export=False,
        )

    def collect_all_targets(self) -> dict:
        from pipeline import collect_all

        if not self.ready:
            self.initialize()
        return collect_all(
            store=self.store if self.ready else None,
            write_export=False,
        )
