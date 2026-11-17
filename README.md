# Job watch

Checks target companies' own job boards every 3 hours and records matching roles.

- `watchlist.json`: companies to check and the title, level, location and pay filters
- `fetch_jobs.py`: the checker (Python standard library only)
- `.github/workflows/check-jobs.yml`: runs the checker every 3 hours, and right after `fetch_jobs.py`, `watchlist.json` or the probe files change
- `probe.py` + `probe_targets.json`: one-off discovery for companies whose job system isn't known yet. Runs after the checker only when `probe_targets.json` changes; results in `reports/probe.md` and `data/probe.json`

Trial readers: companies marked `"trial": true` in `watchlist.json` use new readers. Each run reports what they found (section "Trial readers" in `reports/latest.md`, with sample postings in `data/company_status.json`) but doesn't log their roles until the flag is removed.

Closing rule (2026-11-17): a role is logged as closed only after it is missing from two successful runs in a row (`close_after_misses` in `watchlist.json`), and a run that reads far fewer postings than the last one (under 60%) doesn't count. Search-based readers sometimes return a partial list; Paramount's Finance Leadership Development Program was wrongly logged as closed this way. Tracking parameters (`utm_…`, `feedId`) are stripped from job URLs so a posting keeps the same key. Companies removed from the watchlist have their open roles marked `untracked` rather than closed.

Venture portfolio boards (2026-11-17): `getro` reads venture firms' portfolio job boards (a16z, Upfront Ventures). Roles there belong to many companies, so each title carries the company name in parentheses, e.g. "Director, Business Operations (Luma AI)".

Extra controls in `watchlist.json` (2026-11-17): `filters.level_optional_companies` lists companies whose titles carry no level (Stripe), so the level word isn't required there; a company's `pinned` list holds posting IDs to follow whatever the filters say (Stripe 8155580).

Rate limits: on HTTP 429 or 503 the checker waits (the server's Retry-After, or 15–60 seconds) and retries up to three more times; Netflix and Microsoft requests are spaced 3 seconds apart.

Results after each run:

- `reports/latest.md`: new and closed roles since the last run, companies that failed, all open matches
- `data/open.json`: open matches, newest first
- `data/events.jsonl`: history of new and closed roles
- `data/company_status.json`: whether each company was checked successfully

Phone alerts: add a repository secret `NTFY_TOPIC` with your ntfy topic name.

Run it locally: `python3 fetch_jobs.py` (or `python3 fetch_jobs.py "Netflix" "Yahoo"` for specific companies).
Test without network: `python3 test_offline.py` and `python3 test_readers.py`.
