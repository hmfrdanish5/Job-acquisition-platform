"""
database.py  —  v2.0
--------------------
SQLite schema and low-level persistence for the job acquisition platform.

Tables
──────
  sources     — acquisition adapters (Indeed today; others later)
  scrape_runs — one row per operator scrape session
  jobs        — canonical deduplicated job records per source
  run_jobs    — jobs observed in a given run (new vs seen flags)
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from config import DATABASE

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

REQUIRED_TABLES = (
    "schema_meta",
    "sources",
    "scrape_runs",
    "jobs",
    "run_jobs",
    "csv_imports",
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id      INTEGER NOT NULL,
    keyword        TEXT NOT NULL,
    location       TEXT NOT NULL,
    max_pages      INTEGER NOT NULL,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL,
    raw_count      INTEGER DEFAULT 0,
    unique_count   INTEGER DEFAULT 0,
    new_count      INTEGER DEFAULT 0,
    exported_count INTEGER DEFAULT 0,
    notes          TEXT,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     INTEGER NOT NULL,
    dedupe_key    TEXT NOT NULL,
    company_name  TEXT,
    job_title     TEXT,
    job_location  TEXT,
    posting_date  TEXT,
    salary        TEXT,
    job_url       TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    times_seen    INTEGER NOT NULL DEFAULT 1,
    UNIQUE (source_id, dedupe_key),
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS run_jobs (
    run_id  INTEGER NOT NULL,
    job_id  INTEGER NOT NULL,
    is_new  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, job_id),
    FOREIGN KEY (run_id) REFERENCES scrape_runs(id),
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_source_last_seen
    ON jobs (source_id, last_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_runs_started
    ON scrape_runs (started_at DESC);

CREATE INDEX IF NOT EXISTS idx_run_jobs_new
    ON run_jobs (run_id, is_new);
"""


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    """Thin SQLite wrapper with schema initialization."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path or DATABASE["path"])
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        """Create tables if missing and record schema version."""
        try:
            with self.connect() as conn:
                conn.executescript(_SCHEMA_SQL)
                conn.execute(
                    """
                    INSERT INTO schema_meta (key, value)
                    VALUES ('version', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (str(SCHEMA_VERSION),),
                )
            self._migrate()
            logger.info("[DB] Initialized SQLite at '%s'", self.db_path)
        except sqlite3.Error as exc:
            logger.error("[DB] Failed to initialize database: %s", exc)
            raise

    def _column_exists(self, conn: sqlite3.Connection, table: str, column: str) -> bool:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row[1] == column for row in rows)

    def _add_column_if_missing(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        if not self._column_exists(conn, table, column):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            logger.info("[DB] Added column %s.%s", table, column)

    def _migrate(self) -> None:
        """Apply additive schema upgrades for existing databases."""
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS csv_imports (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path        TEXT NOT NULL,
                    file_fingerprint TEXT NOT NULL UNIQUE,
                    source_id        INTEGER NOT NULL,
                    run_id           INTEGER,
                    imported_at      TEXT NOT NULL,
                    rows_read        INTEGER DEFAULT 0,
                    jobs_imported    INTEGER DEFAULT 0,
                    jobs_duplicate   INTEGER DEFAULT 0,
                    rows_skipped     INTEGER DEFAULT 0,
                    rows_error       INTEGER DEFAULT 0,
                    FOREIGN KEY (source_id) REFERENCES sources(id),
                    FOREIGN KEY (run_id) REFERENCES scrape_runs(id)
                );
                """
            )

            self._add_column_if_missing(
                conn, "scrape_runs", "duration_seconds", "REAL"
            )
            self._add_column_if_missing(
                conn, "scrape_runs", "failure_reason", "TEXT"
            )
            self._add_column_if_missing(
                conn, "scrape_runs", "pages_attempted", "INTEGER DEFAULT 0"
            )
            self._add_column_if_missing(
                conn, "scrape_runs", "pages_completed", "INTEGER DEFAULT 0"
            )

            conn.execute(
                """
                INSERT INTO schema_meta (key, value)
                VALUES ('version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )

    def ensure_source(self, name: str, display_name: str | None = None) -> int:
        """Return the source id, creating the row if needed."""
        label = display_name or name
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM sources WHERE name = ?", (name,)
            ).fetchone()
            if row:
                return int(row["id"])

            cur = conn.execute(
                """
                INSERT INTO sources (name, display_name, created_at)
                VALUES (?, ?, ?)
                """,
                (name, label, utc_now()),
            )
            source_id = int(cur.lastrowid)
            logger.info("[DB] Registered source '%s' (id=%s)", name, source_id)
            return source_id

    def get_schema_version(self) -> str | None:
        """Return recorded schema version from schema_meta, or None."""
        try:
            with self.connect() as conn:
                row = conn.execute(
                    "SELECT value FROM schema_meta WHERE key = 'version'"
                ).fetchone()
            return str(row["value"]) if row else None
        except sqlite3.Error:
            return None

    def health_check(self) -> dict:
        """
        Verify database reachability, schema version, and required tables.
        Does not mutate schema.
        """
        result: dict = {
            "reachable": False,
            "db_path": str(self.db_path),
            "schema_version": None,
            "expected_version": str(SCHEMA_VERSION),
            "tables_ok": False,
            "missing_tables": [],
            "error": None,
        }

        if not self.db_path.exists():
            result["error"] = "database file does not exist"
            return result

        try:
            with self.connect() as conn:
                result["reachable"] = True
                row = conn.execute(
                    "SELECT value FROM schema_meta WHERE key = 'version'"
                ).fetchone()
                if row:
                    result["schema_version"] = str(row["value"])

                missing = []
                for table in REQUIRED_TABLES:
                    found = conn.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type = 'table' AND name = ?
                        """,
                        (table,),
                    ).fetchone()
                    if not found:
                        missing.append(table)

                result["missing_tables"] = missing
                result["tables_ok"] = len(missing) == 0
        except sqlite3.Error as exc:
            result["error"] = str(exc)

        return result
