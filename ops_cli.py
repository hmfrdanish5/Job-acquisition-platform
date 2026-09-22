"""
ops_cli.py  —  v2.1 operator CLI for data operations.

Usage:
  python ops_cli.py status
  python ops_cli.py stats
  python ops_cli.py recent [--limit N]
  python ops_cli.py companies [--limit N]
  python ops_cli.py search "python" [--limit N]
  python ops_cli.py source careers [--limit N]
  python ops_cli.py runs [--limit N]
  python ops_cli.py jobs [--limit N] [--new-only --run-id ID]
  python ops_cli.py recurring [--min-seen N]
  python ops_cli.py health
"""

from __future__ import annotations

import argparse
import sys

from job_store import JobStore, resolve_source_name


def _print_jobs(jobs: list[dict], title: str) -> None:
    if not jobs:
        print(f"\n  No jobs found ({title}).\n")
        return
    print(f"\n  {title} — {len(jobs)} result(s)\n")
    for j in jobs:
        print(f"  [{j['id']}] {j.get('job_title', 'N/A')} @ {j.get('company_name', 'N/A')}")
        loc = j.get("job_location", "N/A")
        posted = j.get("posting_date", "N/A")
        seen = j.get("times_seen", 1)
        first = j.get("first_seen_at", "")
        last = j.get("last_seen_at", "")
        print(f"       {loc}  |  {posted}  |  seen {seen}x")
        if first and last and first != last:
            print(f"       first: {first}  last: {last}")
        url = j.get("job_url", "N/A")
        if url and len(url) > 78:
            url = url[:75] + "..."
        print(f"       {url}\n")


def _cmd_status(store: JobStore) -> int:
    stats = store.get_platform_stats()
    print("\n" + "=" * 60)
    print("  Platform status")
    print("=" * 60)
    print(f"  Database : {stats['db_path']}")
    print(f"  Jobs     : {stats['jobs_total']}")
    print(f"  Runs     : {stats['runs_total']}")
    print(f"  Imports  : {stats.get('imports_total', 0)}")
    print(f"  Sources  : {', '.join(s['name'] for s in stats['sources']) or '(none)'}")
    last = stats.get("last_run")
    if last:
        print("\n  Last run:")
        print(f"    id={last['id']}  source={last['source']}  status={last['status']}")
        print(f"    query={last['keyword']!r} @ {last['location']!r}")
        print(f"    started={last['started_at']}")
        if last.get("duration_seconds") is not None:
            print(f"    duration={last['duration_seconds']}s")
        if last.get("pages_completed") is not None:
            print(
                f"    pages={last.get('pages_completed', 0)}"
                f"/{last.get('pages_attempted', 0)}"
            )
        if last.get("failure_reason"):
            print(f"    failure={last['failure_reason']}")
        print(f"    new={last['new_count']}")
    else:
        print("\n  Last run: (none)")
    print()
    return 0


def _cmd_stats(store: JobStore) -> int:
    stats = store.get_database_stats()
    print("\n" + "=" * 60)
    print("  Database statistics")
    print("=" * 60)
    print(f"  Path              : {stats['db_path']}")
    print(f"  Total jobs        : {stats['jobs_total']}")
    print(f"  Total runs        : {stats['runs_total']}")
    print(f"  CSV imports       : {stats.get('imports_total', 0)}")
    print(f"  Recurring (2+ seen): {stats.get('recurring_jobs', 0)}")
    print(f"  Avg times_seen    : {stats.get('avg_times_seen', 0)}")

    print("\n  Jobs by source:")
    for row in stats.get("jobs_by_source", []):
        print(f"    {row['name']:<16} {row['job_count']:>6}")

    last = stats.get("last_run")
    if last:
        print("\n  Last scrape run:")
        print(f"    #{last['id']}  {last['status']}  source={last['source']}")
        dur = last.get("duration_seconds")
        if dur is not None:
            print(f"    duration={dur}s  pages={last.get('pages_completed')}/{last.get('pages_attempted')}")
    print()
    return 0


def _cmd_recent(store: JobStore, limit: int) -> int:
    jobs = store.list_recent_jobs(limit=limit)
    _print_jobs(jobs, f"Recent jobs (limit {limit})")
    return 0


def _cmd_companies(store: JobStore, limit: int) -> int:
    rows = store.top_companies(limit=limit)
    if not rows:
        print("\n  No companies in database.\n")
        return 0
    print(f"\n  Top companies (limit {limit})\n")
    print(f"  {'Company':<36}  {'Jobs':>6}  Last seen")
    print("  " + "-" * 62)
    for row in rows:
        name = (row["company_name"] or "N/A")[:36]
        print(f"  {name:<36}  {row['job_count']:>6}  {row['last_seen']}")
    print()
    return 0


def _cmd_search(store: JobStore, query: str, limit: int) -> int:
    if not query.strip():
        print("Search query cannot be empty.")
        return 1
    jobs = store.search_jobs(query, limit=limit)
    _print_jobs(jobs, f'Search "{query}"')
    return 0


def _cmd_source(store: JobStore, name: str, limit: int) -> int:
    resolved = resolve_source_name(name)
    if not store.source_exists(resolved):
        print(f"\n  Unknown source '{name}' (resolved: {resolved}).")
        print("  Known sources:", ", ".join(s["name"] for s in store.get_database_stats()["sources"]) or "(none)")
        print("  Register a source by running main.py or import_csv.py first.\n")
        return 1
    jobs = store.list_jobs_by_source(resolved, limit=limit)
    _print_jobs(jobs, f"Source: {resolved}")
    return 0


