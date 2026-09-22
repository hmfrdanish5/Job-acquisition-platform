"""
import_csv.py  —  v2.1
----------------------
Import historical jobs.csv files into SQLite with deduplication.

Usage:
  python import_csv.py jobs.csv
  python import_csv.py path/to/old_export.csv --source careers
  python import_csv.py jobs.csv --force
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from config import EXPORT, IMPORT, LOGGING
from data_quality import validate_csv_row
from job_store import JobStore, build_dedupe_key, file_fingerprint, resolve_source_name
from normalizer import normalize_job

logging.basicConfig(
    level=getattr(logging, LOGGING["level"], logging.INFO),
    format=LOGGING["format"],
    datefmt=LOGGING["datefmt"],
)
logger = logging.getLogger(__name__)


def _read_csv(path: Path) -> tuple[list[dict], int]:
    """Return (raw row dicts, line count including header)."""
    encoding = EXPORT.get("encoding", "utf-8-sig")
    rows: list[dict] = []
    with path.open(newline="", encoding=encoding) as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return [], 0
        for row in reader:
            rows.append(dict(row))
    return rows, len(rows) + 1


def import_csv_file(
    path: Path,
    *,
    source_name: str,
    force: bool = False,
    store: JobStore | None = None,
) -> dict[str, int]:
    """
    Import one CSV file. Summary keys:
      imported, duplicates, skipped, errors, rows_read
    """
    store = store or JobStore()
    source_name = resolve_source_name(source_name)
    summary = {
        "rows_read": 0,
        "imported": 0,
        "duplicates": 0,
        "skipped": 0,
        "errors": 0,
    }

    if not path.is_file():
        logger.error("File not found: %s", path)
        summary["errors"] = 1
        return summary

    fingerprint = file_fingerprint(path)
    if force and store.is_csv_imported(fingerprint):
        store.clear_csv_import_record(fingerprint)
        logger.info("Force re-import: cleared previous fingerprint for %s", path.name)

    if store.is_csv_imported(fingerprint) and not force:
        logger.warning(
            "File already imported (fingerprint %s…). Use --force to re-import.",
            fingerprint[:12],
        )
        summary["skipped"] = -1  # sentinel for "whole file skipped"
        return summary

    raw_rows, summary["rows_read"] = _read_csv(path)
    prepared: list[dict] = []
    seen_in_file: set[str] = set()

    for line_no, row in enumerate(raw_rows, start=2):
        try:
            validated = validate_csv_row(row, line_no)
            if not validated:
                summary["skipped"] += 1
                continue

            job = normalize_job(validated)
            key = build_dedupe_key(job)
            if key in seen_in_file:
                summary["duplicates"] += 1
                continue
            seen_in_file.add(key)
            prepared.append(job)
        except Exception as exc:
            logger.warning("Row %s error: %s", line_no, exc)
            summary["errors"] += 1

    if not prepared:
        logger.warning("No valid rows to import from %s", path)
        run_id = store.start_run(
            source_name,
            keyword="[csv_import]",
            location=str(path.name),
            max_pages=0,
            adapter_kind="csv",
        )
        store.finish_run(
            run_id,
            status="skipped",
            raw_count=summary["rows_read"],
            notes="no valid rows",
        )
        return summary

    run_id = store.start_run(
        source_name,
        keyword="[csv_import]",
        location=str(path.name),
        max_pages=0,
        adapter_kind="csv",
    )

    try:
        stats = store.persist_jobs(run_id, source_name, prepared)
        summary["imported"] = stats["new"]
        summary["duplicates"] += stats["seen_again"]

        store.finish_run(
            run_id,
            status="imported",
            raw_count=summary["rows_read"],
            unique_count=len(prepared),
            new_count=summary["imported"],
            exported_count=0,
            notes=f"csv:{path.name}",
        )
        store.record_csv_import(
            file_path=str(path.resolve()),
            fingerprint=fingerprint,
            source_name=source_name,
            run_id=run_id,
            summary=summary,
        )
    except Exception as exc:
        store.finish_run(
            run_id,
            status="failed",
            failure_reason=str(exc),
            notes=str(exc),
        )
        summary["errors"] += 1
        raise

    return summary


def _print_summary(path: Path, summary: dict[str, int]) -> None:
    if summary.get("skipped") == -1:
        print(f"\nSkipped (already imported): {path.name}\n")
        return

    print("\n" + "=" * 60)
    print(f"  CSV import summary — {path.name}")
    print("=" * 60)
    print(f"  Rows read     : {summary.get('rows_read', 0)}")
    print(f"  Imported (new): {summary.get('imported', 0)}")
    print(f"  Duplicates    : {summary.get('duplicates', 0)}")
    print(f"  Skipped (bad) : {summary.get('skipped', 0)}")
    print(f"  Errors        : {summary.get('errors', 0)}")
    print("=" * 60 + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Import jobs.csv into SQLite")
    parser.add_argument("csv_path", help="Path to jobs.csv export")
    parser.add_argument(
        "--source",
        default=IMPORT["default_source"],
        help="Source name (default: careers)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-import even if this file was imported before",
    )
    args = parser.parse_args()

    path = Path(args.csv_path)
    try:
        summary = import_csv_file(
            path,
            source_name=args.source,
            force=args.force,
        )
        _print_summary(path, summary)
        if summary.get("skipped") == -1:
            return 0
        if summary.get("errors", 0) and summary.get("imported", 0) == 0:
            return 1
        return 0
    except Exception as exc:
        logger.error("Import failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
