# Job watch

Checks target companies' own job boards every 3 hours and records matching roles.

- `watchlist.json`: companies to check and the title, level, location and pay filters
- `fetch_jobs.py`: the checker (Python standard library only)
- `.github/workflows/check-jobs.yml`: runs the checker every 3 hours, and right after `fetch_jobs.py`, `watchlist.json` or the probe files change
- `probe.py` + `probe_targets.json`: one-off discovery for companies whose job system isn't known yet. Runs after the checker only when `probe_targets.json` changes; results in `reports/probe.md` and `data/probe.json`

Trial readers: companies marked `"trial": true` in `watchlist.json` use new readers. Each run reports what they found (section "Trial readers" in `reports/latest.md`, with sample postings in `data/company_status.json`) but doesn't log their roles until the flag is removed.

Results after each run:

- `reports/latest.md`: new and closed roles since the last run, companies that failed, all open matches
- `data/open.json`: open matches, newest first
- `data/events.jsonl`: history of new and closed roles
- `data/company_status.json`: whether each company was checked successfully

Phone alerts: add a repository secret `NTFY_TOPIC` with your ntfy topic name.

Run it locally: `python3 fetch_jobs.py` (or `python3 fetch_jobs.py "Netflix" "Yahoo"` for specific companies).
Test without network: `python3 test_offline.py` and `python3 test_readers.py`.
