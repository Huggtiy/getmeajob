#!/usr/bin/env python3
"""Defence & security job aggregator.

Pipeline (all driven by config.json — no keywords or sources live in code):

    config.json -> fetch enabled sources -> keyword + location filter ->
    write docs/jobs.json for the Pages site -> print a summary ->
    optionally email the current list via SMTP.

Stateless by design: every run re-fetches everything and fully rewrites
docs/jobs.json. Nothing is stored between runs.

Filter rules (case-insensitive, whole-word):
  * drop a job if its TITLE matches any exclude keyword;
  * keep it if TITLE + COMPANY matches any include keyword
    (an empty include list keeps everything);
  * drop a job whose LOCATION matches no include_locations entry —
    unless the location is empty/unstated (common in RSS feeds), which
    is kept so feed-only sources aren't wiped out.
"""

import json
import os
import re
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

from fetchers import FETCHERS

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
DOCS_DIR = ROOT / "docs"


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def compile_patterns(keywords):
    # Lookarounds instead of \b so keywords ending in punctuation
    # (e.g. "counter-terrorism") still match on whole-word boundaries.
    return [re.compile(r"(?<!\w)" + re.escape(k.strip()) + r"(?!\w)", re.IGNORECASE)
            for k in keywords if k and k.strip()]


def matches_any(patterns, text):
    return any(p.search(text) for p in patterns)


def fetch_all(config):
    all_jobs, counts = [], {}
    for name, source_cfg in config.get("sources", {}).items():
        if not source_cfg.get("enabled"):
            counts[name] = "off"
            continue
        fetcher = FETCHERS.get(name)
        if fetcher is None:
            print(f"  [warn] no fetcher registered for source '{name}' — skipping", file=sys.stderr)
            counts[name] = "no fetcher"
            continue
        try:
            jobs = fetcher(source_cfg)
        except Exception as exc:
            print(f"  [warn] source '{name}' failed entirely: {exc}", file=sys.stderr)
            jobs = []
        counts[name] = len(jobs)
        all_jobs.extend(jobs)
    return all_jobs, counts


def dedup_by_url(jobs):
    seen = {}
    for job in jobs:
        if job["url"] and job["url"] not in seen:
            seen[job["url"]] = job
    return list(seen.values())


def filter_jobs(jobs, include_pats, exclude_pats, location_pats):
    kept = []
    for job in jobs:
        if matches_any(exclude_pats, job["title"]):
            continue
        if include_pats and not matches_any(include_pats, f"{job['title']} {job['company']}"):
            continue
        if location_pats and job["location"] and not matches_any(location_pats, job["location"]):
            continue
        kept.append(job)
    return kept


def sort_key(job):
    return job.get("posted") or "0000-00-00"


def write_outputs(config, jobs, generated_at):
    DOCS_DIR.mkdir(exist_ok=True)
    payload = {"generated_at": generated_at, "count": len(jobs), "jobs": jobs}
    (DOCS_DIR / "jobs.json").write_text(
        json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    # Same-origin copy so the Pages config editor can load live values.
    (DOCS_DIR / "config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def send_email(jobs):
    host = os.environ.get("SMTP_HOST", "").strip()
    port = os.environ.get("SMTP_PORT", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASS", "").strip()
    if not (host and port and user and password):
        print("Email: SMTP_HOST/PORT/USER/PASS not fully set — skipping.")
        return
    if not jobs:
        print("Email: no matching jobs — nothing to send.")
        return
    to_addr = os.environ.get("EMAIL_TO", user).strip() or user
    lines = [f"* {j['title']} — {j['company']} ({j['location'] or 'location n/a'})\n  {j['url']}"
             for j in jobs]
    msg = MIMEText(f"{len(jobs)} matching role(s) currently open:\n\n" + "\n\n".join(lines))
    msg["Subject"] = f"SITREP: {len(jobs)} defence & security roles"
    msg["From"] = user
    msg["To"] = to_addr
    try:
        with smtplib.SMTP(host, int(port), timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
        print(f"Email: sent {len(jobs)} role(s) to {to_addr}.")
    except Exception as exc:
        print(f"  [warn] email failed: {exc}", file=sys.stderr)


def main():
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    config = load_config()
    include_pats = compile_patterns(config.get("include_keywords", []))
    exclude_pats = compile_patterns(config.get("exclude_keywords", []))
    location_pats = compile_patterns(config.get("include_locations", []))

    print("=== SITREP // defence & security job aggregation ===")
    raw_jobs, counts = fetch_all(config)
    unique_jobs = dedup_by_url(raw_jobs)
    filtered = filter_jobs(unique_jobs, include_pats, exclude_pats, location_pats)

    filtered.sort(key=sort_key, reverse=True)
    write_outputs(config, filtered, now_iso)

    print("--- sources ---")
    for name, count in counts.items():
        print(f"  {name:<12} {count}")
    print("--- results ---")
    print(f"  fetched      {len(raw_jobs)}")
    print(f"  unique       {len(unique_jobs)}")
    print(f"  matched      {len(filtered)}")
    print(f"  wrote docs/jobs.json @ {now_iso}")

    send_email(filtered)


if __name__ == "__main__":
    main()
