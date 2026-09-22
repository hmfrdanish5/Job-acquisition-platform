"""
Turn career-page HTML or ATS JSON into job dicts.

Public job-board APIs (Greenhouse, Lever, Ashby) are used when the URL
clearly points at those platforms. Everything else is parsed from HTML:
JSON-LD JobPosting first, then conservative link heuristics.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from config import EXPORT

logger = logging.getLogger(__name__)

_NA = EXPORT["missing_value"]

_JOB_PATH = re.compile(
    r"/(jobs?|careers?|opportunit(?:y|ies)|positions?|openings?|vacancies|postings?|vacancy)(/|$)",
    re.I,
)
_SKIP_HREF = re.compile(
    r"(login|sign[-_]?in|privacy|cookie|terms|blog|news|mailto:|javascript:|#$)",
    re.I,
)
_SKIP_TEXT = re.compile(
    r"^(view all|see all|all jobs|careers|jobs|apply now|learn more|read more)$",
    re.I,
)


def _text(value: Any) -> str:
    if value is None:
        return _NA
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    return cleaned if cleaned else _NA


def _job(
    *,
    title: str,
    company: str = _NA,
    location: str = _NA,
    posted: str = _NA,
    salary: str = _NA,
    url: str = _NA,
) -> dict[str, str] | None:
    title = _text(title)
    if title == _NA:
        return None
    return {
        "company_name": _text(company),
        "job_title": title,
        "job_location": _text(location),
        "posting_date": _text(posted),
        "salary": _text(salary),
        "job_url": _text(url),
    }


def detect_ats(url: str) -> tuple[str, str | None]:
    """Return (kind, board_slug) for known public job boards."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.strip("/")
    parts = [p for p in path.split("/") if p]
    query = parse_qs(parsed.query)

    if "greenhouse.io" in host:
        slug = query.get("for", [None])[0]
        if not slug and parts:
            slug = parts[0]
        return "greenhouse", slug

    if host.endswith("lever.co") or host == "jobs.lever.co":
        slug = parts[0] if parts else None
        return "lever", slug

    if "ashbyhq.com" in host:
        slug = parts[0] if parts else None
        return "ashby", slug

    if "smartrecruiters.com" in host:
        if "companies" in parts:
            idx = parts.index("companies")
            if idx + 1 < len(parts) and parts[idx + 1] not in {"postings"}:
                return "smartrecruiters", parts[idx + 1]
        slug = parts[0] if parts else None
        return "smartrecruiters", slug

    return "html", None


def jobs_from_greenhouse_payload(payload: Any, company_hint: str = "") -> list[dict]:
    jobs_raw = payload.get("jobs", []) if isinstance(payload, dict) else []
    out: list[dict] = []
    for item in jobs_raw:
        if not isinstance(item, dict):
            continue
        loc = ""
        location = item.get("location")
        if isinstance(location, dict):
            loc = location.get("name") or ""
        offices = item.get("offices") or []
        if not loc and offices and isinstance(offices[0], dict):
            loc = offices[0].get("name") or ""
        parsed = _job(
            title=item.get("title"),
            company=company_hint or item.get("company_name") or "",
            location=loc,
            posted=item.get("updated_at") or item.get("first_published"),
            url=item.get("absolute_url"),
        )
        if parsed:
            posted = parsed["posting_date"]
            if posted and "T" in posted:
                parsed["posting_date"] = posted.split("T")[0]
            out.append(parsed)
    return out


def jobs_from_lever_payload(payload: Any, company_hint: str = "") -> list[dict]:
    if not isinstance(payload, list):
        return []
    out: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        cats = item.get("categories") or {}
        loc = ""
        if isinstance(cats, dict):
            loc = cats.get("location") or cats.get("team") or ""
        parsed = _job(
            title=item.get("text") or item.get("title"),
            company=company_hint,
            location=loc,
            posted=str(item.get("createdAt") or item.get("createdAtDate") or ""),
            url=item.get("hostedUrl") or item.get("applyUrl"),
        )
        if parsed:
            out.append(parsed)
    return out


def jobs_from_ashby_payload(payload: Any, company_hint: str = "") -> list[dict]:
    jobs_raw = []
    if isinstance(payload, dict):
        jobs_raw = payload.get("jobs") or payload.get("jobPostings") or []
    out: list[dict] = []
    for item in jobs_raw:
        if not isinstance(item, dict):
            continue
        loc = item.get("location") or ""
        if isinstance(loc, dict):
            loc = loc.get("name") or loc.get("label") or ""
        parsed = _job(
            title=item.get("title") or item.get("jobTitle"),
            company=company_hint,
            location=loc,
            posted=item.get("publishedDate") or item.get("publishedAt") or "",
            url=item.get("jobUrl") or item.get("applyUrl") or item.get("id"),
        )
        if parsed:
            out.append(parsed)
    return out


