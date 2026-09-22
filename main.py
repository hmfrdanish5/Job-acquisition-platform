"""
Command-line collection.

Prefer the web app for day-to-day use:
  python app.py

This script is a thin wrapper around the same pipeline.
"""

from __future__ import annotations

import argparse
import logging
import sys

from config import EXPORT, LOGGING, SEARCH
from exporter import preview_jobs
from pipeline import collect_jobs

logging.basicConfig(
    level=getattr(logging, LOGGING["level"], logging.INFO),
    format=LOGGING["format"],
    datefmt=LOGGING["datefmt"],
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect job listings from a company careers page."
    )
    parser.add_argument("url", help="Careers / jobs page URL")
    parser.add_argument("--company", default="", help="Company name if the page omits it")
    parser.add_argument("--keyword", default="", help="Keep listings that contain this text")
    parser.add_argument(
        "--pages",
        type=int,
        default=SEARCH["default_max_pages"],
        help="Maximum HTML pages to follow",
    )
    parser.add_argument(
        "--output",
        default=EXPORT["output_file"],
        help="CSV path",
    )
    args = parser.parse_args()

    result = collect_jobs(
        args.url,
        company_name=args.company,
        keyword=args.keyword,
        max_pages=args.pages,
        export_path=args.output,
    )
    if not result["ok"]:
        print(f"\n{result['message']}\n")
        sys.exit(1)

    preview_jobs(result["jobs"])
    print(result["message"])
    if result.get("exported"):
        print(f"Saved CSV: {result.get('export_path')}\n")


if __name__ == "__main__":
    main()
