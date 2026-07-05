# SITREP — defence & security job aggregator

A personal job watch for defence, national security, policy, intelligence,
strategic communications and think-tank roles. It runs **entirely free on
GitHub Actions**, publishes a static job board on **GitHub Pages**, and is
configured through the Pages site itself. APIs and RSS only — no scraping of
LinkedIn/Indeed, no servers, no paid services.

```
config.json ──> aggregator.py ──> fetchers (USAJobs / Greenhouse / Lever / RSS)
                     │
                     ├──> keyword + location filter (whole-word, case-insensitive)
                     └──> docs/jobs.json ──> GitHub Pages board (docs/index.html)
```

Stateless by design: every run re-fetches everything and fully rewrites
`docs/jobs.json`. Nothing is stored between runs.

## How it works

1. A GitHub Actions workflow (`.github/workflows/run.yml`) runs **daily at
   07:00 UTC**, on **manual dispatch**, and on **any push to `main` that
   changes `config.json`** (so saving a new config re-aggregates immediately).
2. `aggregator.py` reads `config.json`, calls every enabled fetcher in
   `fetchers.py`, and filters by keyword and location.
3. The full, filtered, currently-open job list is written to `docs/jobs.json`
   (newest first, with a `generated_at` timestamp) and committed with
   `[skip ci]` — that commit is what refreshes the website.
4. GitHub Pages serves `/docs`: `index.html` is the board,
   `config.html` is the configuration builder.
5. Optionally, the current list is emailed via SMTP.

## The config.json contract

`config.json` at the repo root is the **single source of truth**, shared by
the Python aggregator and the Pages site. No keywords or sources are
hard-coded in Python.

```jsonc
{
  "include_locations": ["London", "Madrid", ...],   // drop if LOCATION matches none; empty/unstated locations pass
  "include_keywords": ["defence", "policy", ...],   // keep if TITLE+COMPANY matches any (whole-word, case-insensitive)
  "exclude_keywords": ["engineer", "software", ...],// drop if TITLE matches any (checked first)
  "sources": {
    "usajobs":    { "enabled": true, "keywords": ["national security"], "results_per_keyword": 25 },
    "greenhouse": { "enabled": true, "boards": ["andurilindustries"] },      // boards.greenhouse.io/<token>
    "lever":      { "enabled": true, "companies": ["palantir"] },            // jobs.lever.co/<company>
    "rss":        { "enabled": true, "feeds": [["Label", "https://…/rss.xml"]] }
  }
}
```

Notes:

* An **empty include list keeps everything**; excludes always win over includes.
* Location filtering only applies to jobs that state a location — RSS items
  usually don't, so they pass and show a blank location on the board.
* `usajobs` ships **disabled**: US federal roles almost always require US
  citizenship. Flip it on from the config page if that ever changes.
* Every fetcher returns the same standard dict:
  `{source, title, company, location, url, posted}` — `posted` is
  `YYYY-MM-DD` or `""`.
* Big ATS platforms used by BAE/Leonardo/QinetiQ/Babcock (Workday,
  SuccessFactors) have no public JSON API — watch those via any RSS feed you
  can find, or a Google Alert RSS feed for e.g.
  `site:careers.baesystems.com`.

## Configuring via the Pages site

Open **`/config.html`** on your Pages URL. It loads the *live* `config.json`
from the repo and mirrors the whole schema as a form (keyword tags, source
toggles, board/company/feed lists). Since Pages is static it can't push
commits, so saving is a two-click commit:

1. **Copy config.json** — puts the generated JSON on your clipboard.
2. **Edit on GitHub** — a deep link to the repo's file editor for
   `config.json`. Select all, paste, commit to `main`.
3. The push triggers the workflow and the board refreshes in ~1 minute.

## Adding a new source

Two small steps — filtering and the site never change:

1. In `fetchers.py`, write one function that takes its config block and
   returns a list of standard job dicts, then register it:

   ```python
   def fetch_workable(cfg):
       jobs = []
       for account in cfg.get("accounts", []):
           ...  # call the API, append _job("workable", title, company, loc, url, posted)
       return jobs

   FETCHERS["workable"] = fetch_workable
   ```

2. Add a matching block under `"sources"` in `config.json`:

   ```json
   "workable": { "enabled": true, "accounts": ["some-company"] }
   ```

The board picks up the new source automatically (badge, filter dropdown).

## Secrets setup (Settings → Secrets and variables → Actions)

All optional — everything else works with **zero keys**.

| Secret | Purpose |
|---|---|
| `USAJOBS_API_KEY` | USAJobs API — free key from <https://developer.usajobs.gov/APIRequest> |
| `USAJOBS_EMAIL` | The email you registered the key with (sent as User-Agent) |
| `SMTP_HOST` / `SMTP_PORT` | e.g. `smtp.gmail.com` / `587` (STARTTLS) |
| `SMTP_USER` / `SMTP_PASS` | Login — for Gmail use an [app password](https://myaccount.google.com/apppasswords) |
| `EMAIL_TO` | Optional; defaults to `SMTP_USER` |

If SMTP secrets are unset the email step is skipped; if the USAJobs secrets
are unset that source is skipped. Both log a warning and never fail the run.

## One-time repo setup

1. **Pages**: Settings → Pages → Deploy from a branch → `main` / `/docs`.
2. **Actions push permission**: the workflow declares `permissions:
   contents: write`, which is sufficient on default settings. If pushes are
   rejected, set Settings → Actions → General → Workflow permissions →
   *Read and write permissions*.
3. Run it once: Actions → **Aggregate jobs** → *Run workflow* (or just wait
   for 07:00 UTC).

## Local run

```bash
pip install -r requirements.txt
USAJOBS_API_KEY=… USAJOBS_EMAIL=… python aggregator.py   # env vars optional
python -m http.server -d docs 8000                        # preview the board
```
