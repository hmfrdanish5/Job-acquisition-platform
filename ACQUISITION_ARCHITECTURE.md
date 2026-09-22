# Acquisition architecture

```
target configuration (targets.json)
        ↓
source detection (sources.registry.select_source)
        ↓
source adapter (greenhouse | lever | ashby | smartrecruiters | html)
        ↓
raw job dicts (shared field shape)
        ↓
pipeline (normalise → in-run dedupe → keyword OR filter)
        ↓
SQLite (field-preserving upsert, run diagnostics)
        ↓
export / dashboard / ops_cli
```

## Concepts (keep them distinct)

| Concept | What it is | Where it lives |
|---|---|---|
| Adapter kind | How we fetch (Greenhouse API, HTML, …) | `sources/*`, `scrape_runs.adapter_kind` |
| Acquisition target | A company URL you want to collect | `targets.json` / `data/targets.json` |
| Job catalog | Canonical stored listings | `jobs` keyed by `(source_id=careers, dedupe_key)` |
| Run | One collection attempt | `scrape_runs` + `run_jobs` |

Historical jobs stay on `sources.name = careers`. New collections still persist there so `times_seen` and field merge keep working. The run row records `adapter_kind` and `target_key`.

## How to add an acquisition target

1. Copy `targets.example.json` to `data/targets.json` if needed.
2. Append an object: `id`, `name`, `url`, optional `company`, `keywords`, `enabled`.
3. Run `python ops_cli.py collect YOUR_ID` or Collect on `/targets`.

No Python change.

## How to add a source adapter

1. Create `sources/your_source.py` with `name`, `display_name`, `matches(url)`, `collect(request) -> CollectionResult`.
2. Parse into the shared job fields (`company_name`, `job_title`, `job_location`, `posting_date`, `salary`, `job_url`).
3. Register the instance in `sources/registry.py` **before** the HTML fallback.
4. Extend `extractors.detect_ats` if URL detection belongs there.
5. Add payload/unit tests. Do not put SQL, Flask, or persistence in the adapter.

Adapters may use public HTTP APIs or Playwright. They must call `url_safety` before navigating. They must not implement CAPTCHA solving, proxies, stealth, or credential vaults.

## What does not belong in an adapter

- SQLite / `JobStore`
- Dashboard templates
- CSV export
- Keyword policy (pipeline/`filters.py`)
- Field-merge policy (`choose_better_value`)
- Anti-bot escalation

## Why there is no anti-bot escalation

Blocked pages are an honest `failed` / `blocked` outcome. Circumventing Cloudflare (or equivalent) is out of scope: it is unstable, hostile to site operators, and not required for the ATS APIs this platform prefers.

## Run status model

- `completed` — listings persisted; export ok or not requested
- `export_failed` — listings persisted; CSV write failed (`exported_count = 0`)
- `empty` — fetch finished, zero jobs
- `failed` — block, unsafe URL, or exception
- `imported` / `skipped` — CSV import

## Schema v3

Additive columns on `scrape_runs`:

- `target_key TEXT` — registry id, nullable for ad-hoc URL collections
- `adapter_kind TEXT` — greenhouse / lever / ashby / smartrecruiters / html / csv

Existing `sources`, `jobs`, and `run_jobs` rows are unchanged. `Database.initialize()` migrates v2 files in place.
