#!/usr/bin/env python3
"""Check target companies' own job boards for matching roles.

Reads watchlist.json, queries each company's public job-board API
(Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Netflix) or, for companies
with their own career sites, a site-specific reader (Amazon, Microsoft, Apple,
Uber, Atlassian, Google, Meta, SuccessFactors, Avature, Fox, Clinch, Paylocity, Dayforce,
Oracle Recruiting Cloud, Phenom) and venture firms' Getro portfolio job boards,
keeps roles that match the title, level, location and pay filters, and compares
them with the previous run.

Companies marked "trial": true in watchlist.json are read and reported in
company_status.json (with sample postings) but their roles are not logged as
new or closed until the trial flag is removed.

Outputs (committed by the GitHub workflow):
  data/state.json     every matching role ever seen, with first_seen, last_seen, status
  data/open.json      matching roles open now, newest first
  data/events.jsonl   one line per new or closed role, with a timestamp
  data/company_status.json  per-company result of this run (ok / error)
  reports/latest.md   human-readable summary of this run

Standard library only. Set NTFY_TOPIC to get phone alerts for new roles.
"""
import datetime as dt
import gzip
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
REPORTS = os.path.join(ROOT, "reports")
UA = "Mozilla/5.0 (compatible; personal-job-watch/1.0)"
NOW = dt.datetime.now(dt.timezone.utc)
TODAY = NOW.date()


# ---------------------------------------------------------------- HTTP

RATE_LIMIT_EXTRA_RETRIES = 3   # extra tries after HTTP 429/503 (Netflix and Microsoft rate-limit bursts)


def _retry_wait(e, attempt):
    """Seconds to wait after a 429/503: the server's Retry-After when given (capped), else 15s, 30s, 45s..."""
    try:
        ra = int((e.headers or {}).get("Retry-After") or 0)
    except (TypeError, ValueError):
        ra = 0
    return min(ra, 90) if ra > 0 else min(15 * (attempt + 1), 60)


def _body(r):
    raw = r.read()
    if (r.headers.get("Content-Encoding") or "").lower() == "gzip" or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw


def http(url, body=None, timeout=30, retries=2):
    """GET (or POST JSON when body is given) and return parsed JSON."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    last, attempt = None, -1
    limit = retries + RATE_LIMIT_EXTRA_RETRIES
    while attempt < retries:
        attempt += 1
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(_body(r).decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (400, 401, 403, 404, 410, 422):
                break
            if e.code in (429, 503) and attempt < limit:
                retries = max(retries, attempt + 1)  # keep going: rate limits clear after a pause
                time.sleep(_retry_wait(e, attempt))
                continue
        except Exception as e:  # network, JSON
            last = f"{type(e).__name__}: {e}"
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{last} for {url}")


BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def http_raw(url, body=None, headers=None, timeout=30, retries=1, form=False):
    """Return (status, response headers, text) for pages that are not plain JSON APIs.

    body: dict sent as JSON (or urlencoded when form=True). Raises RuntimeError
    on failure, like http().
    """
    h = {"User-Agent": BROWSER_UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
         "Accept-Language": "en-US,en;q=0.9"}
    data = None
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
    h.update(headers or {})
    last, attempt = None, -1
    limit = retries + RATE_LIMIT_EXTRA_RETRIES
    while attempt < retries:
        attempt += 1
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, dict(r.headers), _body(r).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (400, 401, 403, 404, 405, 410, 422):
                break
            if e.code in (429, 503) and attempt < limit:
                retries = max(retries, attempt + 1)
                time.sleep(_retry_wait(e, attempt))
                continue
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{last} for {url}")


def http_text(url, body=None, headers=None, form=False):
    return http_raw(url, body, headers, form=form)[2]


def pause(seconds=0.6):
    time.sleep(seconds)


SLOW_SITE_PAUSE = 3.0  # seconds between requests to sites that rate-limit (Netflix, Microsoft)


# ---------------------------------------------------------------- helpers

def strip_html(s):
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


PAY_RE = re.compile(
    r"\$\s?(\d{2,3}(?:,\d{3})+(?:\.\d{2})?|\d{2,3}(?:\.\d+)?\s?[kK])\s*(?:USD)?\s*(?:-|–|—|to)\s*\$?\s?(\d{2,3}(?:,\d{3})+(?:\.\d{2})?|\d{2,3}(?:\.\d+)?\s?[kK])"
)


def money(x):
    x = x.replace(",", "").replace(" ", "")
    if x[-1] in "kK":
        return int(float(x[:-1]) * 1000)
    return int(float(x))


def parse_pay(text):
    """Return (low, high) of the widest annual range found in text, or None."""
    best = None
    for a, b in PAY_RE.findall(text or ""):
        lo, hi = money(a), money(b)
        if hi < 50000 or hi > 2000000 or lo > hi:
            continue  # hourly figures or noise
        if best is None or hi > best[1]:
            best = (lo, hi)
    return best


def fmt_pay(p):
    if not p:
        return ""
    return f"${round(p[0] / 1000)}–{round(p[1] / 1000)}K"


def iso_from_ms(ms):
    try:
        return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).date().isoformat()
    except Exception:
        return ""


def iso_from_s(s):
    try:
        return dt.datetime.fromtimestamp(int(s), dt.timezone.utc).date().isoformat()
    except Exception:
        return ""


def iso_date(s):
    return (s or "")[:10]


def workday_posted(s):
    """'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' / 'Posted 30+ Days Ago'."""
    s = (s or "").lower()
    if "today" in s:
        return TODAY.isoformat()
    if "yesterday" in s:
        return (TODAY - dt.timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*days", s)
    if m:
        d = (TODAY - dt.timedelta(days=int(m.group(1)))).isoformat()
        return d + ("+" if "+" in s else "")
    return ""


TRACKING_PARAMS = re.compile(r"^(utm_\w+|feedid|source|src|ref|refid|trk|gh_src)$", re.I)


def canonical_url(url):
    """Drop tracking query parameters (utm_*, feedId, …) so the same posting always has the same URL."""
    if not url or "?" not in url:
        return url or ""
    parts = urllib.parse.urlsplit(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
         if not TRACKING_PARAMS.match(k)]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))


def job(company, ats, jid, title, location, url, posted="", pay=None, text=""):
    return {
        "key": f"{ats}:{company['name']}:{jid}",
        "company": company["name"],
        "sector": company.get("sector", ""),
        "ats": ats,
        "id": str(jid),
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "url": url,
        "posted": posted,
        "pay": fmt_pay(pay),
        "pay_top": pay[1] if pay else None,
        "_text": text,
    }


# ---------------------------------------------------------------- fetchers

def fetch_greenhouse(c, terms):
    d = http(f"https://boards-api.greenhouse.io/v1/boards/{c['id']}/jobs?content=true")
    out = []
    for j in d.get("jobs", []):
        text = strip_html(j.get("content", ""))
        posted = iso_date(j.get("first_published") or j.get("updated_at"))
        out.append(job(c, "greenhouse", j["id"], j.get("title"),
                       (j.get("location") or {}).get("name", ""),
                       j.get("absolute_url"), posted, parse_pay(text), text))
    return out


def fetch_lever(c, terms):
    d = http(f"https://api.lever.co/v0/postings/{c['id']}?mode=json")
    out = []
    for j in d:
        cat = j.get("categories") or {}
        locs = [cat.get("location", "")] + (cat.get("allLocations") or [])
        loc = "; ".join(dict.fromkeys(l for l in locs if l))
        if j.get("workplaceType") == "remote" and "remote" not in loc.lower():
            loc = (loc + "; Remote").strip("; ")
        text = (j.get("descriptionPlain") or "") + " " + (j.get("additionalPlain") or "")
        sr = j.get("salaryRange") or {}
        pay = (int(sr["min"]), int(sr["max"])) if sr.get("min") and sr.get("max") and sr.get("interval", "").startswith("per-year") else parse_pay(text)
        out.append(job(c, "lever", j["id"], j.get("text"), loc, j.get("hostedUrl"),
                       iso_from_ms(j.get("createdAt")), pay, text))
    return out


def _ashby_board_page(c):
    """Fallback when Ashby's posting API answers 404 for a board that exists (Moonvalley's
    jobs.ashbyhq.com/moonvalley-ai, 2026-11-18): read the postings embedded in the board page."""
    page = http_text(f"https://jobs.ashbyhq.com/{c['id']}")
    m = re.search(r"window\.__appData\s*=\s*(\{.*?\});\s*</script>", page, re.S)
    if not m:
        raise RuntimeError("ashby board page: no __appData")
    app = json.loads(m.group(1))
    board = app.get("jobBoard") or {}
    if not board and not app.get("organization"):
        raise RuntimeError("ashby board page: board not found")
    jobs = []
    for p in board.get("jobPostings") or []:
        locs = [p.get("locationName") or ""] + [(x.get("locationName") or "") for x in p.get("secondaryLocations") or []]
        if p.get("workplaceType") == "Remote" or p.get("isRemote"):
            locs.append("Remote")
        jobs.append({"id": p.get("id"), "title": p.get("title"), "location": "; ".join(dict.fromkeys(l for l in locs if l)),
                     "jobUrl": f"https://jobs.ashbyhq.com/{c['id']}/{p.get('id')}",
                     "publishedAt": p.get("publishedDate") or "",
                     "compensation": {"compensationTierSummary": p.get("compensationTierSummary") or ""}})
    return {"jobs": jobs}


def fetch_ashby(c, terms):
    try:
        d = http(f"https://api.ashbyhq.com/posting-api/job-board/{c['id']}?includeCompensation=true")
    except RuntimeError as e:
        if "HTTP 404" not in str(e):
            raise
        d = _ashby_board_page(c)
    out = []
    for j in d.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [(x.get("location") or "") for x in j.get("secondaryLocations") or []]
        loc = "; ".join(dict.fromkeys(l for l in locs if l))
        if j.get("isRemote") and "remote" not in loc.lower():
            loc = (loc + "; Remote").strip("; ")
        pay = None
        comp = j.get("compensation") or {}
        for comp_part in comp.get("summaryComponents") or []:
            if comp_part.get("compensationType") == "Salary" and comp_part.get("maxValue"):
                pay = (int(comp_part.get("minValue") or 0), int(comp_part["maxValue"]))
        text = j.get("descriptionPlain") or ""
        pay = pay or parse_pay((comp.get("compensationTierSummary") or "") + " " + text)
        out.append(job(c, "ashby", j["id"], j.get("title"), loc, j.get("jobUrl"),
                       iso_date(j.get("publishedAt")), pay, text))
    return out


def fetch_workday(c, terms):
    base = f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}"
    seen = {}
    for term in terms:
        offset = 0
        while offset < 100:
            d = http(base + "/jobs", {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term})
            posts = d.get("jobPostings") or []
            for p in posts:
                path = p.get("externalPath", "")
                if path and path not in seen:
                    seen[path] = p
            pause()
            if len(posts) < 20:
                break
            offset += 20
    out = []
    for path, p in seen.items():
        rid = (p.get("bulletFields") or [path.rsplit("_", 1)[-1]])[0]
        url = f"https://{c['host']}/en-US/{c['site']}{path}"
        out.append(job(c, "workday", rid, p.get("title"), p.get("locationsText", ""), url,
                       workday_posted(p.get("postedOn")), None, ""))
        out[-1]["_detail"] = base + path
    return out


def workday_detail(j):
    """Fill pay, locations and start date for a Workday match (one extra call)."""
    try:
        d = http(j["_detail"]).get("jobPostingInfo", {})
    except Exception:
        return
    text = strip_html(d.get("jobDescription", ""))
    j["_text"] = text
    pay = parse_pay(text)
    if pay:
        j["pay"], j["pay_top"] = fmt_pay(pay), pay[1]
    extra = [d.get("location", "")] + (d.get("additionalLocations") or [])
    loc = "; ".join(dict.fromkeys(x for x in [j["location"]] + extra if x))
    j["location"] = loc
    if d.get("startDate"):
        j["posted"] = iso_date(d["startDate"])
    if d.get("canApply") is False:
        j["_closed"] = True
    pause()


def fetch_smartrecruiters(c, terms):
    out, seen = [], set()
    for term in terms:
        q = urllib.parse.quote(term)
        d = http(f"https://api.smartrecruiters.com/v1/companies/{c['id']}/postings?limit=100&q={q}")
        for j in d.get("content", []):
            if j["id"] in seen:
                continue
            seen.add(j["id"])
            loc = j.get("location") or {}
            locs = loc.get("fullLocation") or ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)
            if loc.get("remote"):
                locs += "; Remote"
            out.append(job(c, "smartrecruiters", j["id"], j.get("name"), locs,
                           f"https://jobs.smartrecruiters.com/{c['id']}/{j['id']}",
                           iso_date(j.get("releasedDate"))))
        pause()
    return out


def fetch_netflix(c, terms):
    out, seen = [], set()
    base = "https://explore.jobs.netflix.net/api/apply/v2/jobs?domain=netflix.com&sort_by=new"
    for term in terms:
        start = 0
        while start < 200:
            d = http(f"{base}&query={urllib.parse.quote(term)}&start={start}&num=50")
            pos = d.get("positions") or []
            for p in pos:
                if p["id"] in seen:
                    continue
                seen.add(p["id"])
                loc = "; ".join(p.get("locations") or [p.get("location", "")])
                url = p.get("canonicalPositionUrl") or f"https://explore.jobs.netflix.net/careers/job/{p['id']}"
                out.append(job(c, "netflix", p["id"], p.get("name"), loc, url,
                               iso_from_s(p.get("t_create"))))
            pause(SLOW_SITE_PAUSE)
            if not pos or start + len(pos) >= int(d.get("count") or 0):
                break
            start += len(pos)
    return out


# ------------------------------------------- company career sites (added 2026-11-13)
# These read companies that run their own career sites instead of a standard
# job board. Each one is new and marked "trial" in watchlist.json until a run
# shows it returns real postings (see company_status.json "samples").

def _date_text(s):
    """'November 12, 2026' / 'Nov 12, 2026' / RFC 822 / ISO -> YYYY-MM-DD, else ''."""
    s = (s or "").strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z", "%d %B %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def _slug_words(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def fetch_eightfold(c, terms):
    """Eightfold career sites (same API as Netflix), e.g. Microsoft."""
    out, seen = [], set()
    loc = urllib.parse.quote(c.get("location", "United States"))
    base = f"https://{c['host']}/api/apply/v2/jobs?domain={c['domain']}&sort_by=timestamp&location={loc}"
    for term in terms:
        start = 0
        while start < 150:
            d = http(f"{base}&query={urllib.parse.quote(term)}&start={start}&num=50")
            pos = d.get("positions") or []
            for p in pos:
                if p["id"] in seen:
                    continue
                seen.add(p["id"])
                locs = "; ".join(p.get("locations") or [p.get("location", "")])
                url = p.get("canonicalPositionUrl") or f"https://{c['host']}/careers/job/{p['id']}"
                out.append(job(c, "eightfold", p["id"], p.get("name"), locs, url, iso_from_s(p.get("t_create"))))
            pause()
            if not pos or start + len(pos) >= int(d.get("count") or 0):
                break
            start += len(pos)
    return out


def fetch_microsoft_gcs(c, terms):
    """Microsoft's older careers search API (fallback for Eightfold)."""
    out, seen = [], set()
    for term in terms:
        for pg in range(1, 6):
            d = http("https://gcsservices.careers.microsoft.com/search/api/v1/search?"
                     f"q={urllib.parse.quote(term)}&lc=United%20States&l=en_us&pg={pg}&pgSz=20&o=Recent")
            jobs = ((d.get("operationResult") or {}).get("result") or {}).get("jobs") or []
            for j in jobs:
                if j["jobId"] in seen:
                    continue
                seen.add(j["jobId"])
                props = j.get("properties") or {}
                locs = "; ".join(props.get("locations") or [props.get("primaryLocation", "")])
                flex = (props.get("workSiteFlexibility") or "").lower()
                if "100%" in flex and ("home" in flex or "remote" in flex):
                    locs += "; Remote"
                out.append(job(c, "microsoft", j["jobId"], j.get("title"), locs,
                               f"https://jobs.careers.microsoft.com/global/en/job/{j['jobId']}",
                               iso_date(j.get("postingDate"))))
            pause()
            if len(jobs) < 20:
                break
    return out


