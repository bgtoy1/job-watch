#!/usr/bin/env python3
"""One-off discovery probe for companies the checker can't read yet.

Reads probe_targets.json and, for each target:
  - "pages": opens career pages, records status / final URL / size / title, finds
    links to known job systems (Greenhouse, Lever, Ashby, Workday, SmartRecruiters,
    Paylocity, Dayforce, iCIMS, ...) and tests each one's public API automatically;
  - "api": calls an endpoint and records status, content type, top-level keys and
    the first part of the response.
Also runs every "trial" company in watchlist.json through its reader and keeps
up to 5 sample postings.

Writes data/probe.json and reports/probe.md. Skips the work when
probe_targets.json hasn't changed since the last probe (same hash), so leaving it
in the repo costs nothing. Standard library only.
"""
import hashlib
import json
import os
import re
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_jobs as fj  # noqa: E402

ROOT = fj.ROOT
OUT_JSON = os.path.join(ROOT, "data", "probe.json")
OUT_MD = os.path.join(ROOT, "reports", "probe.md")

ATS_PATTERNS = {
    "greenhouse": r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)",
    "greenhouse_embed": r"greenhouse\.io/embed/job_board/js\?for=([A-Za-z0-9_-]+)",
    "lever": r"jobs\.lever\.co/([A-Za-z0-9_-]+)",
    "ashby": r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)",
    "workday": r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)",
    "smartrecruiters": r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)",
    "paylocity": r"recruiting\.paylocity\.com/recruiting/jobs/(?:All|List)/([0-9a-fA-F-]{36})",
    "dayforce": r"jobs\.dayforcehcm\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)",
    "icims": r"([a-z0-9-]+)\.icims\.com",
    "jobvite": r"jobs\.jobvite\.com/([A-Za-z0-9_-]+)",
    "workable": r"apply\.workable\.com/([A-Za-z0-9_-]+)",
    "rippling": r"ats\.rippling\.com/([A-Za-z0-9_-]+)",
    "bamboohr": r"([a-z0-9-]+)\.bamboohr\.com",
    "ultipro": r"recruiting2?\.ultipro\.com/([A-Za-z0-9]+)/JobBoard/([0-9a-f-]{36})",
    "oracle": r"([a-z0-9-]+\.fa\.[a-z0-9-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[a-z]+/sites/([A-Za-z0-9_-]+)",
    "successfactors": r"(career\d*\.successfactors\.com|[a-z0-9.-]*jobs2web[a-z0-9.-]*)",
    "avature": r"([a-z0-9-]+\.avature\.net)",
    "gr8people": r"([a-z0-9-]+\.gr8people\.com)",
    "eightfold": r"([a-z0-9.-]+)/careers\?domain=([a-z0-9.-]+)",
    "phenom": r"(cdn\.phenompeople\.com|phenom)",
    "radancy": r"(tbcdn\.talentbrew\.com|radancy)",
    "clinch": r"(clinchtalent\.com)",
}
SKIP_IDS = {"embed", "jobs", "api", "v1", "boards", "job_board", "js", "en-US", "en-us"}


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def body_text(page):
    page = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S | re.I)
    return fj.strip_html(page)


def find_ats(page):
    found = {}
    for name, pat in ATS_PATTERNS.items():
        hits = []
        for m in re.finditer(pat, page):
            g = m.groups()
            if g and g[0] in SKIP_IDS:
                continue
            hits.append("/".join(x for x in g if x))
        if hits:
            found[name] = sorted(set(hits))[:8]
    return found


def test_board(kind, ident):
    """Try the standard API for a discovered board; return a short result."""
    c = {"name": "probe", "sector": ""}
    try:
        if kind in ("greenhouse", "greenhouse_embed"):
            jobs = fj.fetch_greenhouse(dict(c, id=ident), [])
        elif kind == "lever":
            jobs = fj.fetch_lever(dict(c, id=ident), [])
        elif kind == "ashby":
            jobs = fj.fetch_ashby(dict(c, id=ident), [])
        elif kind == "smartrecruiters":
            jobs = fj.fetch_smartrecruiters(dict(c, id=ident), ["strategy"])
        elif kind == "workday":
            tenant, wd, site = ident.split("/")
            jobs = fj.fetch_workday(dict(c, host=f"{tenant}.{wd}.myworkdayjobs.com", tenant=tenant, site=site), ["strategy"])
        elif kind == "paylocity":
            jobs = fj.fetch_paylocity(dict(c, id=ident), [])
        else:
            return None
        return {"ok": True, "jobs": len(jobs), "sample": [f"{j['title']} | {j['location']}" for j in jobs[:4]]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def probe_page(url):
    r = {"url": url}
    try:
        status, headers, page = fj.http_raw(url)
        r.update(status=status, bytes=len(page),
                 content_type={k.lower(): v for k, v in headers.items()}.get("content-type", ""),
                 title=fj.strip_html((re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I) or [None, ""])[1])[:120])
        r["ats"] = find_ats(page)
        tested = {}
        for kind, ids in r["ats"].items():
            for ident in ids[:3]:
                res = test_board(kind, ident)
                if res is not None:
                    tested[f"{kind}:{ident}"] = res
        if tested:
            r["board_tests"] = tested
        # first job-like links and a snippet around the first, to finish HTML readers
        links = re.findall(r'href="([^"]*(?:job|position|career|opening|requisition)[^"]*)"', page, re.I)
        r["job_links"] = list(dict.fromkeys(links))[:15]
        m = re.search(r'href="[^"]*(?:JobDetail|/job/|/jobs/|/details/|/position)[^"]*"', page, re.I)
        if m:
            r["html_snippet"] = page[max(0, m.start() - 1200):m.start() + 1500]
        r["text_start"] = body_text(page)[:700]
        r["json_hints"] = sorted(set(re.findall(r'"(positionId|postingTitle|jobPosts|jobId|requisitionId|job_id|externalPath|jobTitle|title)"\s*:', page)))[:10]
    except Exception as e:
        r["error"] = str(e)[:300]
    return r


