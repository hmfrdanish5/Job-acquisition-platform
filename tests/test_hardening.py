"""Field merge, keywords, URL safety, run status, sources, batch, migration."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import SCHEMA_VERSION, Database
from extractors import detect_ats, jobs_from_smartrecruiters_payload
from filters import apply_keyword_filter, parse_keywords
from job_store import JobStore, choose_better_value, is_missing_value
from normalizer import normalize_job
from pipeline import (
    STATUS_COMPLETED,
    STATUS_EMPTY,
    STATUS_EXPORT_FAILED,
    STATUS_FAILED,
    collect_all,
    collect_jobs,
)
from sources.registry import select_source
from url_safety import is_safe_public_url, join_and_validate


def _public(_host: str) -> list[str]:
    return ["93.184.216.34"]


def _job(**overrides) -> dict:
    base = {
        "company_name": "Co",
        "job_title": "Role",
        "job_location": "Loc",
        "posting_date": "Today",
        "salary": "₹10L - ₹15L",
        "job_url": "https://example.com/jobs/1",
    }
    base.update(overrides)
    return normalize_job(base)


class TestFieldMerge(unittest.TestCase):
    def test_choose_better_value_policy(self):
        self.assertEqual(choose_better_value("₹10L", "N/A"), "₹10L")
        self.assertEqual(choose_better_value("N/A", "₹12L"), "₹12L")
        self.assertEqual(choose_better_value("Old", "New"), "New")
        self.assertIsNone(choose_better_value(None, None))
        self.assertEqual(choose_better_value("Kept", ""), "Kept")
        self.assertEqual(choose_better_value("Kept", "  "), "Kept")
        self.assertTrue(is_missing_value("n/a"))

    def test_persist_preserves_salary_and_updates_seen(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = JobStore(db=Database(Path(tmp.name) / "t.db"))
        first = _job(salary="₹10L - ₹15L", job_url="https://example.com/jobs/keep")
        run1 = store.start_run("careers", keyword="k", location="u", max_pages=1)
        store.persist_jobs(run1, "careers", [first])
        store.finish_run(run1, status="completed")

        second = _job(salary="N/A", job_url="https://example.com/jobs/keep")
        run2 = store.start_run("careers", keyword="k", location="u", max_pages=1)
        store.persist_jobs(run2, "careers", [second])
        store.finish_run(run2, status="completed")

        with store.db.connect() as conn:
            row = conn.execute("SELECT salary, times_seen, job_title FROM jobs").fetchone()
        self.assertEqual(row["salary"], "₹10L - ₹15L")
        self.assertEqual(row["times_seen"], 2)

        third = _job(salary="₹20L", job_title="Senior Role", job_url="https://example.com/jobs/keep")
        run3 = store.start_run("careers", keyword="k", location="u", max_pages=1)
        store.persist_jobs(run3, "careers", [third])
        with store.db.connect() as conn:
            row = conn.execute("SELECT salary, job_title, times_seen FROM jobs").fetchone()
        self.assertEqual(row["salary"], "₹20L")
        self.assertEqual(row["job_title"], "Senior Role")
        self.assertEqual(row["times_seen"], 3)


class TestKeywords(unittest.TestCase):
    def test_parse_or_terms(self):
        self.assertEqual(
            parse_keywords("engineer, intern, remote"),
            ["engineer", "intern", "remote"],
        )
        self.assertEqual(parse_keywords("engineer, engineer"), ["engineer"])
        self.assertEqual(parse_keywords(""), [])
        self.assertEqual(parse_keywords("  python  "), ["python"])

    def test_filter_or(self):
        jobs = [
            {"job_title": "Python Engineer", "job_location": "NY", "company_name": "A"},
            {"job_title": "Intern", "job_location": "Remote", "company_name": "B"},
            {"job_title": "Chef", "job_location": "Paris", "company_name": "C"},
        ]
        kept = apply_keyword_filter(jobs, "engineer, intern, remote")
        titles = {j["job_title"] for j in kept}
        self.assertIn("Python Engineer", titles)
        self.assertIn("Intern", titles)
        self.assertNotIn("Chef", titles)


class TestUrlSafety(unittest.TestCase):
    def test_public_accepted(self):
        ok, _ = is_safe_public_url("https://example.com/careers", resolve_host=_public)
        self.assertTrue(ok)

    def test_https_external_link(self):
        url, reason = join_and_validate(
            "https://example.com/jobs",
            "https://jobs.example.com/open",
            resolve_host=_public,
        )
        self.assertTrue(url)
        self.assertEqual(reason, "")

    def test_safe_relative_link(self):
        url, _ = join_and_validate(
            "https://example.com/careers",
            "/jobs/eng",
            resolve_host=_public,
        )
        self.assertEqual(url, "https://example.com/jobs/eng")

    def test_discovered_unsafe_rejected(self):
        url, reason = join_and_validate(
            "https://example.com/careers",
            "http://127.0.0.1/secret",
            resolve_host=_public,
        )
        self.assertIsNone(url)
        self.assertTrue(reason)

    def test_loopback_ipv6(self):
        ok, _ = is_safe_public_url("http://[::1]/jobs")
        self.assertFalse(ok)

    def test_redirect_policy_uses_same_check(self):
        ok, _ = is_safe_public_url("http://192.168.0.10/careers")
        self.assertFalse(ok)


class TestRunStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(db=Database(Path(self.tmp.name) / "t.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_completed_and_export_failed(self):
        jobs = [_job()]
        with patch("pipeline.scrape_career_page", return_value=jobs), patch(
            "url_safety.is_safe_public_url", return_value=(True, "")
        ), patch("pipeline.export_to_csv", return_value=True):
            result = collect_jobs(
                "https://boards.greenhouse.io/x",
                store=self.store,
                write_export=True,
            )
        self.assertEqual(result["status"], STATUS_COMPLETED)
        self.assertTrue(result["exported"])
        self.assertEqual(self.store.list_runs(1)[0]["status"], STATUS_COMPLETED)

        with patch("pipeline.scrape_career_page", return_value=jobs), patch(
            "url_safety.is_safe_public_url", return_value=(True, "")
        ), patch("pipeline.export_to_csv", return_value=False):
            result = collect_jobs(
                "https://boards.greenhouse.io/x",
                store=self.store,
                write_export=True,
            )
        self.assertEqual(result["status"], STATUS_EXPORT_FAILED)
        self.assertFalse(result["exported"])
        run = self.store.list_runs(1)[0]
        self.assertEqual(run["status"], STATUS_EXPORT_FAILED)
        self.assertEqual(run["exported_count"], 0)
        self.assertEqual(run["failure_reason"], "csv export failed")

    def test_empty_not_failed(self):
        with patch("pipeline.scrape_career_page", return_value=[]), patch(
            "url_safety.is_safe_public_url", return_value=(True, "")
        ):
            result = collect_jobs("https://example.com/careers", store=self.store)
        self.assertEqual(result["status"], STATUS_EMPTY)
        self.assertFalse(result["ok"])
        run = self.store.list_runs(1)[0]
        self.assertEqual(run["status"], STATUS_EMPTY)
        self.assertIsNone(run["failure_reason"])

    def test_blocked_is_failed(self):
        from sources.errors import AcquisitionError

        with patch(
            "pipeline.scrape_career_page",
            side_effect=AcquisitionError("challenge", blocked=True),
        ), patch("url_safety.is_safe_public_url", return_value=(True, "")):
            result = collect_jobs("https://example.com/careers", store=self.store)
        self.assertEqual(result["status"], STATUS_FAILED)
        self.assertTrue(result["blocked"])
        run = self.store.list_runs(1)[0]
        self.assertEqual(run["status"], STATUS_FAILED)
        self.assertIn("blocked", run["failure_reason"].lower())


class TestSources(unittest.TestCase):
    def test_select_adapters(self):
        self.assertEqual(select_source("https://boards.greenhouse.io/acme").name, "greenhouse")
        self.assertEqual(select_source("https://jobs.lever.co/acme").name, "lever")
        self.assertEqual(select_source("https://jobs.ashbyhq.com/acme").name, "ashby")
        self.assertEqual(
            select_source("https://jobs.smartrecruiters.com/Acme").name,
            "smartrecruiters",
        )
        self.assertEqual(select_source("https://example.com/careers").name, "html")

    def test_smartrecruiters_payload(self):
        payload = {
            "content": [
                {
                    "id": "abc",
                    "name": "Backend Engineer",
                    "releasedDate": "2026-01-02T10:00:00.000Z",
                    "location": {"city": "Berlin", "country": "DE"},
                    "company": {"name": "Acme"},
                    "ref": "https://jobs.smartrecruiters.com/Acme/abc",
                }
            ]
        }
        jobs = jobs_from_smartrecruiters_payload(payload)
        self.assertEqual(jobs[0]["job_title"], "Backend Engineer")
        self.assertEqual(jobs[0]["posting_date"], "2026-01-02")
        kind, slug = detect_ats("https://jobs.smartrecruiters.com/Acme")
        self.assertEqual(kind, "smartrecruiters")
        self.assertEqual(slug, "Acme")


class TestBatchAndTargets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(db=Database(Path(self.tmp.name) / "t.db"))
        self.targets_path = Path(self.tmp.name) / "targets.json"
        payload = {
            "targets": [
                {
                    "id": "good",
                    "name": "Good Co",
                    "url": "https://boards.greenhouse.io/good",
                    "company": "Good",
                    "enabled": True,
                    "keywords": "",
                },
                {
                    "id": "bad",
                    "name": "Bad Co",
                    "url": "https://example.com/careers",
                    "company": "Bad",
                    "enabled": True,
                    "keywords": "",
                },
                {
                    "id": "off",
                    "name": "Off Co",
                    "url": "https://boards.greenhouse.io/off",
                    "company": "Off",
                    "enabled": False,
                    "keywords": "",
                },
            ]
        }
        self.targets_path.write_text(json.dumps(payload), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_batch_continues_and_skips_disabled(self):
        calls = []

        def fake_jobs(url, **kwargs):
            calls.append(url)
            store = kwargs["store"]
            if "good" in url:
                run = store.start_run(
                    "careers",
                    keyword="",
                    location=url,
                    max_pages=1,
                    target_key=kwargs.get("target_key"),
                    adapter_kind="greenhouse",
                )
                store.persist_jobs(run, "careers", [_job(job_url="https://example.com/jobs/g1")])
                store.finish_run(run, status=STATUS_COMPLETED, new_count=1)
                return {
                    "ok": True,
                    "status": STATUS_COMPLETED,
                    "jobs": [_job()],
                    "new_count": 1,
                    "run_id": run,
                    "message": "ok",
                    "blocked": False,
                }
            run = store.start_run(
                "careers",
                keyword="",
                location=url,
                max_pages=1,
                target_key=kwargs.get("target_key"),
                adapter_kind="html",
            )
            store.finish_run(run, status=STATUS_FAILED, failure_reason="blocked: challenge")
            return {
                "ok": False,
                "status": STATUS_FAILED,
                "jobs": [],
                "new_count": 0,
                "run_id": run,
                "message": "blocked",
                "blocked": True,
            }

        with patch("targets.TARGETS", {"path": str(self.targets_path), "example_path": str(self.targets_path)}), patch(
            "pipeline.collect_jobs", side_effect=fake_jobs
        ):
            summary = collect_all(store=self.store, write_export=False)

        self.assertEqual(summary["targets"], 2)
        self.assertEqual(summary["skipped_disabled"], 1)
        self.assertEqual(summary["successful"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["new_jobs"], 1)
        self.assertEqual(len(calls), 2)
        runs = self.store.list_runs(10)
        keys = {r.get("target_key") for r in runs}
        self.assertIn("good", keys)
        self.assertIn("bad", keys)
        self.assertNotIn("off", keys)


class TestMigration(unittest.TestCase):
    def test_live_db_copy_preserves_jobs(self):
        live = Path("data/jobs.db")
        if not live.is_file():
            self.skipTest("data/jobs.db not present")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        dest = Path(tmp.name) / "jobs.db"
        shutil.copy2(live, dest)
        before = Database(dest)
        with before.connect() as conn:
            jobs_before = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
            sources_before = [
                r["name"] for r in conn.execute("SELECT name FROM sources ORDER BY name")
            ]
        db = Database(dest)
        db.initialize()
        health = db.health_check()
        self.assertEqual(health["schema_version"], str(SCHEMA_VERSION))
        self.assertTrue(health["tables_ok"])
        with db.connect() as conn:
            jobs_after = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
            cols = [r[1] for r in conn.execute("PRAGMA table_info(scrape_runs)")]
            sources_after = [
                r["name"] for r in conn.execute("SELECT name FROM sources ORDER BY name")
            ]
        self.assertEqual(jobs_before, jobs_after)
        self.assertGreater(jobs_after, 0)
        self.assertIn("target_key", cols)
        self.assertIn("adapter_kind", cols)
        self.assertEqual(sources_before, sources_after)


class TestFlaskTargetsPage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import config

        self._old = config.DATABASE["path"]
        config.DATABASE["path"] = str(Path(self.tmp.name) / "jobs.db")
        from dashboard.app import create_app
        from dashboard.routes import service

        service.initialize()
        app = create_app()
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        import config

        config.DATABASE["path"] = self._old
        self.tmp.cleanup()

    def test_targets_page(self):
        response = self.client.get("/targets")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Acquisition targets", response.data)

    def test_history_still_renders(self):
        response = self.client.get("/history")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