def fetch_amazon(c, terms):
    """amazon.jobs public search (covers Amazon, Prime Video, MGM, Twitch, Audible)."""
    out, seen = [], set()
    for term in terms:
        for offset in (0, 100, 200):
            d = http("https://www.amazon.jobs/en/search.json?normalized_country_code%5B%5D=USA"
                     f"&base_query={urllib.parse.quote(term)}&result_limit=100&offset={offset}&sort=recent")
            jobs = d.get("jobs") or []
            for j in jobs:
                jid = j.get("id_icims") or j.get("id")
                if jid in seen:
                    continue
                seen.add(jid)
                text = " ".join(strip_html(j.get(k, "")) for k in ("description", "basic_qualifications", "preferred_qualifications"))
                loc = j.get("normalized_location") or j.get("location") or ""
                if j.get("is_virtual") or "virtual" in loc.lower():
                    loc += "; Remote"
                out.append(job(c, "amazon", jid, j.get("title"), loc,
                               "https://www.amazon.jobs" + (j.get("job_path") or f"/en/jobs/{jid}"),
                               _date_text(j.get("posted_date")), parse_pay(text), text))
            pause()
            if len(jobs) < 100:
                break
    return out


def fetch_apple(c, terms):
    """jobs.apple.com search API (needs a CSRF token), with an HTML fallback."""
    out, seen = [], set()

    def add(p):
        pid = str(p.get("positionId") or p.get("id") or "")
        # The search list shows "200694814-0836" (role + team code); the page's embedded data shows
        # "200694814" for the same role, without a location. Count them once, by the role number.
        base_id = pid.split("-")[0]
        if not pid or base_id in seen:
            return
        seen.add(base_id)
        locs = "; ".join(x.get("name") or x.get("city") or x.get("countryName") or ""
                         for x in p.get("locations") or [] if isinstance(x, dict))
        if p.get("homeOffice"):
            locs += "; Remote"
        slug = p.get("transformedPostingTitle") or _slug_words(p.get("postingTitle"))
        out.append(job(c, "apple", pid, p.get("postingTitle"), locs,
                       f"https://jobs.apple.com/en-us/details/{pid}/{slug}",
                       _date_text(p.get("postingDate") or p.get("postDateInGMT"))))

    token_hdr = None
    for path in ("/api/v1/CSRFToken", "/api/csrfToken"):
        try:
            _, hdrs, _ = http_raw("https://jobs.apple.com" + path)
            token = {k.lower(): v for k, v in hdrs.items()}.get("x-apple-csrf-token")
            if token:
                cookie = {k.lower(): v for k, v in hdrs.items()}.get("set-cookie", "")
                token_hdr = {"x-apple-csrf-token": token, "Cookie": cookie.split(";")[0],
                             "Origin": "https://jobs.apple.com", "Referer": "https://jobs.apple.com/en-us/search"}
                api = "https://jobs.apple.com" + path.replace("CSRFToken", "search").replace("csrfToken", "role/search")
                break
        except Exception:
            continue
    for term in terms:
        for page in range(1, 4):
            got = 0
            if token_hdr:
                try:
                    body = {"query": term, "filters": {"locations": ["postLocation-USA"]},
                            "page": page, "locale": "en-us", "sort": "newest"}
                    d = json.loads(http_text(api, body, token_hdr))
                    res = (d.get("res") or d).get("searchResults") or []
                    for p in res:
                        add(p)
                    got = len(res)
                except Exception:
                    pass
                if not got:
                    token_hdr = None  # API gave nothing: read the search page instead
            if not token_hdr:
                page_html = http_text("https://jobs.apple.com/en-us/search?"
                                      f"search={urllib.parse.quote(term)}&location=united-states-USA&sort=newest&page={page}")
                # Server-rendered result list (site layout seen 2026-11-13)
                for li in re.split(r'<li data-core-accordion-item', page_html)[1:]:
                    a = re.search(r'href="/en-us/details/([\d-]+)/([^"?]*)[^"]*"[^>]*>(.*?)</a>', li, re.S)
                    if not a:
                        continue
                    d = re.search(r'class="job-posted-date"[^>]*>(.*?)<', li, re.S)
                    loc = re.search(r'id="search-store-name-container-\d+"[^>]*>(.*?)</span>', li, re.S)
                    add({"positionId": a.group(1), "postingTitle": strip_html(a.group(3)),
                         "transformedPostingTitle": a.group(2), "postingDate": strip_html(d.group(1)) if d else "",
                         "locations": [{"name": strip_html(loc.group(1))}] if loc else []})
                    got += 1
                page_html = page_html.replace('\\"', '"')
                for m in re.finditer(r'"positionId"\s*:\s*"?(\d+)"?', page_html):
                    seg = page_html[max(0, m.start() - 1500):m.end() + 1500]
                    rel = seg[seg.find(m.group(0)):]
                    get = lambda k: (re.search(rf'"{k}"\s*:\s*"((?:[^"\\]|\\.)*)"', rel) or re.search(rf'"{k}"\s*:\s*"((?:[^"\\]|\\.)*)"', seg) or [None, ""])[1]
                    title = get("postingTitle")
                    if not title:
                        continue
                    locs_m = re.search(r'"locations"\s*:\s*\[(.*?)\]', rel, re.S)
                    names = re.findall(r'"(?:name|city)"\s*:\s*"([^"]+)"', locs_m.group(1)) if locs_m else []
                    if not names:
                        continue  # no location in the embedded data: the list entry (with location) covers it
                    add({"positionId": m.group(1), "postingTitle": title, "postingDate": get("postingDate"),
                         "transformedPostingTitle": get("transformedPostingTitle"),
                         "locations": [{"name": n} for n in names], "homeOffice": '"homeOffice":true' in rel[:1500]})
                    got += 1
            pause()
            if got < 20:
                break
    return out


