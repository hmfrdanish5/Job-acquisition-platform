# Job Acquisition Platform

Local operator tool: collect job listings from configured career pages and public ATS boards, store them in SQLite, view them in a small Flask UI, and export CSV.

It is an acquisition platform with pluggable sources — not a Cloudflare-bypass scraper.

## What it does

1. You collect from a URL or from a configured **target**.
2. An adapter fetches listings (Greenhouse / Lever / Ashby / SmartRecruiters public APIs, or HTML).
3. Jobs are normalised, de-duplicated, and merged into SQLite without wiping good fields with `N/A`.
4. The Jobs table shows results. CSV export is available from that page.

## Supported source types

| Adapter | When it is used |
|---|---|
| Greenhouse | `boards.greenhouse.io`, `job-boards.greenhouse.io` |
| Lever | `jobs.lever.co` |
| Ashby | `jobs.ashbyhq.com` |
| SmartRecruiters | `jobs.smartrecruiters.com`, `careers.smartrecruiters.com` (public Posting API, no auth) |
| HTML | anything else, via Chromium + JSON-LD / job links |

Adding a **company** is configuration (`targets.json`). Adding a **new ATS type** is a small adapter module. See `ACQUISITION_ARCHITECTURE.md`.

## Source registry

Copy `targets.example.json` to `data/targets.json` and enable the rows you want:

```json
{
  "targets": [
    {
      "id": "stripe_greenhouse",
      "name": "Stripe",
      "url": "https://boards.greenhouse.io/stripe",
      "company": "Stripe",
      "enabled": true,
      "keywords": "engineer, intern"
    }
  ]
}
```

Comma-separated `keywords` are **OR** terms, matched case-insensitively against title, location, and company. Empty keywords means no filter.

## Batch collection

```bash
python ops_cli.py targets
python ops_cli.py collect stripe_greenhouse
python ops_cli.py collect-all
```

`collect-all` runs enabled targets **one after another**. A blocked or failed target does not stop the rest. Each target gets its own scrape run.

## Run statuses

| Status | Meaning |
|---|---|
| `completed` | Jobs stored; CSV export succeeded or was skipped |
| `export_failed` | Jobs stored; CSV write failed |
| `empty` | Fetch succeeded, zero listings (not an operational crash) |
| `failed` | Blocked page, unsafe URL, or exception |
| `imported` / `skipped` | CSV import outcomes |

Blocked sites are `failed` with a `blocked:` reason. This project does **not** try to defeat Cloudflare or other human checks.

## URL safety

Every navigation target is checked before use: start URL, careers-link follow, pagination, and the URL after redirects. Localhost and private/link-local/loopback addresses are rejected.

## Persistence

- SQLite at `data/jobs.db` (gitignored)
- Job identity stays on the `careers` catalog so historical rows keep matching
- Adapter kind and target id are stored on the **run**
- Re-seeing a job updates `last_seen_at` / `times_seen` and fills missing fields; `N/A` never overwrites a real salary/title/URL

Schema version **3** adds `scrape_runs.target_key` and `scrape_runs.adapter_kind` additively.

## Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
python app.py
```

Open http://127.0.0.1:5000 — localhost only, no login. Do not expose it.

CLI collection of a single URL:

```bash
python main.py "https://jobs.lever.co/example" --company Example --keyword "engineer, intern"
```

```bash
python ops_cli.py health
python ops_cli.py status
python import_csv.py jobs.csv
```

## Tests

```bash
python -m unittest discover -s tests -v
```

## Limits

- Cloudflare / “verify you are human” pages stop collection.
- HTML extraction is heuristic; public ATS APIs are more reliable.
- Keyword match is substring OR, not a search engine.
- Respect site terms. Collect only from pages you are allowed to access.
