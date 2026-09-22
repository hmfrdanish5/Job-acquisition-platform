"""Generic careers-page HTML via Playwright. Does not bypass bot checks."""

from __future__ import annotations

import logging
import time

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from config import BROWSER, CHALLENGE, TIMING
from debugger import save_debug_artifact
from extractors import extract_jobs_from_html, find_careers_link
from sources.base import CollectionRequest, CollectionResult
from sources.errors import AcquisitionError
from url_safety import is_safe_public_url, join_and_validate

logger = logging.getLogger(__name__)


def _is_challenge_text(title: str, body: str, url: str) -> bool:
    blob = f"{title} {body} {url}".lower()
    for phrase in CHALLENGE["body_phrases"]:
        if phrase in blob:
            return True
    for fragment in CHALLENGE["url_fragments"]:
        if fragment in url.lower():
            return True
    return False


class HtmlSource:
    name = "html"
    display_name = "Careers HTML"

    def matches(self, url: str) -> bool:
        return True

    def collect(self, request: CollectionRequest) -> CollectionResult:
        result = CollectionResult(source_kind=self.name)
        ok, reason = is_safe_public_url(request.url)
        if not ok:
            result.error = reason
            result.failure_reason = "unsafe_url"
            return result

        visited: set[str] = set()
        all_jobs: list[dict] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=BROWSER["headless"])
            context = browser.new_context(**BROWSER["context"])
            page = context.new_page()
            page.set_default_timeout(TIMING["page_load_timeout_ms"])
            try:
                current = request.url
                for page_index in range(request.max_pages):
                    if current in visited:
                        break
                    visited.add(current)
                    result.pages_attempted = page_index + 1
                    try:
                        html, final_url = _open_page(page, current, f"page_{page_index + 1}")
                    except AcquisitionError as exc:
                        result.error = str(exc)
                        result.blocked = exc.blocked
                        result.failure_reason = "blocked" if exc.blocked else "navigation"
                        break

                    result.pages_completed = page_index + 1
                    page_jobs = extract_jobs_from_html(
                        html, final_url, request.company_name
                    )
                    if not page_jobs and page_index == 0:
                        careers = find_careers_link(html, final_url)
                        safe, _reason = (
                            join_and_validate(final_url, careers) if careers else (None, "")
                        )
                        if safe and safe not in visited:
                            logger.info("[NAV] Following careers link → %s", safe)
                            time.sleep(TIMING["between_page_delay_s"])
                            current = safe
                            continue

                    all_jobs.extend(page_jobs)
                    next_href = _find_next_href(html)
                    if not next_href:
                        break
                    safe_next, _reason = join_and_validate(final_url, next_href)
                    if not safe_next or safe_next in visited:
                        break
                    time.sleep(TIMING["between_page_delay_s"])
                    current = safe_next
            finally:
                context.close()
                browser.close()

        result.jobs = all_jobs
        logger.info("[DATA] HTML scrape finished — %s listing(s)", len(all_jobs))
        return result


def _open_page(page, url: str, label: str) -> tuple[str, str]:
    ok, reason = is_safe_public_url(url)
    if not ok:
        raise AcquisitionError(reason)

    logger.info("[NAV] %s → %s", label, url)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=TIMING["page_load_timeout_ms"])
    except PlaywrightTimeoutError as exc:
        save_debug_artifact(page, label=f"timeout_{label}")
        raise AcquisitionError("The careers page took too long to load.") from exc

    try:
        page.wait_for_load_state(
            "networkidle",
            timeout=int(TIMING["network_idle_timeout_s"] * 1000),
        )
    except PlaywrightTimeoutError:
        logger.info("[NAV] Network still busy — continuing with current HTML")

    final = page.url
    ok, reason = is_safe_public_url(final)
    if not ok:
        raise AcquisitionError(f"Redirected to a blocked URL: {reason}")

    title = ""
    body = ""
    try:
        title = page.title()
        body_el = page.query_selector("body")
        body = body_el.inner_text()[: CHALLENGE["body_snippet_length"]] if body_el else ""
    except Exception:
        pass

    if _is_challenge_text(title, body, final):
        save_debug_artifact(page, label=f"blocked_{label}")
        raise AcquisitionError(
            "This site is protected (often Cloudflare) and blocked automated access. "
            "Try a public job-board URL such as Greenhouse, Lever, Ashby, or "
            "SmartRecruiters, or a careers page that does not require a human check.",
            blocked=True,
        )

    return page.content(), final


def _find_next_href(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    rel_next = soup.find("a", rel=lambda value: value and "next" in value)
    if rel_next and rel_next.get("href"):
        return rel_next["href"]
    for anchor in soup.find_all("a", href=True):
        label = (anchor.get_text(" ", strip=True) or "").lower().strip()
        if label in {"next", "next page", "older", ">"}:
            return anchor["href"]
    return None
