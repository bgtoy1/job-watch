#!/usr/bin/env python3
"""Check target companies' own job boards for matching roles.

Reads watchlist.json, queries each company's public job-board API
(Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Netflix) or, for companies
with their own career sites, a site-specific reader (Amazon, Microsoft, Apple,
Uber, Atlassian, Google, Meta, SuccessFactors, Avature, Fox, Clinch, Paylocity),
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

def http(url, body=None, timeout=30, retries=2):
    """GET (or POST JSON when body is given) and return parsed JSON."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (400, 401, 403, 404, 410, 422):
                break
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
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (400, 401, 403, 404, 405, 410, 422):
                break
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{last} for {url}")


def http_text(url, body=None, headers=None, form=False):
    return http_raw(url, body, headers, form=form)[2]


def pause():
    time.sleep(0.6)


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


def fetch_ashby(c, terms):
    d = http(f"https://api.ashbyhq.com/posting-api/job-board/{c['id']}?includeCompensation=true")
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
            pause()
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
        if not pid or pid in seen:
            return
        seen.add(pid)
        locs = "; ".join(x.get("name", "") for x in p.get("locations") or [] if isinstance(x, dict))
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
                    token_hdr = None
            if not token_hdr:
                page_html = http_text("https://jobs.apple.com/en-us/search?"
                                      f"search={urllib.parse.quote(term)}&location=united-states-USA&sort=newest&page={page}")
                page_html = page_html.replace('\\"', '"')
                for m in re.finditer(r'"positionId"\s*:\s*"?(\d+)"?', page_html):
                    seg = page_html[max(0, m.start() - 1500):m.end() + 1500]
                    rel = seg[seg.find(m.group(0)):]
                    get = lambda k: (re.search(rf'"{k}"\s*:\s*"((?:[^"\\]|\\.)*)"', rel) or re.search(rf'"{k}"\s*:\s*"((?:[^"\\]|\\.)*)"', seg) or [None, ""])[1]
                    title = get("postingTitle")
                    if not title:
                        continue
                    locs_m = re.search(r'"locations"\s*:\s*\[(.*?)\]', rel, re.S)
                    names = re.findall(r'"name"\s*:\s*"([^"]+)"', locs_m.group(1)) if locs_m else []
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


def fetch_meta(c, terms):
    """metacareers.com search pages; reads job data embedded in the HTML."""
    out, seen = [], set()
    offices = "".join(f"&offices[{i}]={urllib.parse.quote(o)}" for i, o in enumerate(c.get("offices", ["Los Angeles, CA", "Burbank, CA", "Remote, US"])))
    for term in terms:
        page_html = http_text(f"https://www.metacareers.com/jobs?q={urllib.parse.quote(term)}{offices}")
        for m in re.finditer(r'"id"\s*:\s*"(\d{12,})"\s*,\s*"title"\s*:\s*"((?:[^"\\]|\\.)+)"\s*,\s*"locations"\s*:\s*\[((?:[^\]])*)\]', page_html):
            jid = m.group(1)
            if jid in seen:
                continue
            seen.add(jid)
            title = json.loads('"' + m.group(2) + '"')
            loc = "; ".join(re.findall(r'"((?:[^"\\]|\\.)+)"', m.group(3)))
            out.append(job(c, "meta", jid, title, loc, f"https://www.metacareers.com/jobs/{jid}/"))
        pause()
    return out


