#!/usr/bin/env python3
"""Check target companies' own job boards for matching roles.

Reads watchlist.json, queries each company's public job-board API
(Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Netflix), keeps roles
that match the title, level, location and pay filters, and compares them
with the previous run.

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
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        loc = "; ".join(dict.fromkeys(l for l in locs if l))
        if j.get("isRemote") and "remote" not in loc.lower():
            loc = (loc + "; Remote").strip("; ")
        pay = None
        comp = j.get("compensation") or {}
        for comp_part in comp.get("summaryComponents") or []:
            if comp_part.get("compensationType") == "Salary" and comp_part.get("maxValue"):
                pay = (int(comp_part.get("minValue") or 0), int(comp_part["maxValue"]))
        text = j.get("descriptionPlain") or ""
        pay = pay or parse_pay(comp.get("compensationTierSummary", "") + " " + text)
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


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workday": fetch_workday,
    "smartrecruiters": fetch_smartrecruiters,
    "netflix": fetch_netflix,
}


# ---------------------------------------------------------------- filters

class Filters:
    def __init__(self, f):
        flag = re.I
        self.inc = re.compile(f["title_include"], flag)
        self.lvl = re.compile(f["level_include"], flag)
        self.exc = re.compile(f["title_exclude"], flag)
        self.la = re.compile(f["la_terms"], flag)
        self.us = re.compile(f["us_remote_terms"], flag)
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
        parts = [p.strip() for p in re.split(r";|\|| or |\n", loc) if p.strip()]
        for p in parts:
            if self.la.search(p):
                return True
            if self.us.search(p) and not self.non_us.search(p):
                return True
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
    status, matches = {}, {}

    for c in wl["companies"]:
        if only and c["name"] not in only:
            continue
        fetch = FETCHERS.get(c["ats"])
        if not fetch:
            continue
        try:
            jobs = fetch(c, terms)
            kept = []
            for j in jobs:
                if not flt.title_ok(j):
                    continue
                if j["ats"] == "workday":
                    workday_detail(j)
                    if j.get("_closed"):
                        continue
                if flt.location_ok(j) and flt.pay_ok(j):
                    kept.append(j)
            for j in kept:
                j["pay_tag"] = flt.tag(j)
                matches[j["key"]] = {k: v for k, v in j.items() if not k.startswith("_")}
            status[c["name"]] = {"ok": True, "jobs_seen": len(jobs), "matches": len(kept)}
        except Exception as e:
            status[c["name"]] = {"ok": False, "error": str(e)[:300]}
        pause()

    ts = NOW.strftime("%Y-%m-%dT%H:%MZ")
    new, closed, events = [], [], []
    for key, j in matches.items():
        prev = state.get(key)
        if prev is None or prev.get("status") == "closed":
            j["first_seen"] = ts
            new.append(j)
            events.append({"ts": ts, "event": "reopened" if prev else "new", **j})
        else:
            j["first_seen"] = prev.get("first_seen", ts)
            if not j.get("posted"):
                j["posted"] = prev.get("posted", "")
        j["last_seen"], j["status"] = ts, "open"
        state[key] = j
    ok_companies = {n for n, s in status.items() if s.get("ok")}
    for key, prev in state.items():
        if prev.get("status") == "open" and key not in matches and prev["company"] in ok_companies:
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
    failed = {n: s for n, s in status.items() if not s.get("ok")}
    lines += [f"## Companies that failed ({len(failed)})", ""]
    lines += [f"- {n}: {s['error']}" for n, s in sorted(failed.items())] or ["- none"]
    lines += ["", f"## All open matches ({len(open_now)})", "", head] + [row(j) for j in open_now]
    lines += ["", "Not covered by this script (checked in the weekly search sweep): " + ", ".join(uncovered)]
    with open(os.path.join(REPORTS, "latest.md"), "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    only = set(sys.argv[1:]) or None
    run(only=only)
