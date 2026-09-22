"""
Collect job listings from a company careers page.

Strategy:
  1. If the URL is Greenhouse, Lever, or Ashby, use that board's public JSON API.
  2. Otherwise load the page in Chromium (many career sites are JavaScript apps).
  3. If Cloudflare or another bot-check page is shown, stop and report it.
     This tool does not attempt to bypass those protections.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urljoin

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from config import BROWSER, CHALLENGE, SEARCH, TIMING
from debugger import save_debug_artifact
from extractors import (
    detect_ats,
    extract_jobs_from_html,
    find_careers_link,
    jobs_from_ashby_payload,
    jobs_from_greenhouse_payload,
    jobs_from_lever_payload,
)

logger = logging.getLogger(__name__)

LAST_SCRAPE_STATS: dict[str, Any] = {
    "pages_attempted": 0,
    "pages_completed": 0,
    "failure_reason": None,
    "source_kind": "html",
    "blocked": False,
}

_REQUEST_HEADERS = {
    "User-Agent": BROWSER["context"]["user_agent"],
    "Accept": "application/json, text/html;q=0.9",
}


def get_last_scrape_stats() -> dict[str, Any]:
    return dict(LAST_SCRAPE_STATS)


def _reset_stats() -> None:
    LAST_SCRAPE_STATS.update(
        {
            "pages_attempted": 0,
            "pages_completed": 0,
            "failure_reason": None,
            "source_kind": "html",
            "blocked": False,
        }
    )


class ScrapeError(Exception):
    def __init__(self, message: str, *, blocked: bool = False) -> None:
        super().__init__(message)
        self.blocked = blocked


def _is_challenge_text(title: str, body: str, url: str) -> bool:
    blob = f"{title} {body} {url}".lower()
    for phrase in CHALLENGE["body_phrases"]:
        if phrase in blob:
            return True
    for fragment in CHALLENGE["url_fragments"]:
        if fragment in url.lower():
            return True
    return False


def _fetch_json(url: str) -> Any:
    response = requests.get(url, headers=_REQUEST_HEADERS, timeout=20)
    response.raise_for_status()
    return response.json()


def _collect_from_ats(url: str, company_hint: str) -> list[dict]:
    kind, slug = detect_ats(url)
    LAST_SCRAPE_STATS["source_kind"] = kind
    LAST_SCRAPE_STATS["pages_attempted"] = 1

    if kind == "html" or not slug:
        return []

    try:
        if kind == "greenhouse":
            payload = _fetch_json(
                f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
            )
            jobs = jobs_from_greenhouse_payload(payload, company_hint)
        elif kind == "lever":
            payload = _fetch_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
            jobs = jobs_from_lever_payload(payload, company_hint)
        elif kind == "ashby":
            payload = _fetch_json(
                f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
            )
            jobs = jobs_from_ashby_payload(payload, company_hint)
        else:
            jobs = []
    except requests.HTTPError as exc:
        logger.warning("[ATS] %s API returned %s — falling back to the page", kind, exc)
        return []
    except requests.RequestException as exc:
        logger.warning("[ATS] %s API failed: %s", kind, exc)
        return []

    LAST_SCRAPE_STATS["pages_completed"] = 1
    logger.info("[ATS] %s/%s — %s job(s)", kind, slug, len(jobs))
    return jobs


def _filter_keyword(jobs: list[dict], keyword: str) -> list[dict]:
    needle = (keyword or "").strip().lower()
    if not needle:
        return jobs
    kept = []
    for job in jobs:
        hay = " ".join(
            [
                job.get("job_title") or "",
                job.get("job_location") or "",
                job.get("company_name") or "",
            ]
        ).lower()
        if needle in hay:
            kept.append(job)
    return kept


def scrape_career_page(
    url: str,
    *,
    company_name: str = "",
    keyword: str = "",
    max_pages: int | None = None,
) -> list[dict]:
    """
    Return raw job dicts from a careers URL.

    Raises ScrapeError when the page is blocked or cannot be loaded.
    """
    _reset_stats()
    max_pages = max_pages or SEARCH["default_max_pages"]
    company_hint = (company_name or "").strip()

    jobs = _collect_from_ats(url, company_hint)
    if jobs:
        return _filter_keyword(jobs, keyword)

    LAST_SCRAPE_STATS["source_kind"] = "html"
    jobs = _collect_with_browser(url, company_hint, max_pages)
    return _filter_keyword(jobs, keyword)


def _collect_with_browser(start_url: str, company_hint: str, max_pages: int) -> list[dict]:
    all_jobs: list[dict] = []
    visited: set[str] = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=BROWSER["headless"])
        context = browser.new_context(**BROWSER["context"])
        page = context.new_page()
        page.set_default_timeout(TIMING["page_load_timeout_ms"])

        try:
            current = start_url
            for page_index in range(max_pages):
                if current in visited:
                    break
                visited.add(current)
                LAST_SCRAPE_STATS["pages_attempted"] = page_index + 1

                html, final_url = _open_page(page, current, f"page_{page_index + 1}")
                LAST_SCRAPE_STATS["pages_completed"] = page_index + 1

                page_jobs = extract_jobs_from_html(html, final_url, company_hint)
                if not page_jobs and page_index == 0:
                    careers = find_careers_link(html, final_url)
                    if careers and careers not in visited:
                        logger.info("[NAV] Following careers link → %s", careers)
                        time.sleep(TIMING["between_page_delay_s"])
                        current = careers
                        continue

                all_jobs.extend(page_jobs)
                next_url = _find_next_page(html, final_url, visited)
                if not next_url:
                    break
                time.sleep(TIMING["between_page_delay_s"])
                current = next_url
        finally:
            context.close()
            browser.close()

    logger.info("[DATA] Browser scrape finished — %s listing(s)", len(all_jobs))
    return all_jobs


def _open_page(page, url: str, label: str) -> tuple[str, str]:
    logger.info("[NAV] %s → %s", label, url)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=TIMING["page_load_timeout_ms"])
    except PlaywrightTimeoutError as exc:
        save_debug_artifact(page, label=f"timeout_{label}")
        raise ScrapeError("The careers page took too long to load.") from exc

    try:
        page.wait_for_load_state(
            "networkidle",
            timeout=int(TIMING["network_idle_timeout_s"] * 1000),
        )
    except PlaywrightTimeoutError:
        logger.info("[NAV] Network still busy — continuing with current HTML")

    title = ""
    body = ""
    try:
        title = page.title()
        body_el = page.query_selector("body")
        body = body_el.inner_text()[: CHALLENGE["body_snippet_length"]] if body_el else ""
    except Exception:
        pass

    if _is_challenge_text(title, body, page.url):
        LAST_SCRAPE_STATS["blocked"] = True
        LAST_SCRAPE_STATS["failure_reason"] = "blocked"
        save_debug_artifact(page, label=f"blocked_{label}")
        raise ScrapeError(
            "This site is protected (often Cloudflare) and blocked automated access. "
            "Try a public job-board URL such as Greenhouse, Lever, or Ashby, "
            "or a careers page that does not require a human check.",
            blocked=True,
        )

    html = page.content()
    return html, page.url


def _find_next_page(html: str, page_url: str, visited: set[str]) -> str | None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    rel_next = soup.find("a", rel=lambda value: value and "next" in value)
    if rel_next and rel_next.get("href"):
        candidate = urljoin(page_url, rel_next["href"])
        if candidate not in visited:
            return candidate

    for anchor in soup.find_all("a", href=True):
        label = (anchor.get_text(" ", strip=True) or "").lower().strip()
        if label in {"next", "next page", "older", ">"}:
            candidate = urljoin(page_url, anchor["href"])
            if candidate not in visited:
                return candidate
    return None