def fetch_uber(c, terms):
    out, seen = [], set()
    hdr = {"x-csrf-token": "x", "Origin": "https://www.uber.com", "Referer": "https://www.uber.com/us/en/careers/list/"}
    for term in terms:
        for page in range(0, 4):
            body = {"limit": 50, "page": page, "params": {"query": term, "location": [{"country": "USA"}]}}
            d = json.loads(http_text("https://www.uber.com/api/loadSearchJobsResults?localeCode=en", body, hdr))
            res = ((d.get("data") or {}).get("results")) or []
            for j in res:
                if j["id"] in seen:
                    continue
                seen.add(j["id"])
                locs = []
                for l in [j.get("location")] + (j.get("allLocations") or []):
                    if isinstance(l, dict):
                        locs.append(", ".join(x for x in [l.get("city"), l.get("region"), l.get("countryName") or l.get("country")] if x))
                out.append(job(c, "uber", j["id"], j.get("title"), "; ".join(dict.fromkeys(x for x in locs if x)),
                               f"https://www.uber.com/global/en/careers/list/{j['id']}/",
                               iso_date(j.get("creationDate")), parse_pay(j.get("description", "")), j.get("description", "")))
            pause()
            if len(res) < 50:
                break
    return out


def fetch_atlassian(c, terms):
    d = http("https://www.atlassian.com/endpoint/careers/listings")
    items = d if isinstance(d, list) else (d.get("listings") or d.get("jobs") or [])
    out = []
    for j in items:
        post = j.get("portalJobPost") or {}
        locs = j.get("locations") or post.get("locations") or []
        loc = "; ".join(x if isinstance(x, str) else (x.get("name") or "") for x in locs)
        text = strip_html(" ".join(str(j.get(k) or "") for k in ("overview", "responsibilities", "qualifications")))
        jid = j.get("id") or post.get("id")
        out.append(job(c, "atlassian", jid, j.get("title") or post.get("title"), loc,
                       f"https://www.atlassian.com/company/careers/details/{jid}",
                       iso_date(post.get("updatedDate") or j.get("updatedDate") or ""), parse_pay(text), text))
    return out


def fetch_google(c, terms):
    """Google / YouTube careers results pages (no public API; parses the HTML)."""
    out, seen = [], set()
    locs = "".join("&location=" + urllib.parse.quote(l) for l in c.get("locations", ["Los Angeles, CA, USA"]))
    levels = "&target_level=DIRECTOR&target_level=ADVANCED"
    for term in terms:
        for page in range(1, 4):
            page_html = http_text("https://www.google.com/about/careers/applications/jobs/results?"
                                  f"q={urllib.parse.quote(term)}{locs}{levels}&page={page}")
            found = 0
            for m in re.finditer(r'jobs/results/(\d{6,})-([a-z0-9\-]+)', page_html):
                jid, slug = m.group(1), m.group(2)
                if jid in seen:
                    continue
                seen.add(jid)
                found += 1
                before = page_html[max(0, m.start() - 4000):m.start()]
                titles = re.findall(r"<h3[^>]*>(.*?)</h3>", before, re.S)
                title = strip_html(titles[-1]) if titles else slug.replace("-", " ").title()
                h3 = before.rfind("<h3")
                block = page_html[max(0, m.start() - 4000) + max(h3, 0):m.start() + 500]
                places = [p.strip() for t in re.findall(r">([^<>]+)<", block) for p in html.unescape(t).split(";")
                          if re.fullmatch(r"\s*[A-Z][A-Za-z .'-]+, [A-Z]{2}, USA\s*", p)]
                loc = "; ".join(dict.fromkeys(places)) or c.get("default_location", "Los Angeles, CA (search filter)")
                out.append(job(c, "google", jid, title, loc,
                               f"https://www.google.com/about/careers/applications/jobs/results/{jid}-{slug}"))
            pause()
            if found < 10:
                break
    return out


META = "https://www.metacareers.com"
META_SEARCH_VARS = {"search_input": {"q": "", "divisions": [], "offices": [], "roles": [], "leadership_levels": [],
                                     "saved_jobs": [], "saved_searches": [], "sub_teams": [], "teams": [],
                                     "is_leadership": False, "is_remote_only": False, "sort_by_new": True,
                                     "results_per_page": None}}


def _meta_url(jid):
    return f"{META}/profile/job_details/{jid}/"