def _cmd_health(store: JobStore) -> int:
    report = store.get_operational_health()
    db = report["database"]

    print("\n" + "=" * 60)
    print("  Platform health check")
    print("=" * 60)
    print(f"  Overall        : {'OK' if report['healthy'] else 'ISSUES DETECTED'}")
    print(f"  Database path  : {db['db_path']}")
    print(f"  Reachable      : {db['reachable']}")

    if db.get("error"):
        print(f"  Error          : {db['error']}")

    print(f"  Schema version : {db.get('schema_version') or '(missing)'}")
    print(f"  Expected       : {db.get('expected_version')}")
    print(f"  Tables OK      : {db.get('tables_ok')}")

    missing = db.get("missing_tables") or []
    if missing:
        print(f"  Missing tables : {', '.join(missing)}")

    print(f"  Total jobs     : {report['jobs_total']}")
    print(f"  Total runs     : {report['runs_total']}")

    if report["last_run_id"] is not None:
        print(f"  Last run       : #{report['last_run_id']}  status={report['last_run_status']}")
    else:
        print("  Last run       : (none)")

    print("=" * 60 + "\n")
    return 0 if report["healthy"] else 1


def _cmd_runs(store: JobStore, limit: int) -> int:
    runs = store.list_runs(limit=limit)
    if not runs:
        print("No scrape runs recorded yet.")
        return 0

    print(
        f"\n{'ID':>4}  {'Source':<12}  {'Status':<10}  {'New':>4}  "
        f"{'Pg':>5}  {'Sec':>6}  Started"
    )
    print("-" * 78)
    for r in runs:
        pages = f"{r.get('pages_completed', 0)}/{r.get('pages_attempted', 0)}"
        dur = r.get("duration_seconds")
        dur_s = f"{dur:>5.1f}" if dur is not None else "    -"
        print(
            f"{r['id']:>4}  {r['source']:<12}  {r['status']:<10}  "
            f"{r['new_count']:>4}  {pages:>5}  {dur_s}  {r['started_at']}"
        )
        if r.get("failure_reason"):
            print(f"       fail: {r['failure_reason']}")
    print()
    return 0


def _cmd_jobs(
    store: JobStore,
    limit: int,
    new_only: bool,
    run_id: int | None,
) -> int:
    if new_only and run_id is None:
        runs = store.list_runs(limit=1)
        if not runs:
            print("No runs available — cannot list new-only jobs.")
            return 1
        run_id = runs[0]["id"]

    jobs = store.list_jobs(new_only=new_only, run_id=run_id, limit=limit)
    label = f"new in run {run_id}" if new_only else "stored"
    _print_jobs(jobs, label)
    return 0


def _cmd_recurring(store: JobStore, min_seen: int, limit: int) -> int:
    jobs = store.list_recurring_jobs(min_times_seen=min_seen, limit=limit)
    _print_jobs(jobs, f"Recurring jobs (seen >={min_seen}x)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Job platform operator utilities")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Quick platform summary")
    sub.add_parser("stats", help="Detailed database statistics")
    sub.add_parser("health", help="Operational validation (DB, schema, tables)")

    p_recent = sub.add_parser("recent", help="Most recently seen jobs")
    p_recent.add_argument("--limit", type=int, default=20)

    p_companies = sub.add_parser("companies", help="Top companies by job count")
    p_companies.add_argument("--limit", type=int, default=15)

    p_search = sub.add_parser("search", help="Keyword search in title/company/location")
    p_search.add_argument("query", help='e.g. "python"')
    p_search.add_argument("--limit", type=int, default=30)

    p_source = sub.add_parser("source", help="Jobs from a source (default: careers)")
    p_source.add_argument("name", help="Source name or alias")
    p_source.add_argument("--limit", type=int, default=30)

    p_runs = sub.add_parser("runs", help="List recent scrape runs")
    p_runs.add_argument("--limit", type=int, default=10)

    p_jobs = sub.add_parser("jobs", help="List stored jobs")
    p_jobs.add_argument("--limit", type=int, default=20)
    p_jobs.add_argument("--new-only", action="store_true")
    p_jobs.add_argument("--run-id", type=int, default=None)

    p_recurring = sub.add_parser("recurring", help="Jobs seen multiple times across runs")
    p_recurring.add_argument("--min-seen", type=int, default=2)
    p_recurring.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()

    if hasattr(args, "limit") and args.limit is not None and args.limit < 1:
        print("Error: --limit must be at least 1.")
        return 1

    store = JobStore()

    commands = {
        "health": lambda: _cmd_health(store),
        "status": lambda: _cmd_status(store),
        "stats": lambda: _cmd_stats(store),
        "recent": lambda: _cmd_recent(store, args.limit),
        "companies": lambda: _cmd_companies(store, args.limit),
        "search": lambda: _cmd_search(store, args.query, args.limit),
        "source": lambda: _cmd_source(store, args.name, args.limit),
        "runs": lambda: _cmd_runs(store, args.limit),
        "jobs": lambda: _cmd_jobs(store, args.limit, args.new_only, args.run_id),
        "recurring": lambda: _cmd_recurring(store, args.min_seen, args.limit),
    }
    return commands[args.command]()


if __name__ == "__main__":
    sys.exit(main())
