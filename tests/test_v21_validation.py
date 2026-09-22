"""
Unit tests for persistence, extractors, and URL safety.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from data_quality import clean_url, is_valid_job, validate_and_prepare_job
from database import SCHEMA_VERSION, Database
from extractors import (
    detect_ats,
    extract_jobs_from_html,
    jobs_from_greenhouse_payload,
    jobs_from_html_links,
    jobs_from_jsonld,
    jobs_from_lever_payload,
)
from job_store import JobStore, build_dedupe_key
from normalizer import normalize_job
from url_safety import is_safe_public_url


class TestDataQuality(unittest.TestCase):
    def test_clean_url_strips_tracking(self):
        raw = "https://example.com/jobs/abc?jk=abc&from=jasx&jsa=xyz"
        cleaned = clean_url(raw)
        self.assertIn("jk=abc", cleaned)
        self.assertNotIn("jsa=", cleaned)

    def test_reject_empty_job(self):
        ok, reason = is_valid_job({"job_title": "N/A", "company_name": "N/A"})
        self.assertFalse(ok)
        self.assertIn("title", reason)

    def test_validate_row(self):
        row = validate_and_prepare_job(
            {
                "company_name": "Acme",
                "job_title": "Dev",
                "job_location": "City",
                "posting_date": "Today",
                "salary": "N/A",
                "job_url": "https://example.com/jobs/x1",
            }
        )
        self.assertIsNotNone(row)


class TestUrlSafety(unittest.TestCase):
    def test_rejects_empty(self):
        ok, _ = is_safe_public_url("")
        self.assertFalse(ok)

    def test_rejects_localhost(self):
        ok, _ = is_safe_public_url("http://127.0.0.1/careers")
        self.assertFalse(ok)
        ok, _ = is_safe_public_url("http://localhost/jobs")
        self.assertFalse(ok)

    def test_rejects_private_ip(self):
        ok, _ = is_safe_public_url("http://10.0.0.8/careers")
        self.assertFalse(ok)


class TestExtractors(unittest.TestCase):
    def test_detect_ats(self):
        kind, slug = detect_ats("https://boards.greenhouse.io/acme")
        self.assertEqual(kind, "greenhouse")
        self.assertEqual(slug, "acme")
        kind, slug = detect_ats("https://jobs.lever.co/acme")
        self.assertEqual(kind, "lever")
        self.assertEqual(slug, "acme")

    def test_jsonld_jobposting(self):
        html = """
        <script type="application/ld+json">
        {
          "@type": "JobPosting",
          "title": "Backend Engineer",
          "url": "https://example.com/jobs/be",
          "hiringOrganization": {"name": "Acme"},
          "jobLocation": {"address": {"addressLocality": "Austin", "addressRegion": "TX"}},
          "datePosted": "2026-09-01"
        }
        </script>
        """
        jobs = jobs_from_jsonld(html)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_title"], "Backend Engineer")
        self.assertEqual(jobs[0]["company_name"], "Acme")
        self.assertIn("Austin", jobs[0]["job_location"])

    def test_html_job_links(self):
        html = """
        <a href="/jobs/python-dev">Python Developer</a>
        <a href="/privacy">Privacy</a>
        <a href="/blog/hello">Blog</a>
        """
        jobs = jobs_from_html_links(html, "https://example.com/careers", "Acme")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_title"], "Python Developer")
        self.assertEqual(jobs[0]["job_url"], "https://example.com/jobs/python-dev")

    def test_extract_prefers_jsonld(self):
        html = """
        <script type="application/ld+json">
        {"@type":"JobPosting","title":"Designer","url":"https://example.com/jobs/d"}
        </script>
        <a href="/jobs/other">Other Role</a>
        """
        jobs = extract_jobs_from_html(html, "https://example.com/careers")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_title"], "Designer")

    def test_greenhouse_payload(self):
        payload = {
            "jobs": [
                {
                    "title": "Staff Engineer",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                    "location": {"name": "Remote"},
                    "updated_at": "2026-01-01",
                }
            ]
        }
        jobs = jobs_from_greenhouse_payload(payload, "Acme")
        self.assertEqual(jobs[0]["company_name"], "Acme")
        self.assertEqual(jobs[0]["job_location"], "Remote")

    def test_lever_payload(self):
        payload = [
            {
                "text": "PM",
                "hostedUrl": "https://jobs.lever.co/acme/1",
                "categories": {"location": "NYC"},
            }
        ]
        jobs = jobs_from_lever_payload(payload, "Acme")
        self.assertEqual(jobs[0]["job_title"], "PM")


class TestLifecycleMetadata(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(db=Database(Path(self.tmp.name) / "test.db"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_first_seen_preserved_on_resight(self):
        job = normalize_job(
            {
                "company_name": "Co",
                "job_title": "Role",
                "job_location": "Loc",
                "posting_date": "Today",
                "salary": "N/A",
                "job_url": "https://example.com/jobs/life01",
            }
        )
        run1 = self.store.start_run("careers", keyword="k", location="l", max_pages=1)
        self.store.persist_jobs(run1, "careers", [job])
        self.store.finish_run(run1, status="completed")

        with self.store.db.connect() as conn:
            row = conn.execute(
                "SELECT first_seen_at, last_seen_at, times_seen FROM jobs LIMIT 1"
            ).fetchone()
        first_seen = row["first_seen_at"]

        run2 = self.store.start_run("careers", keyword="k", location="l", max_pages=1)
        self.store.persist_jobs(run2, "careers", [job])
        self.store.finish_run(run2, status="completed")

        with self.store.db.connect() as conn:
            row = conn.execute(
                "SELECT first_seen_at, last_seen_at, times_seen FROM jobs LIMIT 1"
            ).fetchone()

        self.assertEqual(row["first_seen_at"], first_seen)
        self.assertGreaterEqual(row["last_seen_at"], first_seen)
        self.assertEqual(row["times_seen"], 2)

    def test_dedupe_key_stable_after_url_normalize(self):
        raw = {
            "company_name": "Co",
            "job_title": "Role",
            "job_location": "Loc",
            "posting_date": "Today",
            "salary": "N/A",
            "job_url": "https://example.com/jobs/dedupe1?tk=track",
        }
        k1 = build_dedupe_key(normalize_job(raw))
        raw["job_url"] = "https://example.com/jobs/dedupe1"
        k2 = build_dedupe_key(normalize_job(raw))
        self.assertEqual(k1, k2)

    def test_list_jobs_for_run(self):
        job = normalize_job(
            {
                "company_name": "Co",
                "job_title": "Role",
                "job_location": "Loc",
                "posting_date": "Today",
                "salary": "N/A",
                "job_url": "https://example.com/jobs/run1",
            }
        )
        run_id = self.store.start_run("careers", keyword="k", location="https://example.com/jobs", max_pages=1)
        self.store.persist_jobs(run_id, "careers", [job])
        rows = self.store.list_jobs_for_run(run_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["job_title"], "Role")


class TestRunDiagnostics(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(db=Database(Path(self.tmp.name) / "test.db"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_finish_run_persists_diagnostics(self):
        run_id = self.store.start_run("careers", keyword="py", location="https://example.com", max_pages=3)
        self.store.finish_run(
            run_id,
            status="failed",
            failure_reason="blocked",
            pages_attempted=1,
            pages_completed=0,
            notes="blocked",
        )
        runs = self.store.list_runs(limit=1)
        self.assertEqual(runs[0]["pages_attempted"], 1)
        self.assertEqual(runs[0]["failure_reason"], "blocked")
        self.assertIsNotNone(runs[0]["duration_seconds"])


class TestDatabaseHealth(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "health.db")
        self.db.initialize()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_health_after_init(self):
        health = self.db.health_check()
        self.assertTrue(health["reachable"])
        self.assertTrue(health["tables_ok"])
        self.assertEqual(health["schema_version"], str(SCHEMA_VERSION))


class TestPersistBatchDedupe(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JobStore(db=Database(Path(self.tmp.name) / "test.db"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_duplicate_in_same_batch_counted_once(self):
        job = normalize_job(
            {
                "company_name": "Co",
                "job_title": "Role",
                "job_location": "Loc",
                "posting_date": "Today",
                "salary": "N/A",
                "job_url": "https://example.com/jobs/batch1",
            }
        )
        run_id = self.store.start_run("careers", keyword="k", location="l", max_pages=1)
        stats = self.store.persist_jobs(run_id, "careers", [job, job])
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["new"], 1)


class TestFlaskApp(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        import config

        self._old_db = config.DATABASE["path"]
        config.DATABASE["path"] = str(Path(self.tmp.name) / "jobs.db")
        from dashboard.app import create_app
        from dashboard.routes import service

        service.initialize()
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        import config

        config.DATABASE["path"] = self._old_db
        self.tmp.cleanup()

    def test_home_renders(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Collect job listings", response.data)

    def test_jobs_empty_state(self):
        response = self.client.get("/jobs")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No jobs yet", response.data)

    def test_rejects_bad_url(self):
        response = self.client.post("/", data={"url": "not-a-url"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
