"""Job-posting extraction helpers.

Prefer schema.org JobPosting JSON-LD, which major ATS products publish, then
fall back to conservative title/content heuristics. Every returned field is a
draft for user review; the caller never persists a scrape automatically.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup


def _plain(value: Any, limit: int = 100_000) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = ", ".join(_plain(item, 10_000) for item in value if item is not None)
    elif isinstance(value, dict):
        value = value.get("name") or value.get("value") or ""
    soup = BeautifulSoup(html.unescape(str(value)), "html.parser")
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
    return text[:limit]


def _job_nodes(value: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            nodes.extend(_job_nodes(item))
    elif isinstance(value, dict):
        kind = value.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if any(str(item).casefold() == "jobposting" for item in kinds):
            nodes.append(value)
        graph = value.get("@graph")
        if graph is not None:
            nodes.extend(_job_nodes(graph))
    return nodes


def _location(value: Any) -> str:
    locations = value if isinstance(value, list) else [value]
    rendered: list[str] = []
    for item in locations:
        if not isinstance(item, dict):
            text = _plain(item)
        else:
            address = item.get("address") or item
            if isinstance(address, dict):
                parts = [
                    address.get("addressLocality"), address.get("addressRegion"),
                    address.get("addressCountry"),
                ]
                text = ", ".join(_plain(part, 200) for part in parts if part)
            else:
                text = _plain(address, 500)
        if text and text not in rendered:
            rendered.append(text)
    return " · ".join(rendered)[:1000]


def _salary(value: Any) -> str:
    if not isinstance(value, dict):
        return _plain(value, 500)
    currency = _plain(value.get("currency"), 20)
    raw = value.get("value")
    if isinstance(raw, dict):
        low = raw.get("minValue")
        high = raw.get("maxValue")
        exact = raw.get("value")
        unit = _plain(raw.get("unitText"), 40).lower()
        if low is not None and high is not None:
            amount = f"{low}–{high}"
        else:
            amount = str(exact if exact is not None else low if low is not None else high or "")
        return " ".join(part for part in (currency, amount, f"per {unit}" if unit else "") if part)[:500]
    return " ".join(part for part in (currency, _plain(raw, 400)) if part)[:500]


def extract_job_posting(html_text: str, url: str, readable: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html_text or "", "html.parser")
    candidates: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        try:
            candidates.extend(_job_nodes(json.loads(script.string or script.get_text() or "null")))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue

    job = candidates[0] if candidates else {}
    org = job.get("hiringOrganization") if isinstance(job.get("hiringOrganization"), dict) else {}
    title_tag = soup.find("meta", property="og:title") or soup.find("title")
    page_title = ""
    if title_tag:
        page_title = _plain(title_tag.get("content") if title_tag.name == "meta" else title_tag.get_text(), 500)

    title = _plain(job.get("title"), 500)
    company = _plain(org.get("name"), 500)
    if not title and page_title:
        # Common ATS titles: "Role - Company", "Role | Company", "Role at Company".
        at_match = re.match(r"(.+?)\s+at\s+(.+?)(?:\s*[|–—-].*)?$", page_title, re.I)
        if at_match:
            title, company = _plain(at_match.group(1), 500), _plain(at_match.group(2), 500)
        else:
            title = re.split(r"\s+[|–—]\s+", page_title, maxsplit=1)[0][:500]

    description = _plain(job.get("description"), 100_000)
    if not description and readable:
        description = _plain(readable.get("content"), 100_000)

    apply_url = _plain(job.get("url"), 4000) or url
    if not apply_url.startswith(("http://", "https://")):
        apply_url = url
    remote = str(job.get("jobLocationType") or "").casefold() == "telecommute"
    location = _location(job.get("jobLocation"))
    if remote and "remote" not in location.casefold():
        location = f"Remote{' · ' + location if location else ''}"

    return {
        "company": company,
        "title": title,
        "location": location,
        "employment_type": _plain(job.get("employmentType"), 500),
        "salary": _salary(job.get("baseSalary")),
        "job_url": url,
        "apply_url": apply_url,
        "source_site": (urlparse(url).hostname or "").removeprefix("www.")[:255],
        "description": description,
        "posted_at": _plain(job.get("datePosted"), 100),
        "valid_through": _plain(job.get("validThrough"), 100),
        "structured": bool(candidates),
    }
