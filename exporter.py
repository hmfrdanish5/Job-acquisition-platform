"""
exporter.py
-----------
Exports the final list of cleaned job dicts to a CSV file using pandas.
Column order, encoding, and file path are all read from config.EXPORT.
"""

import logging
import pandas as pd

from config import EXPORT

logger = logging.getLogger(__name__)


def export_to_csv(jobs: list[dict], output_path: str | None = None) -> bool:
    """
    Export a list of job dicts to a CSV file.

    Args:
        jobs        : list of normalised job dicts to export
        output_path : override the filename from config (optional)

    Returns:
        True on success, False on failure.
    """
    if not jobs:
        logger.warning("[EXPORT] No jobs to export — CSV will not be created.")
        return False

    path     = output_path or EXPORT["output_file"]
    columns  = EXPORT["csv_columns"]
    encoding = EXPORT["encoding"]
    missing  = EXPORT["missing_value"]

    try:
        df = pd.DataFrame(jobs)

        # Ensure every expected column is present; fill missing ones
        for col in columns:
            if col not in df.columns:
                df[col] = missing

        df = df[columns]

        # Replace any remaining empty strings or None with the missing placeholder
        df = df.fillna(missing)
        df = df.replace("", missing)

        # utf-8-sig BOM ensures Excel on Windows opens the file without garbling
        df.to_csv(path, index=False, encoding=encoding)

        logger.info(f"[EXPORT] {len(df)} job(s) exported → '{path}'")
        return True

    except PermissionError:
        logger.error(
            f"[EXPORT] Permission denied writing to '{path}'. "
            "Is the file already open in Excel?"
        )
    except Exception as e:
        logger.error(f"[EXPORT] Unexpected error: {e}")

    return False


def preview_jobs(jobs: list[dict], max_rows: int | None = None) -> None:
    """
    Print a formatted preview of the first N jobs to the terminal.
    N defaults to EXPORT["preview_rows"] from config.

    Args:
        jobs     : list of job dicts to preview
        max_rows : override the config default (optional)
    """
    if not jobs:
        print("  (no jobs to preview)")
        return

    n = max_rows if max_rows is not None else EXPORT["preview_rows"]
    total = len(jobs)

    print("\n" + "─" * 70)
    print(f"  PREVIEW — first {min(n, total)} of {total} job(s)")
    print("─" * 70)

    for i, job in enumerate(jobs[:n]):
        print(f"\n  [{i + 1}]  {job.get('job_title', 'N/A')}")
        print(f"       Company  : {job.get('company_name', 'N/A')}")
        print(f"       Location : {job.get('job_location', 'N/A')}")
        print(f"       Posted   : {job.get('posting_date', 'N/A')}")
        print(f"       Salary   : {job.get('salary', 'N/A')}")
        url = job.get('job_url', 'N/A')
        # Truncate very long URLs for readability in the terminal
        display_url = url if len(url) <= 80 else url[:77] + "..."
        print(f"       URL      : {display_url}")

    print("\n" + "─" * 70 + "\n")
