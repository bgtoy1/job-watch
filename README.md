# Job watch

Checks target companies' own job boards every 3 hours and records matching roles.

- `watchlist.json`: companies to check and the title, level, location and pay filters
- `fetch_jobs.py`: the checker (Python standard library only)
- `.github/workflows/check-jobs.yml`: runs the checker on a schedule and saves results

Results after each run:

- `reports/latest.md`: new and closed roles since the last run, companies that failed, all open matches
- `data/open.json`: open matches, newest first
- `data/events.jsonl`: history of new and closed roles
- `data/company_status.json`: whether each company was checked successfully

Phone alerts: add a repository secret `NTFY_TOPIC` with your ntfy topic name.

Run it locally: `python3 fetch_jobs.py` (or `python3 fetch_jobs.py "Netflix" "Yahoo"` for specific companies).
Test without network: `python3 test_offline.py`.