def fetch_successfactors(c, terms):
    """SAP SuccessFactors career sites (e.g. careers.paramount.com): RSS feed, then HTML."""
    out, seen = [], set()
    host = c["host"]
    for term in terms:
        q = urllib.parse.quote(term)
        try:
            xml = http_text(f"https://{host}/services/rss/job/?locale=en_US&keywords=({q})")
            items = re.findall(r"<item>(.*?)</item>", xml, re.S)
        except Exception:
            items = []
        for it in items:
            g = lambda tag: html.unescape(strip_html((re.search(rf"<{tag}>(.*?)</{tag}>", it, re.S) or [None, ""])[1].replace("<![CDATA[", "").replace("]]>", "")))
            link = g("link")
            jid = (re.search(r"/(\d{6,})/?$", link) or [None, link])[1]
            if not link or jid in seen:
                continue
            seen.add(jid)
            title = g("title")
            m = re.match(r"(.*?)\s*\(([^()]*)\)\s*$", title)
            title, loc = (m.group(1), m.group(2)) if m else (title, "")
            desc = g("description")
            out.append(job(c, "successfactors", jid, title, loc, link, _date_text(g("pubDate")), parse_pay(desc), desc))
        if items:
            pause()
            continue
        for start in (0, 25, 50, 75):
            page_html = http_text(f"https://{host}/search/?q={q}&startrow={start}")
            rows = re.findall(r'<tr class="data-row.*?</tr>', page_html, re.S)
            for r in rows:
                a = re.search(r'href="(/job/[^"]+?/(\d+)/?)"[^>]*class="jobTitle-link[^"]*"[^>]*>(.*?)</a>', r, re.S) \
                    or re.search(r'class="jobTitle-link[^"]*"[^>]*href="(/job/[^"]+?/(\d+)/?)"[^>]*>(.*?)</a>', r, re.S)
                if not a or a.group(2) in seen:
                    continue
                seen.add(a.group(2))
                loc = strip_html((re.search(r'class="jobLocation"[^>]*>(.*?)</span>', r, re.S) or [None, ""])[1])
                date = strip_html((re.search(r'class="jobDate"[^>]*>(.*?)</span>', r, re.S) or [None, ""])[1])
                out.append(job(c, "successfactors", a.group(2), strip_html(a.group(3)), loc,
                               f"https://{host}{a.group(1)}", _date_text(date)))
            pause()
            if len(rows) < 25:
                break
    return out


def fetch_avature(c, terms):
    """Avature career sites (e.g. jobs.ea.com): search results HTML."""
    out, seen = [], set()
    base = c["base"].rstrip("/")  # e.g. https://jobs.ea.com/en_US/careers
    for term in terms:
        for offset in (0, 50, 100):
            page_html = http_text(f"{base}/SearchJobs/?keyword={urllib.parse.quote(term)}"
                                  f"&jobRecordsPerPage=50&jobOffset={offset}&listFilterMode=1")
            found = 0
            for m in re.finditer(r'href="([^"]*/careers/JobDetail/([^"/]+)/(\d+))"[^>]*>(.*?)</a>', page_html, re.S):
                url, slug, jid, label = m.groups()
                title = strip_html(label) or slug.replace("-", " ")
                if jid in seen or not title:
                    continue
                seen.add(jid)
                found += 1
                after = page_html[m.end():m.end() + 2500]
                loc = strip_html((re.search(r'class="[^"]*location[^"]*"[^>]*>(.*?)</', after, re.S) or [None, ""])[1])
                if url.startswith("/"):
                    url = re.match(r"https://[^/]+", base).group(0) + url
                out.append(job(c, "avature", jid, title, loc, url))
            pause()
            if found < 50:
                break
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


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
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
        self.min_top = f.get("min_pay_top", 0)
        self.target_top = f.get("target_pay_top", 0)

    def title_ok(self, j):
        t = j["title"]
        if not self.inc.search(t) or self.exc.search(t):
            return False
        if self.lvl.search(t):
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
            for j in jobs:
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
        pause()

    ts = NOW.strftime("%Y-%m-%dT%H:%MZ")
    new, closed, events = [], [], []
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
    ok_companies = {n for n, s in status.items() if s.get("ok")}
    for key, prev in state.items():
        if prev.get("status") == "open" and key not in matches and prev["company"] in ok_companies:
            if key in raw_keys:
                prev["status"] = "filtered"  # still posted, but no longer matches the filters
                continue
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
