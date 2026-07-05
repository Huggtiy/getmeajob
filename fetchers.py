"""Source fetchers for the defence & security job aggregator.

Every fetcher takes its own source config block (a dict from config.json
under "sources") and returns a list of standard job dicts:

    {"source": ..., "title": ..., "company": ..., "location": ...,
     "url": ..., "posted": ...}   # posted is YYYY-MM-DD or ""

To add a new source: write one function with that signature, add it to the
FETCHERS registry at the bottom, and add a matching block (with an
"enabled" flag) under "sources" in config.json. Nothing else needs to
change — filtering, dedup and output are source-agnostic.

Fetchers must never raise on a single bad board/feed/keyword: log a
warning and carry on, so one dead endpoint can't sink the whole run.
"""

import os
import sys
import time
from datetime import datetime, timezone

import feedparser
import requests

TIMEOUT = 30
HEADERS = {"User-Agent": "defence-job-aggregator/1.0 (personal job search tool)"}


def _warn(msg):
    print(f"  [warn] {msg}", file=sys.stderr)


def _job(source, title, company, location, url, posted):
    return {
        "source": source,
        "title": str(title or "").strip(),
        "company": str(company or "").strip(),
        "location": str(location or "").strip(),
        "url": str(url or "").strip(),
        "posted": str(posted or "").strip(),
    }


def _date_only(value):
    """Trim an ISO-ish timestamp to YYYY-MM-DD; return "" if unusable."""
    s = str(value or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return ""


def fetch_usajobs(cfg):
    """Official USAJobs search API. Free key: https://developer.usajobs.gov/APIRequest"""
    api_key = os.environ.get("USAJOBS_API_KEY", "").strip()
    email = os.environ.get("USAJOBS_EMAIL", "").strip()
    if not api_key or not email:
        _warn("usajobs: USAJOBS_API_KEY / USAJOBS_EMAIL not set — skipping "
              "(request a free key at https://developer.usajobs.gov/APIRequest)")
        return []
    headers = {"Host": "data.usajobs.gov", "User-Agent": email, "Authorization-Key": api_key}
    jobs = []
    for keyword in cfg.get("keywords", []):
        try:
            resp = requests.get(
                "https://data.usajobs.gov/api/search",
                params={"Keyword": keyword,
                        "ResultsPerPage": int(cfg.get("results_per_keyword", 25))},
                headers=headers, timeout=TIMEOUT)
            resp.raise_for_status()
            items = resp.json().get("SearchResult", {}).get("SearchResultItems", [])
        except Exception as exc:
            _warn(f"usajobs: keyword '{keyword}' failed: {exc}")
            continue
        for item in items:
            d = item.get("MatchedObjectDescriptor", {})
            jobs.append(_job(
                "usajobs",
                d.get("PositionTitle"),
                d.get("OrganizationName"),
                d.get("PositionLocationDisplay"),
                d.get("PositionURI"),
                _date_only(d.get("PublicationStartDate")),
            ))
    return jobs


def fetch_greenhouse(cfg):
    """Greenhouse public board API — no key needed."""
    jobs = []
    for board in cfg.get("boards", []):
        company = str(board)
        try:
            meta = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{board}",
                                headers=HEADERS, timeout=TIMEOUT)
            if meta.ok:
                company = meta.json().get("name") or company
        except Exception:
            pass  # cosmetic only — fall back to the board token as the name
        try:
            resp = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs",
                                headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            postings = resp.json().get("jobs", [])
        except Exception as exc:
            _warn(f"greenhouse: board '{board}' failed: {exc}")
            continue
        for p in postings:
            jobs.append(_job(
                "greenhouse",
                p.get("title"),
                company,
                (p.get("location") or {}).get("name"),
                p.get("absolute_url"),
                _date_only(p.get("first_published") or p.get("updated_at")),
            ))
    return jobs


def fetch_lever(cfg):
    """Lever public postings API — no key needed."""
    jobs = []
    for company in cfg.get("companies", []):
        try:
            resp = requests.get(f"https://api.lever.co/v0/postings/{company}",
                                params={"mode": "json"}, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            postings = resp.json()
        except Exception as exc:
            _warn(f"lever: company '{company}' failed: {exc}")
            continue
        for p in postings:
            created = p.get("createdAt")
            posted = ""
            if isinstance(created, (int, float)):
                posted = datetime.fromtimestamp(created / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            jobs.append(_job(
                "lever",
                p.get("text"),
                str(company).replace("-", " ").title(),
                (p.get("categories") or {}).get("location"),
                p.get("hostedUrl"),
                posted,
            ))
    return jobs


def fetch_rss(cfg):
    """Generic RSS/Atom feeds. Config entries are ["Label", "url"] pairs."""
    jobs = []
    for entry in cfg.get("feeds", []):
        try:
            label, url = entry
        except (TypeError, ValueError):
            _warn(f"rss: malformed feed entry {entry!r} — expected [\"Label\", \"url\"]")
            continue
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as exc:
            _warn(f"rss: feed '{label}' failed: {exc}")
            continue
        for e in feed.entries:
            parsed = e.get("published_parsed") or e.get("updated_parsed")
            posted = time.strftime("%Y-%m-%d", parsed) if parsed else ""
            jobs.append(_job("rss", e.get("title"), label, "", e.get("link"), posted))
    return jobs


# Registry: source name in config.json -> fetcher function.
FETCHERS = {
    "usajobs": fetch_usajobs,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "rss": fetch_rss,
}