def _meta_walk(obj, found):
    """Collect dicts that look like Meta postings: a long numeric id plus a title."""
    if isinstance(obj, dict):
        jid, title = str(obj.get("id") or ""), obj.get("title")
        if re.fullmatch(r"\d{12,}", jid) and isinstance(title, str) and title.strip():
            found.append(obj)
            return
        for v in obj.values():
            _meta_walk(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _meta_walk(v, found)


def _meta_loc(p):
    locs = p.get("locations") or []
    if isinstance(locs, str):
        locs = [locs]
    return "; ".join(x if isinstance(x, str) else (x.get("name") or x.get("location") or "") for x in locs)


def _meta_from_html(page):
    """Postings embedded in a metacareers page (JSON in script tags), plus bare detail links."""
    flat = page.replace('\\"', '"')
    found = []
    for m in re.finditer(r'"id"\s*:\s*"(\d{12,})"\s*,\s*"title"\s*:\s*"((?:[^"\\]|\\.)+)"(?:\s*,\s*"locations"\s*:\s*\[([^\]]*)\])?', flat):
        try:
            title = json.loads('"' + m.group(2) + '"')
        except ValueError:
            title = m.group(2)
        locs = re.findall(r'"((?:[^"\\]|\\.)+)"', m.group(3) or "")
        found.append({"id": m.group(1), "title": title, "locations": locs})
    return found


def _meta_graphql(terms):
    """Meta's career site loads search results with a GraphQL query. Find the session token and the
    query's id in the search page and its scripts, then run the query once per search term."""
    status, headers, page = http_raw(f"{META}/jobsearch/")
    lsd = re.search(r'"LSD",\[\],\{"token":"([^"]+)"', page) or re.search(r'name="lsd" value="([^"]+)"', page)
    if not lsd:
        raise RuntimeError(f"graphql: no session token on /jobsearch/ ({len(page)} bytes)")
    lsd = lsd.group(1)
    rx = re.compile(r'__d\("(Careers\w*Search\w*Query)_facebookRelayOperation",\[\],\(function\([^)]*\)\{e\.exports="(\d+)"')
    ops = dict(rx.findall(page))
    if not ops:
        srcs = [html.unescape(u) for u in re.findall(r'<script[^>]+src="([^"]+)"', page)]
        for u in list(dict.fromkeys(srcs))[:80]:
            try:
                ops.update(rx.findall(http_raw(u, retries=0)[2]))
            except Exception:
                continue
            if ops:
                break
    if not ops:
        raise RuntimeError("graphql: search query id not found in page scripts")
    name = next((n for n in ops if "Results" in n), next(iter(ops)))
    cookie = "; ".join(x.split(";")[0] for k, x in headers.items() if k.lower() == "set-cookie")
    out, seen = [], set()
    for term in terms:
        variables = json.loads(json.dumps(META_SEARCH_VARS))
        variables["search_input"]["q"] = term
        body = {"av": "0", "__user": "0", "__a": "1", "lsd": lsd, "fb_api_caller_class": "RelayModern",
                "fb_api_req_friendly_name": name, "variables": json.dumps(variables),
                "server_timestamps": "true", "doc_id": ops[name]}
        hdr = {"X-FB-LSD": lsd, "X-FB-Friendly-Name": name, "Origin": META, "Referer": f"{META}/jobsearch/"}
        if cookie:
            hdr["Cookie"] = cookie
        text = http_raw(f"{META}/graphql", body, hdr, form=True)[2]
        text = text[len("for (;;);"):] if text.startswith("for (;;);") else text
        d = json.loads(text.splitlines()[0] if text.strip() else "{}")
        if not isinstance(d, dict) or not d.get("data"):
            err = (d.get("errors") or [{}])[0].get("message", "") if isinstance(d, dict) else ""
            raise RuntimeError(f"graphql {name}: no data {err[:120]}")
        posts = []
        _meta_walk(d["data"], posts)
        for p in posts:
            if str(p["id"]) not in seen:
                seen.add(str(p["id"]))
                out.append(p)
        pause()
    return out


def fetch_meta(c, terms):
    """metacareers.com. Tries (1) job data embedded in the search page, (2) the site's own GraphQL
    search query, (3) the old /jobs search page. Postings link to /profile/job_details/<id>/."""
    posts, errors = [], []
    enc = "".join(f"&offices%5B{i}%5D={urllib.parse.quote(o)}" for i, o in enumerate(c.get("offices", [])))
    for term in terms:
        try:
            posts += _meta_from_html(http_text(f"{META}/jobsearch/?q={urllib.parse.quote(term)}{enc}"))
        except Exception as e:
            errors.append(f"jobsearch page: {str(e).split(' for ')[0][:60]}")
            break
        pause()
    if not posts:
        try:
            posts = _meta_graphql(terms)
        except Exception as e:
            errors.append(str(e).split(" for ")[0][:160])
    if not posts:
        for term in terms:
            try:
                posts += _meta_from_html(http_text(f"{META}/jobs?q={urllib.parse.quote(term)}{enc}"))
            except Exception as e:
                errors.append(f"old /jobs page: {str(e).split(' for ')[0][:60]}")
                break
            pause()
    if not posts:
        raise RuntimeError("meta " + (" | ".join(errors) or "no postings found on any route"))
    out, seen = [], set()
    for p in posts:
        jid = str(p["id"])
        if jid in seen:
            continue
        seen.add(jid)
        out.append(job(c, "meta", jid, p["title"], _meta_loc(p), _meta_url(jid)))
    return out


def fetch_successfactors(c, terms):
    """SAP SuccessFactors career sites (e.g. careers.paramount.com): RSS feed plus the paged HTML search.

    The RSS feed returns only a short, recent slice of each keyword search, so older postings drop out
    of it while still open (an older Paramount posting dropped out of the feed on 2026-11-17
    and was logged closed). The HTML search is paged in full for every term;
    the RSS feed is still read first because it carries pay and the description."""
    out, by_id = [], {}
    host = c["host"]
    max_rows = int(c.get("max_rows", 500))
    for term in terms:
        q = urllib.parse.quote(term)
        try:
            xml = http_text(f"https://{host}/services/rss/job/?locale=en_US&keywords=({q})")
            items = re.findall(r"<item>(.*?)</item>", xml, re.S)
        except Exception:
            items = []
        for it in items:
            g = lambda tag: html.unescape(strip_html((re.search(rf"<{tag}>(.*?)</{tag}>", it, re.S) or [None, ""])[1].replace("<![CDATA[", "").replace("]]>", "")))
            link = canonical_url(g("link"))  # the feed adds ?feedId=…&utm_… tracking; keep the bare job URL
            jid = (re.search(r"/(\d{6,})/?$", link) or [None, link])[1]
            if not link or jid in by_id:
                continue
            title = g("title")
            m = re.match(r"(.*?)\s*\(([^()]*)\)\s*$", title)
            title, loc = (m.group(1), m.group(2)) if m else (title, "")
            desc = g("description")
            by_id[jid] = job(c, "successfactors", jid, title, loc, link, _date_text(g("pubDate")), parse_pay(desc), desc)
            out.append(by_id[jid])
        if items:
            pause()
        start = 0
        while start < max_rows:
            try:
                page_html = http_text(f"https://{host}/search/?q={q}&startrow={start}")
            except Exception:
                if out:  # keep what the feed and earlier pages gave
                    break
                raise
            rows = re.findall(r'<tr class="data-row.*?</tr>', page_html, re.S)
            for r in rows:
                a = re.search(r'href="(/job/[^"]+?/(\d+)/?)"[^>]*class="jobTitle-link[^"]*"[^>]*>(.*?)</a>', r, re.S) \
                    or re.search(r'class="jobTitle-link[^"]*"[^>]*href="(/job/[^"]+?/(\d+)/?)"[^>]*>(.*?)</a>', r, re.S)
                if not a or a.group(2) in by_id:
                    continue
                loc = strip_html((re.search(r'class="jobLocation"[^>]*>(.*?)</span>', r, re.S) or [None, ""])[1])
                date = strip_html((re.search(r'class="jobDate"[^>]*>(.*?)</span>', r, re.S) or [None, ""])[1])
                by_id[a.group(2)] = job(c, "successfactors", a.group(2), strip_html(a.group(3)), loc,
                                        f"https://{host}{a.group(1)}", _date_text(date))
                out.append(by_id[a.group(2)])
            pause()
            if len(rows) < 25:
                break
            start += len(rows)
    return out


def fetch_avature(c, terms):
    """Avature career sites (e.g. jobs.ea.com): search results HTML, 20 per page.

    EA's search ignores the keyword and lists every role, so the whole list is paged once."""
    out, seen = [], set()
    base = c["base"].rstrip("/")  # e.g. https://jobs.ea.com/en_US/careers
    offset = 0
    while offset < 1000:
        page_html = http_text(f"{base}/SearchJobs/?jobRecordsPerPage=20&jobOffset={offset}&listFilterMode=1")
        found = 0
        for m in re.finditer(r'href="([^"]*/careers/JobDetail/([^"/]+)/(\d+))"[^>]*>(.*?)</a>', page_html, re.S):
            url, slug, jid, label = m.groups()
            title = strip_html(label) or slug.replace("-", " ")
            if jid in seen or not title:
                continue
            seen.add(jid)
            found += 1
            after = page_html[m.end():m.end() + 2500]
            loc = strip_html((re.search(r'class="[^"]*location[^"]*"[^>]*>(.*?)</', after, re.S)
                              or re.search(r'class="article__header__text__subtitle"[^>]*>(.*?)</div>', after, re.S)
                              or [None, ""])[1])
            if url.startswith("/"):
                url = re.match(r"https://[^/]+", base).group(0) + url
            out.append(job(c, "avature", jid, title, loc, url))
        pause()
        if found == 0:
            break
        offset += 20
    return out


def fetch_foxcareers(c, terms):
    """foxcareers.com search results (Fox Corporation)."""
    out, seen = [], set()
    for term in terms:
        for page in range(0, 4):
            page_html = http_text(f"https://www.foxcareers.com/Search/SearchResults?keyword={urllib.parse.quote(term)}&page={page}")
            found = 0
            for m in re.finditer(r'href="(/Search/JobDetail/(R\d+)(?:/([a-z0-9\-]+))?)"[^>]*>(.*?)</a>', page_html, re.S | re.I):
                path, jid, slug, label = m.groups()
                if jid in seen:
                    continue
                title = strip_html(label) or (slug or "").replace("-fox-corporation", "").replace("-", " ").title()
                if not title:
                    continue
                seen.add(jid)
                found += 1
                after = strip_html(page_html[m.end():m.end() + 1500])
                place = re.search(r"([A-Z][A-Za-z .'-]+, (?:[A-Z]{2}|California|New York|Arizona)\b)", after)
                out.append(job(c, "foxcareers", jid, title, place.group(1) if place else "",
                               "https://www.foxcareers.com" + path))
            pause()
            if found < 10:
                break
    return out


def fetch_clinch(c, terms):
    """Clinch career sites (e.g. careers.godaddy). Location comes from the URL slug."""
    out, seen = [], set()
    host = c["host"]
    for term in terms:
        for page in range(1, 4):
            page_html = http_text(f"https://{host}/jobs/search?query={urllib.parse.quote(term)}&page={page}")
            found = 0
            for m in re.finditer(r'href="(?:https://[^/"]+)?/jobs/([a-z0-9\-]+)"[^>]*>(.*?)</a>', page_html, re.S):
                slug, label = m.groups()
                title = strip_html(label)
                if slug in ("search",) or slug in seen or not title or len(title) > 150:
                    continue
                seen.add(slug)
                found += 1
                ts = _slug_words(title)
                rest = slug[len(ts):].strip("-") if slug.startswith(ts) else ""
                loc = rest.replace("-", " ").title() if rest else ""
                out.append(job(c, "clinch", slug, title, loc, f"https://{host}/jobs/{slug}"))
            pause()
            if found < 10:
                break
    return out


def fetch_greenhouse_html(c, terms):
    """Greenhouse hosted board page (when the board API returns 404)."""
    out, seen = [], set()
    for page in range(1, 11):
        page_html = http_text(f"https://job-boards.greenhouse.io/{c['id']}?page={page}")
        posts, obj, before = [], {}, len(out)
        i = page_html.find('"jobPosts"')
        if i >= 0:
            try:
                obj, _ = json.JSONDecoder().raw_decode(page_html[page_html.index("{", i):])
                posts = obj.get("data") or []
            except ValueError:
                posts = []
        for p in posts:
            if p.get("id") in seen:
                continue
            seen.add(p.get("id"))
            text = strip_html(p.get("content") or "")
            out.append(job(c, "greenhouse", p.get("id"), p.get("title"), p.get("location") or "",
                           p.get("absolute_url") or f"https://job-boards.greenhouse.io/{c['id']}/jobs/{p.get('id')}",
                           iso_date(p.get("published_at") or p.get("updated_at")), parse_pay(text), text))
        if not posts:
            for m in re.finditer(rf'href="(https://job-boards\.greenhouse\.io/{re.escape(c["id"])}/jobs/(\d+))"[^>]*>(.*?)</a>', page_html, re.S):
                if m.group(2) in seen:
                    continue
                seen.add(m.group(2))
                ps = [strip_html(x) for x in re.findall(r"<p[^>]*>(.*?)</p>", m.group(3), re.S)]
                out.append(job(c, "greenhouse", m.group(2), ps[0] if ps else strip_html(m.group(3)),
                               ps[1] if len(ps) > 1 else "", m.group(1)))
        pause()
        if posts:
            if page >= int(obj.get("total_pages") or 1):
                break
        elif len(out) == before:
            break
    return out


def fetch_paylocity(c, terms):
    """Paylocity public job feed (needs the company GUID)."""
    d = http(f"https://recruiting.paylocity.com/recruiting/v2/api/feed/jobs/{c['id']}")
    items = d.get("jobs") if isinstance(d, dict) else d
    out = []
    for j in items or []:
        loc = j.get("jobLocation") or {}
        locs = ", ".join(x for x in [loc.get("city"), loc.get("state")] if x) if isinstance(loc, dict) else str(loc)
        if j.get("isRemote"):
            locs += "; Remote"
        text = strip_html(j.get("description") or "")
        jid = j.get("jobId") or j.get("id")
        out.append(job(c, "paylocity", jid, j.get("title"), locs,
                       j.get("displayUrl") or f"https://recruiting.paylocity.com/Recruiting/Jobs/Details/{jid}",
                       iso_date(j.get("publishedDate") or ""), parse_pay(text), text))
    return out


DAYFORCE = "https://jobs.dayforcehcm.com"
_DF_ID_KEYS = ("jobPostingId", "postingId", "jobReqId")
_DF_TITLE_KEYS = ("jobTitle", "postingTitle", "title")


def _df_walk(obj, found):
    """Collect dicts that look like job postings (an id key plus a title key)."""
    if isinstance(obj, dict):
        has_id = any(obj.get(k) not in (None, "") for k in _DF_ID_KEYS)
        has_title = any(isinstance(obj.get(k), str) and obj[k].strip() for k in _DF_TITLE_KEYS)
        if has_id and has_title:
            found.append(obj)
            return
        for v in obj.values():
            _df_walk(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _df_walk(v, found)


def _df_location(p):
    parts = []
    for loc in p.get("postingLocations") or p.get("locations") or []:
        if isinstance(loc, str):
            parts.append(loc)
        elif isinstance(loc, dict):
            s = loc.get("formattedAddress") or ", ".join(
                str(x) for x in [loc.get("cityName") or loc.get("city"),
                                 loc.get("stateCode") or loc.get("state"),
                                 loc.get("countryCode") or loc.get("country")] if x)
            if s:
                parts.append(s)
    for k in ("location", "locationName", "formattedAddress"):
        if not parts and isinstance(p.get(k), str):
            parts.append(p[k])
    if p.get("isVirtualLocation") or p.get("hasVirtualLocation") or p.get("isRemote"):
        parts.append("Remote")
    return "; ".join(dict.fromkeys(x.strip() for x in parts if x and x.strip()))


def _df_posted(p):
    for k in ("postingStartTimestampUTC", "postingStartDate", "datePosted", "postedDate", "publishedDate"):
        v = p.get(k)
        if isinstance(v, (int, float)) and v > 10**11:
            return iso_from_ms(v)
        if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v):
            return v[:10]
    return ""


def _df_job(c, p, ns, board):
    jid = next(p[k] for k in _DF_ID_KEYS if p.get(k) not in (None, ""))
    title = next(p[k] for k in _DF_TITLE_KEYS if isinstance(p.get(k), str) and p[k].strip())
    text = strip_html(p.get("jobPostingContent") or p.get("description") or p.get("jobDescription") or "")
    return job(c, "dayforce", jid, title, _df_location(p),
               f"{DAYFORCE}/en-US/{ns}/{board}/jobs/{jid}", _df_posted(p), parse_pay(text), text)


def _df_from_html(page):
    """Postings embedded in the board page (__NEXT_DATA__ or escaped React server payload)."""
    found = []
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if m:
        try:
            _df_walk(json.loads(m.group(1)), found)
        except ValueError:
            pass
    if found:
        return found
    flat = page.replace('\\\\"', '"').replace('\\"', '"')
    for m in re.finditer(r'"jobPostingId"\s*:\s*"?(\d+)', flat):
        near = flat[max(0, m.start() - 1500):m.end() + 1500]
        t = re.search(r'"(?:jobTitle|postingTitle)"\s*:\s*"([^"]{3,200})"', near)
        if not t:
            continue
        p = {"jobPostingId": m.group(1), "jobTitle": t.group(1)}
        loc = re.search(r'"formattedAddress"\s*:\s*"([^"]{2,200})"', near)
        if loc:
            p["location"] = loc.group(1)
        d = re.search(r'"postingStartTimestampUTC"\s*:\s*"(\d{4}-\d{2}-\d{2})', near)
        if d:
            p["postingStartDate"] = d.group(1)
        found.append(p)
    return found


def fetch_dayforce(c, terms):
    """Dayforce candidate portal (jobs.dayforcehcm.com/<lang>/<id>/<board>), e.g. Cosm.

    Tries the portal's JSON search API, then postings embedded in the board page.
    All postings are returned; the usual filters decide what matches.
    """
    ns, board = c["id"], c.get("board", "CANDIDATEPORTAL")
    page_url = f"{DAYFORCE}/en-US/{ns}/{board}"
    hdr = {"Accept": "application/json, text/plain, */*", "Origin": DAYFORCE, "Referer": page_url}
    errors = []
    for label, url in (("api/geo search", f"{DAYFORCE}/api/geo/{ns}/jobposting/search"),
                       ("api search", f"{DAYFORCE}/api/jobposting/search")):
        out, seen, start = [], set(), 0
        try:
            while start < 1000:
                body = {"clientNamespace": ns, "jobBoardCode": board, "cultureCode": "en-US",
                        "distanceUnit": 0, "paginationStart": start}
                d = json.loads(http_raw(url, body, hdr)[2])
                posts = []
                _df_walk(d, posts)
                fresh = [p for p in posts if str(next(p[k] for k in _DF_ID_KEYS if p.get(k) not in (None, ""))) not in seen]
                for p in fresh:
                    j = _df_job(c, p, ns, board)
                    seen.add(j["id"])
                    out.append(j)
                total = d.get("maxCount") or d.get("totalCount") or d.get("count") if isinstance(d, dict) else None
                if not fresh or (isinstance(total, int) and len(out) >= total):
                    break
                start += len(fresh)
                pause()
        except Exception as e:
            errors.append(f"{label}: {str(e).split(' for ')[0][:80]}")
            continue
        if out:
            return out
        errors.append(f"{label}: 0 postings")
    try:
        page = http_text(page_url)
        posts = _df_from_html(page)
        if posts:
            uniq = {}
            for p in posts:
                j = _df_job(c, p, ns, board)
                uniq.setdefault(j["id"], j)
            return list(uniq.values())
        errors.append(f"board page: no postings in {len(page)} bytes")
    except Exception as e:
        errors.append(f"board page: {str(e).split(' for ')[0][:80]}")
    raise RuntimeError("dayforce " + " | ".join(errors))


def fetch_eightfold_pcsx(c, terms):
    """Eightfold's newer career-site search (apply.careers.microsoft.com/api/pcsx/search)."""
    out, seen = [], set()
    loc = urllib.parse.quote(c.get("location", "United States"))
    host = c["host"]
    # Newest first, so the first pages hold the roles that matter; deep paging (start=140+) is what
    # tripped Microsoft's rate limit (HTTP 429) on Nov 16-18. A 429 after some pages keeps what was read.
    max_start = int(c.get("max_start", 100))
    for term in c.get("terms") or terms:
        start = 0
        while start < max_start:
            try:
                d = json.loads(http_text(f"https://{host}/api/pcsx/search?domain={c['domain']}&query={urllib.parse.quote(term)}"
                                         f"&location={loc}&start={start}&sort_by=timestamp",
                                         headers={"Accept": "application/json"}))
            except RuntimeError as e:
                if "HTTP 429" in str(e) and out:
                    break
                raise
            data = d.get("data") or {}
            pos = data.get("positions") or data.get("jobs") or []
            for p in pos:
                if p.get("id") in seen:
                    continue
                seen.add(p.get("id"))
                locs = p.get("locations") or p.get("standardizedLocations") or []
                locs = "; ".join(locs) if isinstance(locs, list) else str(locs)
                if str(p.get("workLocationOption") or "").lower() == "remote":
                    locs += "; Remote"
                url = p.get("positionUrl") or f"/careers/job/{p.get('id')}"
                out.append(job(c, "eightfold", p.get("id"), p.get("name"), locs,
                               url if url.startswith("http") else f"https://{host}{url}",
                               iso_from_s(p.get("postedTs") or p.get("creationTs"))))
            pause(SLOW_SITE_PAUSE)
            if not pos or start + len(pos) >= int(data.get("count") or 0):
                break
            start += len(pos)
    return out


def fetch_oracle(c, terms):
    """Oracle Recruiting Cloud career sites (…oraclecloud.com/hcmUI/CandidateExperience/en/sites/<site>), e.g. Uber.

    The REST finder needs the site number (CX_…); it is read from the site page, then the site code is tried."""
    host, site = c["host"], c["site"]
    site_url = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/jobs"
    numbers, errors = [], []
    if c.get("site_number"):
        numbers.append(c["site_number"])
    try:
        page = http_text(site_url)
        numbers += re.findall(r"siteNumber[\"']?\s*[:=]\s*[\"'](CX_?\w*)[\"']", page)
        numbers += re.findall(r"\b(CX_\d+)\b", page)
    except Exception as e:
        errors.append(f"site page: {str(e).split(' for ')[0][:60]}")
    numbers += [site, "CX_1", "CX_1001", "CX"]
    api = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    for num in dict.fromkeys(numbers):
        out, seen = [], set()
        try:
            for term in terms:
                offset = 0
                while offset < 200:
                    finder = (f"findReqs;siteNumber={num},facetsList=LOCATIONS,limit=25,"
                              f"keyword=\"{term}\",sortBy=POSTING_DATES_DESC,offset={offset}")
                    d = json.loads(http_text(f"{api}?onlyData=true&expand=requisitionList.secondaryLocations"
                                             f"&finder={urllib.parse.quote(finder, safe='=;,')}",
                                             headers={"Accept": "application/json"}))
                    items = d.get("items") or [{}]
                    reqs = items[0].get("requisitionList") or []
                    for r in reqs:
                        if r.get("Id") in seen:
                            continue
                        seen.add(r.get("Id"))
                        locs = [r.get("PrimaryLocation") or ""] + [x.get("Name", "") for x in r.get("secondaryLocations") or []]
                        if str(r.get("WorkplaceType") or r.get("WorkplaceTypeCode") or "").lower().find("remote") >= 0:
                            locs.append("Remote")
                        text = strip_html(r.get("ShortDescriptionStr") or "")
                        out.append(job(c, "oracle", r.get("Id"), r.get("Title"), "; ".join(dict.fromkeys(x for x in locs if x)),
                                       f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{r.get('Id')}",
                                       iso_date(r.get("PostedDate")), parse_pay(text), text))
                    pause()
                    total = int(items[0].get("TotalJobsCount") or 0)
                    if not reqs or offset + len(reqs) >= total:
                        break
                    offset += len(reqs)
        except Exception as e:
            errors.append(f"{num}: {str(e).split(' for ')[0][:50]}")
            continue
        if out:
            return out
        errors.append(f"{num}: 0 jobs")
    raise RuntimeError("oracle " + " | ".join(errors))


def fetch_phenom(c, terms):
    """Phenom career sites (e.g. join.disneycareers.com): the site's /widgets search, then the
    search-results page's embedded data (phApp.ddo)."""
    host, path = c["host"], c.get("path", "global/en").strip("/")
    lang, country = c.get("lang", "en_global"), c.get("country", "global")
    out, seen, errors = [], set(), []

    def add(p):
        jid = str(p.get("jobId") or p.get("reqId") or p.get("jobSeqNo") or "")
        if not jid or jid in seen or not p.get("title"):
            return
        seen.add(jid)
        locs = p.get("multi_location") or p.get("multiLocation") or []
        loc = "; ".join(locs) if isinstance(locs, list) and locs else (p.get("location") or p.get("cityStateCountry") or "")
        if str(p.get("isRemote") or p.get("remote") or "").lower() in ("true", "yes", "remote"):
            loc += "; Remote"
        text = strip_html(p.get("descriptionTeaser") or p.get("description") or "")
        out.append(job(c, "phenom", jid, p.get("title"), loc,
                       f"https://{host}/{path}/job/{jid}/{_slug_words(p.get('title'))}",
                       iso_date(p.get("postedDate") or p.get("dateCreated") or ""), parse_pay(text), text))

    def jobs_in(d):
        d = d.get("refineSearch") or d.get("eagerLoadRefineSearch") or d
        data = d.get("data") or {}
        return data.get("jobs") or [], int(d.get("totalHits") or data.get("totalHits") or 0)

    for term in terms:
        start = 0
        try:
            while start < 300:
                body = {"lang": lang, "deviceType": "desktop", "country": country, "pageName": "search-results",
                        "ddoKey": "refineSearch", "sortBy": "Most recent", "subsearch": "", "from": start, "jobs": True,
                        "counts": True, "all_fields": ["category", "country", "state", "city", "type"], "size": 50,
                        "clearAll": False, "jdsource": "facets", "isSliderEnable": False, "pageId": "page20",
                        "siteType": "external", "keywords": term, "global": True, "selected_fields": {},
                        "locationData": {}}
                d = json.loads(http_text(f"https://{host}/widgets", body,
                                         {"Accept": "application/json", "Origin": f"https://{host}",
                                          "Referer": f"https://{host}/{path}/search-results?keywords={urllib.parse.quote(term)}"}))
                posts, total = jobs_in(d)
                for p in posts:
                    add(p)
                pause()
                if not posts or start + len(posts) >= total:
                    break
                start += len(posts)
        except Exception as e:
            errors.append(f"widgets: {str(e).split(' for ')[0][:60]}")
        if out:
            continue
        try:
            for start in range(0, 100, 10):
                page = http_text(f"https://{host}/{path}/search-results?keywords={urllib.parse.quote(term)}&from={start}&s=1")
                m = re.search(r"phApp\.ddo\s*=\s*(\{.*?\});\s*(?:phApp|</script>)", page, re.S)
                if not m:
                    errors.append("search page: no phApp.ddo")
                    break
                posts, total = jobs_in(json.loads(m.group(1)))
                for p in posts:
                    add(p)
                pause()
                if not posts or start + len(posts) >= total:
                    break
        except Exception as e:
            errors.append(f"search page: {str(e).split(' for ')[0][:60]}")
    if not out and errors:
        raise RuntimeError("phenom " + " | ".join(dict.fromkeys(errors)))
    return out


# ------------------------------------------- venture portfolio job boards (added 2026-11-17)

def _getro_walk(obj, found):
    """Collect dicts that look like Getro job postings: a title plus an organization (or company) and a link."""
    if isinstance(obj, dict):
        org = obj.get("organization") or obj.get("company")
        if isinstance(obj.get("title"), str) and obj["title"].strip() and org and \
                (obj.get("url") or obj.get("slug") or obj.get("id")):
            found.append(obj)
            return
        for v in obj.values():
            _getro_walk(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _getro_walk(v, found)


def _getro_loc(p):
    locs = p.get("locations") or p.get("searchable_locations") or p.get("location") or []
    if isinstance(locs, str):
        locs = [locs]
    out = [x if isinstance(x, str) else (x.get("name") or x.get("display_name") or "") for x in locs]
    if p.get("work_mode") == "remote" or p.get("remote") is True:
        out.append("Remote")
    return "; ".join(x for x in out if x)


def _getro_pay(p):
    lo, hi = p.get("compensation_amount_min_cents"), p.get("compensation_amount_max_cents")
    period = str(p.get("compensation_period") or "year").lower()
    if lo and hi and period.startswith("year"):
        return (int(lo) // 100, int(hi) // 100)
    return parse_pay(str(p.get("compensation") or ""))


def fetch_getro(c, terms):
    """Getro portfolio job boards run by venture firms (jobs.a16z.com, jobs.upfront.com).

    Reads the board's search API (collection id from the board page), else the page's embedded
    Next.js data. Roles belong to many portfolio companies, so the company name goes in the title
    ("Title (Company)") and the board stays the watchlist company, which keeps closing logic working."""
    base = c["board"].rstrip("/")  # e.g. https://jobs.a16z.com
    out, seen, errors = [], set(), []

    def add(p):
        jid = str(p.get("id") or p.get("slug") or p.get("url") or "")
        if not jid or jid in seen:
            return
        seen.add(jid)
        org = p.get("organization") or p.get("company") or {}
        org_name = org.get("name", "") if isinstance(org, dict) else str(org)
        org_slug = org.get("slug", "") if isinstance(org, dict) else _slug_words(org_name)
        url = p.get("url") or ""
        if not url.startswith("http"):
            url = f"{base}/companies/{org_slug}/jobs/{p.get('slug') or jid}" if org_slug else base
        posted = p.get("created_at") or p.get("posted_at") or ""
        if isinstance(posted, (int, float)):
            posted = iso_from_s(posted)
        title = p["title"].strip() + (f" ({org_name})" if org_name else "")
        out.append(job(c, "getro", jid, title, _getro_loc(p), url, iso_date(str(posted)), _getro_pay(p)))

    collection = c.get("collection_id")
    for term in terms:
        q = urllib.parse.quote(term)
        page = ""
        if not collection or not out:
            try:
                page = http_text(f"{base}/jobs?q={q}")
                collection = collection or (re.search(r'"collection(?:Id|_id)"\s*:\s*"?(\d+)', page) or
                                            re.search(r'"network"\s*:\s*\{\s*"id"\s*:\s*"?(\d+)', page) or [None, None])[1]
            except Exception as e:
                errors.append(f"board page: {str(e).split(' for ')[0][:60]}")
        got = 0
        if collection:
            try:
                for pg in range(0, 5):
                    d = json.loads(http_text(f"https://api.getro.com/api/v2/collections/{collection}/search/jobs",
                                             {"hitsPerPage": 50, "page": pg, "filters": {}, "query": term},
                                             {"Accept": "application/json", "Origin": base, "Referer": base + "/jobs"}))
                    res = d.get("results") or d
                    found = []
                    _getro_walk(res.get("jobs") if isinstance(res, dict) and "jobs" in res else res, found)
                    for p in found:
                        add(p)
                    got += len(found)
                    pause()
                    if len(found) < 50:
                        break
            except Exception as e:
                errors.append(f"api: {str(e).split(' for ')[0][:60]}")
        if not got and page:
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
            if m:
                found = []
                _getro_walk(json.loads(m.group(1)), found)
                for p in found:
                    add(p)
            else:
                errors.append("board page: no __NEXT_DATA__")
        pause()
    if not out and errors:
        raise RuntimeError("getro " + " | ".join(dict.fromkeys(errors)))
    return out


def fetch_consider(c, terms):
    """Consider portfolio job boards run by venture firms (jobs.a16z.com: job links look like
    /jobs/<company>/<id>--<title>). Reads the board's search API, newest first, a page at a time;
    the company name goes in the title ("Title (Company)") as with the Getro reader.
    The board id comes from the watchlist ("board_id") or the board page."""
    base = c["board"].rstrip("/")
    board_id = c.get("board_id")
    if not board_id:
        page = http_text(base + "/jobs")
        board_id = (re.search(r'"board"\s*:\s*\{\s*"id"\s*:\s*"([^"]+)"', page) or
                    re.search(r'"boardId"\s*:\s*"([^"]+)"', page) or [None, None])[1]
        if not board_id:
            raise RuntimeError("consider board page: no board id")
    out, seen, sequence = [], set(), None
    for _ in range(int(c.get("max_pages", 15))):
        meta = {"size": 100}
        if sequence:
            meta["sequence"] = sequence
        d = json.loads(http_text(base + "/api-boards/search-jobs",
                                 {"meta": meta, "board": {"id": board_id, "isParent": True},
                                  "query": {"promoteFeatured": False}, "grouped": False},
                                 {"Accept": "application/json", "Origin": base, "Referer": base + "/jobs"}))
        jobs = d.get("jobs") or []
        for p in jobs:
            jid = str(p.get("jobId") or p.get("id") or p.get("url") or "")
            if not jid or jid in seen or not p.get("title"):
                continue
            seen.add(jid)
            comp = p.get("company")
            org = p.get("companyName") or (comp.get("name", "") if isinstance(comp, dict) else str(comp or ""))
            org_slug = p.get("companySlug") or _slug_words(org)
            locs = p.get("locations") or p.get("normalizedLocations") or []
            locs = [x if isinstance(x, str) else (x.get("label") or x.get("value") or "") for x in locs] if isinstance(locs, list) else [str(locs)]
            if p.get("remote") is True or str(p.get("workplaceType") or "").lower() == "remote":
                locs.append("Remote")
            url = p.get("url") if str(p.get("url") or "").startswith(base) else \
                f"{base}/jobs/{org_slug}/{jid}--{_slug_words(p['title'])}"
            sal = p.get("salary") or {}
            pay = None
            if isinstance(sal, dict) and sal.get("maxValue") and "year" in json.dumps(sal.get("period") or "year").lower():
                pay = (int(sal.get("minValue") or 0), int(sal["maxValue"]))
            ts = p.get("timeStamp") or p.get("postedAt") or ""
            posted = iso_from_s(ts) if isinstance(ts, (int, float)) else iso_date(str(ts))
            out.append(job(c, "consider", jid, p["title"].strip() + (f" ({org})" if org else ""),
                           "; ".join(x for x in locs if x), url, posted, pay))
        sequence = (d.get("meta") or {}).get("sequence")
        pause()
        if len(jobs) < 100 or not sequence:
            break
    return out


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "consider": fetch_consider,
    "workday": fetch_workday,
    "smartrecruiters": fetch_smartrecruiters,
    "netflix": fetch_netflix,
    "eightfold": fetch_eightfold,
    "microsoft_gcs": fetch_microsoft_gcs,
    "amazon": fetch_amazon,
    "apple": fetch_apple,
    "uber": fetch_uber,
    "atlassian": fetch_atlassian,
    "google": fetch_google,
    "meta": fetch_meta,
    "successfactors": fetch_successfactors,
    "avature": fetch_avature,
    "foxcareers": fetch_foxcareers,
    "clinch": fetch_clinch,
    "greenhouse_html": fetch_greenhouse_html,
    "paylocity": fetch_paylocity,
    "dayforce": fetch_dayforce,
    "eightfold_pcsx": fetch_eightfold_pcsx,
    "oracle": fetch_oracle,
    "phenom": fetch_phenom,
    "getro": fetch_getro,
}


# ---------------------------------------------------------------- filters

class Filters:
    def __init__(self, f):
        flag = re.I
        self.inc = re.compile(f["title_include"], flag)
        self.lvl = re.compile(f["level_include"], flag)
        self.exc = re.compile(f["title_exclude"], flag)
        self.la = re.compile(f["la_terms"], flag)
        self.remote = re.compile(f["remote_terms"], flag)
        self.bare_us = re.compile(r"^\s*(us|u\.s\.|usa|united states( of america)?)\s*$", flag)
        self.non_us = re.compile(f["non_us_terms"], flag)
        self.mgr_ok = {x.lower() for x in f.get("manager_ok_companies", [])}
        # Companies whose titles carry no level ("Strategic Business Performance, Central Strategy & Operations"
        # at Stripe): the title and pay filters still apply, the level word is not required.
        self.no_level = {x.lower() for x in f.get("level_optional_companies", [])}
        self.min_top = f.get("min_pay_top", 0)
        self.target_top = f.get("target_pay_top", 0)

    def title_ok(self, j):
        t = j["title"]
        if not self.inc.search(t) or self.exc.search(t):
            return False
        if self.lvl.search(t) or j["company"].lower() in self.no_level:
            return True
        return j["company"].lower() in self.mgr_ok and re.search(r"\bmanager\b", t, re.I) is not None

    def location_ok(self, j):
        loc = j["location"]
        if not loc:
            return True  # unknown: keep, the reviewer decides
        parts = [p.strip() for p in re.split(r";|\||\bor\b|\n", loc) if p.strip()]
        for p in parts:
            if self.non_us.search(p) and not self.la.search(p):
                continue
            if self.la.search(p):
                return True
            if self.remote.search(p):
                return True  # remote / distributed, not tied to a non-US country
            if self.bare_us.match(p):
                return True  # "United States" with no city usually means remote or flexible
        return False

    def pay_ok(self, j):
        return j["pay_top"] is None or j["pay_top"] >= self.min_top

    def tag(self, j):
        if j["pay_top"] is None:
            return "pay not posted"
        return "pay meets target" if j["pay_top"] >= self.target_top else "pay below target"


# ---------------------------------------------------------------- run

def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False, sort_keys=True)


def notify(new):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic or not new:
        return
    lines = [f"{j['company']}: {j['title']} ({j['pay'] or 'pay n/a'})" for j in new[:15]]
    if len(new) > 15:
        lines.append(f"...and {len(new) - 15} more")
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}", data="\n".join(lines).encode(),
        headers={"Title": f"{len(new)} new role{'s' if len(new) != 1 else ''}",
                 "Click": new[0]["url"], "Tags": "briefcase"})
    try:
        urllib.request.urlopen(req, timeout=20)
    except Exception as e:
        print("ntfy failed:", e, file=sys.stderr)