def jobs_from_smartrecruiters_payload(payload: Any, company_hint: str = "") -> list[dict]:
    rows: list[Any] = []
    if isinstance(payload, dict):
        rows = payload.get("content") or payload.get("postings") or []
    elif isinstance(payload, list):
        rows = payload
    out: list[dict] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        loc = item.get("location") or {}
        location = ""
        if isinstance(loc, dict):
            location = ", ".join(
                str(part)
                for part in (loc.get("city"), loc.get("region"), loc.get("country"))
                if part
            )
        company = company_hint
        org = item.get("company") or {}
        if not company and isinstance(org, dict):
            company = org.get("name") or ""
        ref = item.get("ref") or item.get("applyUrl") or ""
        posting_id = str(item.get("id") or "")
        url = ref if isinstance(ref, str) and ref.startswith("http") else posting_id
        parsed = _job(
            title=item.get("name") or item.get("title"),
            company=company,
            location=location,
            posted=item.get("releasedDate") or item.get("released") or "",
            url=url,
        )
        if parsed:
            posted = parsed["posting_date"]
            if posted and "T" in posted:
                parsed["posting_date"] = posted.split("T")[0]
            out.append(parsed)
    return out


def _walk_jsonld(node: Any) -> list[dict]:
    found: list[dict] = []
    if isinstance(node, list):
        for item in node:
            found.extend(_walk_jsonld(item))
        return found
    if not isinstance(node, dict):
        return found

    types = node.get("@type") or node.get("type") or ""
    if isinstance(types, list):
        types = " ".join(str(t) for t in types)
    if "jobposting" in str(types).lower():
        org = node.get("hiringOrganization") or {}
        company = org.get("name") if isinstance(org, dict) else org
        location = _jsonld_location(node.get("jobLocation"))
        salary = _jsonld_salary(node.get("baseSalary"))
        parsed = _job(
            title=node.get("title"),
            company=company,
            location=location,
            posted=node.get("datePosted"),
            salary=salary,
            url=node.get("url") or node.get("@id"),
        )
        if parsed:
            found.append(parsed)

    if "@graph" in node:
        found.extend(_walk_jsonld(node["@graph"]))
    return found


def _jsonld_location(value: Any) -> str:
    if not value:
        return _NA
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        address = value.get("address") or value
        if isinstance(address, dict):
            parts = [
                address.get("addressLocality"),
                address.get("addressRegion"),
                address.get("addressCountry"),
            ]
            return ", ".join(str(p) for p in parts if p)
        return str(value.get("name") or "")
    return _NA


def _jsonld_salary(value: Any) -> str:
    if not value:
        return _NA
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return _NA
    amount = value.get("value") or value
    currency = value.get("currency") or ""
    if isinstance(amount, dict):
        low = amount.get("minValue") or amount.get("value")
        high = amount.get("maxValue")
        unit = amount.get("unitText") or value.get("unitText") or ""
        if low and high:
            text = f"{low}–{high}"
        else:
            text = str(low or "")
        bits = [currency, text, unit]
        return " ".join(str(b) for b in bits if b).strip() or _NA
    return _text(amount)


def jobs_from_jsonld(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        out.extend(_walk_jsonld(data))
    return out


def jobs_from_html_links(html: str, page_url: str, company_hint: str = "") -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    out: list[dict] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        if not href or href.startswith("javascript:"):
            continue
        absolute = urljoin(page_url, href)
        if absolute in seen:
            continue
        if _SKIP_HREF.search(absolute) or _SKIP_HREF.search(href):
            continue
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if not _looks_like_job_link(href, absolute):
            continue

        title = _text(anchor.get_text(" ", strip=True))
        if title == _NA or _SKIP_TEXT.match(title):
            aria = _text(anchor.get("aria-label"))
            title = aria
        if title == _NA or _SKIP_TEXT.match(title):
            continue
        if len(title) > 180:
            continue

        location = _nearby_location(anchor)
        parsed_job = _job(
            title=title,
            company=company_hint,
            location=location,
            url=absolute,
        )
        if parsed_job:
            seen.add(absolute)
            out.append(parsed_job)

    return out


def _looks_like_job_link(href: str, absolute: str) -> bool:
    if _JOB_PATH.search(href) or _JOB_PATH.search(absolute):
        return True
    lowered = f"{href} {absolute}".lower()
    return any(
        token in lowered
        for token in ("gh_jid", "lever.co", "ashbyhq.com", "smartrecruiters.com", "myworkdayjobs.com")
    )


def _nearby_location(anchor) -> str:
    parent = anchor.parent
    for _ in range(3):
        if parent is None:
            break
        text = parent.get_text(" ", strip=True)
        match = re.search(
            r"(Remote|Hybrid|On[- ]?site|[A-Z][a-zA-Z]+(?:,\s*[A-Z]{2})?)",
            text,
        )
        if match and match.group(0).lower() not in {"apply", "view"}:
            candidate = match.group(0)
            if candidate.lower() != (anchor.get_text(strip=True) or "").lower():
                return candidate
        parent = parent.parent
    return _NA


def extract_jobs_from_html(html: str, page_url: str, company_hint: str = "") -> list[dict]:
    jobs = jobs_from_jsonld(html)
    if jobs:
        if company_hint:
            for job in jobs:
                if job["company_name"] == _NA:
                    job["company_name"] = company_hint
        logger.info("[EXTRACT] %s job(s) from JSON-LD", len(jobs))
        return jobs

    jobs = jobs_from_html_links(html, page_url, company_hint=company_hint)
    logger.info("[EXTRACT] %s job(s) from HTML links", len(jobs))
    return jobs


def find_careers_link(html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for anchor in soup.find_all("a", href=True):
        label = (anchor.get_text(" ", strip=True) or "").lower()
        href = anchor["href"]
        if any(word in label for word in ("career", "jobs", "openings", "join us")):
            absolute = urljoin(page_url, href)
            if urlparse(absolute).scheme in ("http", "https"):
                return absolute
    return None
