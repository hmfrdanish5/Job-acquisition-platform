# Job Acquisition Platform

Collect job listings from a **company careers page**, view them in a simple local web UI, and download a CSV.

## What it does

1. You paste a careers / jobs URL (optional company name and keyword).
2. The app loads the page and extracts openings.
3. Listings are shown in the Jobs table and stored locally in SQLite.
4. You can download the results as `jobs.csv`.

Public job boards are preferred when the URL is one of:

- Greenhouse (`boards.greenhouse.io`, `job-boards.greenhouse.io`)
- Lever (`jobs.lever.co`)
- Ashby (`jobs.ashbyhq.com`)

Those use each board’s public JSON API. Other sites are opened in Chromium and parsed from JSON-LD or job links.

## Limits (Cloudflare and similar)

Many career sites sit behind Cloudflare or another bot check. This project **does not bypass those protections**. If the page shows “verify you are human” or similar, collection stops and the UI explains why.

Best results come from:

- Greenhouse / Lever / Ashby board URLs
- Plain HTML careers pages
- Direct `/careers` or `/jobs` URLs rather than a marketing homepage

Only collect from sites you are allowed to access. Respect each site’s terms of use.

## Requirements

- Python 3.10+
- Windows, macOS, or Linux

## Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

On macOS/Linux, activate with `source venv/bin/activate`.

## Run the web app

From the project root:

```bash
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000).

The UI binds to localhost only and has no login. Do not expose it on a public network.

## Command line

```bash
python main.py "https://jobs.lever.co/example" --company "Example" --keyword engineer
```

CSV import into the local database:

```bash
python import_csv.py jobs.csv
```

Optional operator queries:

```bash
python ops_cli.py status
python ops_cli.py search "python"
python ops_cli.py health
```

## Project layout

```
app.py                 Web UI entry point
main.py                CLI collection
pipeline.py            Scrape → clean → save → CSV
scraper.py             ATS APIs + Playwright HTML collection
extractors.py          JSON-LD and HTML parsing
dashboard/             Flask routes and templates
data/jobs.db           Created on first run (gitignored)
```

## Tests

```bash
python -m unittest discover -s tests -v
```