CLOSE_AFTER_MISSES = 2   # full reads in a row a role must be missing from before it counts as closed


def partial_read(now, before):
    """True when this run saw far fewer postings than the last good run (likely a partial or throttled read)."""
    if not before or not before.get("ok") or not now.get("ok"):
        return False
    prev_n, n = before.get("jobs_seen") or 0, now.get("jobs_seen") or 0
    return prev_n >= 20 and n < 0.6 * prev_n


def migrate_keys(state, matches):
    """Carry a role's history over when only its key changed (e.g. a reader now strips tracking
    parameters from URLs), so it is not logged as closed under the old key and new under the new one."""
    by_url = {}
    for key, prev in state.items():
        by_url.setdefault((prev.get("company"), canonical_url(prev.get("url", ""))), []).append(key)
    for key, j in matches.items():
        if key in state:
            continue
        for old in by_url.get((j["company"], canonical_url(j.get("url", ""))), []):
            if old != key and old in state:
                state[key] = state.pop(old)
                state[key]["key"] = key
                break


def run(watchlist_path=os.path.join(ROOT, "watchlist.json"), only=None):
    wl = load(watchlist_path, {})
    flt = Filters(wl["filters"])
    terms = wl.get("search_terms", ["strategy"])
    state = load(os.path.join(DATA, "state.json"), {})
    first_run = not state
    status, matches, raw_keys = {}, {}, set()

    for c in wl["companies"]:
        if only and c["name"] not in only:
            continue
        started = time.time()  # per-company timing, saved in company_status.json ("seconds")
        tries = [c] + [dict(c, **alt) for alt in c.get("try", [])]
        jobs, used, errors = None, None, []
        for t in tries:
            fetch = FETCHERS.get(t["ats"])
            if not fetch:
                continue
            try:
                got = fetch(t, terms)
            except Exception as e:
                errors.append(str(e)[:200])
                continue
            if got or t is tries[-1]:
                jobs, used = got, t
                break
            errors.append(f"{t['ats']}:{t.get('id') or t.get('site')} returned 0 jobs")
        try:
            if jobs is None:
                raise RuntimeError(" | ".join(errors) or "no fetcher")
            kept = []
            raw_keys.update(j["key"] for j in jobs)
            pinned = {str(x) for x in c.get("pinned", [])}  # posting IDs to follow whatever the filters say
            for j in jobs:
                if j["id"] in pinned:
                    kept.append(j)
                    continue
                if not flt.title_ok(j):
                    continue
                if j["ats"] == "workday":
                    workday_detail(j)
                    if j.get("_closed"):
                        raw_keys.discard(j["key"])
                        continue
                if flt.location_ok(j) and flt.pay_ok(j):
                    kept.append(j)
            for j in kept:
                j["pay_tag"] = flt.tag(j)
            if not c.get("trial"):
                for j in kept:
                    matches[j["key"]] = {k: v for k, v in j.items() if not k.startswith("_")}
            board = used.get("id") or used.get("host") or used.get("base") or ""
            if used.get("tenant"):
                board = used["tenant"] + "/" + used.get("site", "")
            status[c["name"]] = {"ok": True, "jobs_seen": len(jobs), "matches": len(kept),
                                 "board": f"{used['ats']}:{board}"}
            if c.get("trial"):
                # New reader on probation: report what it found, but don't log roles yet.
                status[c["name"]]["trial"] = True
                status[c["name"]]["errors_before_success"] = errors
                status[c["name"]]["sample_raw"] = [
                    {"title": j["title"], "location": j["location"], "url": j["url"], "posted": j["posted"]}
                    for j in jobs[:6]]
                status[c["name"]]["sample_matches"] = [
                    {"title": j["title"], "location": j["location"], "url": j["url"],
                     "posted": j["posted"], "pay": j["pay"]} for j in kept[:10]]
        except Exception as e:
            status[c["name"]] = {"ok": False, "error": str(e)[:600]}
            if c.get("trial"):
                status[c["name"]]["trial"] = True
        status[c["name"]]["seconds"] = round(time.time() - started, 1)
        pause()

    ts = NOW.strftime("%Y-%m-%dT%H:%MZ")
    new, closed, events = [], [], []
    migrate_keys(state, matches)
    for key, j in matches.items():
        prev = state.get(key)
        if prev is None or prev.get("status") in ("closed", "filtered"):
            j["first_seen"] = ts
            new.append(j)
            events.append({"ts": ts, "event": "reopened" if prev and prev.get("status") == "closed" else "new", **j})
        else:
            j["first_seen"] = prev.get("first_seen", ts)
            if not j.get("posted"):
                j["posted"] = prev.get("posted", "")
        j["last_seen"], j["status"] = ts, "open"
        state[key] = j
    # A role closes only after it is missing from CLOSE_AFTER_MISSES full reads in a row. Search-based
    # readers (Paramount's RSS feed, Netflix, Apple…) sometimes return a partial list, which used to
    # close roles that were still open (a Paramount posting, 2026-11-17).
    close_after = int(wl.get("close_after_misses", CLOSE_AFTER_MISSES))
    prev_status = load(os.path.join(DATA, "company_status.json"), {}).get("companies", {})
    ok_companies = {n for n, st in status.items() if st.get("ok") and not st.get("trial")
                    and not partial_read(st, prev_status.get(n))}
    watched = {c["name"] for c in wl["companies"]}
    for key, prev in state.items():
        if prev.get("status") != "open" or key in matches:
            continue
        if prev["company"] not in watched and not only:
            prev["status"] = "untracked"  # company removed from the watchlist: stop reporting its roles
            continue
        if prev["company"] not in ok_companies:
            continue
        if key in raw_keys:
            prev["status"] = "filtered"  # still posted, but no longer matches the filters
            prev.pop("misses", None)
            continue
        prev["misses"] = prev.get("misses", 0) + 1
        if prev["misses"] < close_after:
            continue
        prev.pop("misses", None)
        prev["status"], prev["closed_seen"] = "closed", ts
        closed.append(prev)
        events.append({"ts": ts, "event": "closed", **prev})

    open_now = sorted((v for v in state.values() if v["status"] == "open"),
                      key=lambda v: (v.get("posted") or v.get("first_seen", "")), reverse=True)
    save(os.path.join(DATA, "state.json"), state)
    save(os.path.join(DATA, "open.json"), open_now)
    save(os.path.join(DATA, "company_status.json"), {"run": ts, "companies": status})
    if events:
        with open(os.path.join(DATA, "events.jsonl"), "a") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
    write_report(ts, new, closed, open_now, status, wl.get("not_covered_yet", []), first_run)
    if not first_run:
        notify(new)
    print(f"{ts}: {len(new)} new, {len(closed)} closed, {len(open_now)} open; "
          f"{sum(1 for s in status.values() if not s['ok'])} companies failed")
    return new, closed, status


