#!/usr/bin/env python3
"""Defence & security job aggregator.

Pipeline (all driven by config.json — no keywords or sources live in code):

    config.json -> fetch enabled sources -> keyword filter -> dedup vs
    SQLite (jobs.db) -> write docs/jobs.json for the Pages site -> print
    a summary -> optionally email newly seen roles via SMTP.

Filter rules (case-insensitive, whole-word):
  * drop a job if its TITLE matches any exclude keyword;
  * keep it if TITLE + COMPANY matches any include keyword
    (an empty include list keeps everything).

jobs.db remembers every URL ever seen, so "new this run" stays meaningful
between daily GitHub Actions runs. docs/jobs.json always contains the full
currently-open, filtered list.
"""

import json
import os
import re
import smtplib
import sqlite3
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

from fetchers import FETCHERS

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
DB_PATH = ROOT / "jobs.db"
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


def filter_jobs(jobs, include_pats, exclude_pats):
    kept = []
    for job in jobs:
        if matches_any(exclude_pats, job["title"]):
            continue
        if include_pats and not matches_any(include_pats, f"{job['title']} {job['company']}"):
            continue
        kept.append(job)
    return kept


def open_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
        url        TEXT PRIMARY KEY,
        source     TEXT,
        title      TEXT,
        company    TEXT,
        location   TEXT,
        posted     TEXT,
        first_seen TEXT)""")
    return conn


def store_jobs(conn, jobs, now_iso):
    """Stamp each job with first_seen from the DB; insert and return unseen ones."""
    new_jobs = []
    for job in jobs:
        row = conn.execute("SELECT first_seen FROM jobs WHERE url = ?", (job["url"],)).fetchone()
        if row:
            job["first_seen"] = row[0]
        else:
            job["first_seen"] = now_iso
            conn.execute(
                "INSERT INTO jobs (url, source, title, company, location, posted, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job["url"], job["source"], job["title"], job["company"],
                 job["location"], job["posted"], job["first_seen"]))
            new_jobs.append(job)
    conn.commit()
    return new_jobs


def sort_key(job):
    return job.get("posted") or (job.get("first_seen") or "")[:10] or "0000-00-00"


def write_outputs(config, jobs, generated_at):
    DOCS_DIR.mkdir(exist_ok=True)
    payload = {"generated_at": generated_at, "count": len(jobs), "jobs": jobs}
    (DOCS_DIR / "jobs.json").write_text(
        json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    # Same-origin copy so the Pages config editor can load live values.
    (DOCS_DIR / "config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def send_email(new_jobs):
    host = os.environ.get("SMTP_HOST", "").strip()
    port = os.environ.get("SMTP_PORT", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASS", "").strip()
    if not (host and port and user and password):
        print("Email: SMTP_HOST/PORT/USER/PASS not fully set — skipping.")
        return
    if not new_jobs:
        print("Email: no new jobs this run — nothing to send.")
        return
    to_addr = os.environ.get("EMAIL_TO", user).strip() or user
    lines = [f"* {j['title']} — {j['company']} ({j['location'] or 'location n/a'})\n  {j['url']}"
             for j in new_jobs]
    msg = MIMEText(f"{len(new_jobs)} newly seen role(s):\n\n" + "\n\n".join(lines))
    msg["Subject"] = f"SITREP: {len(new_jobs)} new defence & security roles"
    msg["From"] = user
    msg["To"] = to_addr
    try:
        with smtplib.SMTP(host, int(port), timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
        print(f"Email: sent {len(new_jobs)} new role(s) to {to_addr}.")
    except Exception as exc:
        print(f"  [warn] email failed: {exc}", file=sys.stderr)


def main():
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    config = load_config()
    include_pats = compile_patterns(config.get("include_keywords", []))
    exclude_pats = compile_patterns(config.get("exclude_keywords", []))

    print("=== SITREP // defence & security job aggregation ===")
    raw_jobs, counts = fetch_all(config)
    unique_jobs = dedup_by_url(raw_jobs)
    filtered = filter_jobs(unique_jobs, include_pats, exclude_pats)

    conn = open_db()
    new_jobs = store_jobs(conn, filtered, now_iso)
    total_tracked = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    filtered.sort(key=sort_key, reverse=True)
    write_outputs(config, filtered, now_iso)

    print("--- sources ---")
    for name, count in counts.items():
        print(f"  {name:<12} {count}")
    print("--- results ---")
    print(f"  fetched      {len(raw_jobs)}")
    print(f"  unique       {len(unique_jobs)}")
    print(f"  matched      {len(filtered)}")
    print(f"  new this run {len(new_jobs)}")
    print(f"  tracked (db) {total_tracked}")
    print(f"  wrote docs/jobs.json @ {now_iso}")

    send_email(new_jobs)


if __name__ == "__main__":
    main()