def probe_api(t):
    r = {"name": t.get("name"), "url": t["url"]}
    try:
        status, headers, text = fj.http_raw(t["url"], t.get("body"), t.get("headers"), form=t.get("form", False))
        h = {k.lower(): v for k, v in headers.items()}
        r.update(status=status, content_type=h.get("content-type", ""), bytes=len(text))
        if t.get("show_headers"):
            r["headers"] = {k: v[:200] for k, v in h.items() if k in t["show_headers"]}
        try:
            d = json.loads(text)
            r["keys"] = list(d.keys())[:20] if isinstance(d, dict) else f"list[{len(d)}]"
            for k in ("total", "count", "hits", "totalCount"):
                if isinstance(d, dict) and k in d:
                    r[k] = d[k]
        except ValueError:
            pass
        r["start"] = text[:600]
    except Exception as e:
        r["error"] = str(e)[:300]
    return r


def trial_runs():
    wl = fj.load(os.path.join(ROOT, "watchlist.json"), {})
    terms = wl.get("search_terms", ["strategy"])
    out = {}
    for c in wl.get("companies", []):
        if not c.get("trial"):
            continue
        res = []
        for t in [c] + [dict(c, **alt) for alt in c.get("try", [])]:
            name = f"{t['ats']}:{t.get('id') or t.get('host') or t.get('base') or t.get('site', '')}"
            try:
                jobs = fj.FETCHERS[t["ats"]](t, terms[:2])
                res.append({"reader": name, "ok": True, "jobs": len(jobs),
                            "sample": [f"{j['title']} | {j['location']} | {j['url']}" for j in jobs[:5]]})
            except Exception as e:
                res.append({"reader": name, "ok": False, "error": str(e)[:300],
                            "trace": traceback.format_exc()[-600:]})
        out[c["name"]] = res
    return out


def main():
    tpath = os.path.join(ROOT, "probe_targets.json")
    if not os.path.exists(tpath):
        print("probe: no probe_targets.json, nothing to do")
        return
    h = sha(tpath)
    prev = fj.load(OUT_JSON, {})
    if prev.get("targets_hash") == h and "--force" not in sys.argv:
        print("probe: targets unchanged since last probe, skipping")
        return
    targets = json.load(open(tpath))
    result = {"run": fj.NOW.strftime("%Y-%m-%dT%H:%MZ"), "targets_hash": h, "companies": {}}
    for name, t in targets.get("companies", {}).items():
        result["companies"][name] = {
            "pages": [probe_page(u) for u in t.get("pages", [])],
            "api": [probe_api(a) for a in t.get("api", [])],
        }
        print("probed", name)
    result["trial_readers"] = trial_runs()
    fj.save(OUT_JSON, result)

    lines = [f"# Probe {result['run']}", ""]
    for name, r in result["companies"].items():
        lines.append(f"## {name}")
        for p in r["pages"]:
            if "error" in p:
                lines.append(f"- page {p['url']}: ERROR {p['error']}")
                continue
            lines.append(f"- page {p['url']}: {p['status']}, {p['bytes']} bytes, title '{p['title']}'; job systems: {p['ats'] or 'none found'}")
            for k, v in (p.get("board_tests") or {}).items():
                lines.append(f"  - {k}: {v}")
        for a in r["api"]:
            lines.append(f"- api {a.get('name') or a['url']}: " + (f"ERROR {a['error']}" if "error" in a else
                         f"{a['status']} {a['content_type']} {a['bytes']} bytes keys={a.get('keys')}"))
        lines.append("")
    lines.append("## Trial readers")
    for name, res in result["trial_readers"].items():
        for x in res:
            lines.append(f"- {name} / {x['reader']}: " + (f"{x['jobs']} jobs; {x['sample'][:2]}" if x["ok"] else f"ERROR {x['error']}"))
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w") as f:
        f.write("\n".join(lines) + "\n")
    print("probe: wrote", OUT_JSON)


if __name__ == "__main__":
    main()