def row(j):
    posted = j.get("posted") or "?"
    return (f"| {posted} | {j['company']} | [{j['title']}]({j['url']}) | {j['location'][:60]} | "
            f"{j['pay'] or '—'} |")


def write_report(ts, new, closed, open_now, status, uncovered, first_run):
    os.makedirs(REPORTS, exist_ok=True)
    head = "| Posted | Company | Role | Location | Base |\n|---|---|---|---|---|"
    lines = [f"# Job check {ts}", ""]
    if first_run:
        lines += ["First run: every match is listed as new and no alert was sent.", ""]
    lines += [f"## New since last run ({len(new)})", "", head] + [row(j) for j in sorted(new, key=lambda j: j.get('posted') or '', reverse=True)] + [""]
    lines += [f"## Closed since last run ({len(closed)})", "", head] + [row(j) for j in closed] + [""]
    failed = {n: s for n, s in status.items() if not s.get("ok") and not s.get("trial")}
    lines += [f"## Companies that failed ({len(failed)})", ""]
    lines += [f"- {n}: {s['error']}" for n, s in sorted(failed.items())] or ["- none"]
    trial = {n: s for n, s in status.items() if s.get("trial")}
    if trial:
        lines += ["", f"## Trial readers ({len(trial)}; roles not logged until confirmed)", ""]
        for n, s in sorted(trial.items()):
            if s.get("ok"):
                ex = "; ".join(f"{m['title']} ({m['location'][:40]})" for m in s.get("sample_matches", [])[:3])
                lines.append(f"- {n}: OK via {s['board']}, {s['jobs_seen']} postings read, {s['matches']} match"
                             + (f". e.g. {ex}" if ex else ""))
            else:
                lines.append(f"- {n}: FAILED: {s['error'][:300]}")
    lines += ["", f"## All open matches ({len(open_now)})", "", head] + [row(j) for j in open_now]
    if uncovered:
        lines += ["", "Not covered by this script: " + ", ".join(uncovered)]
    with open(os.path.join(REPORTS, "latest.md"), "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    only = set(sys.argv[1:]) or None
    run(only=only)
