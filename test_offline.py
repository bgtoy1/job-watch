"""Offline test: fake API responses, two runs, check filters, new/closed logic and report."""
import json, os, shutil, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_jobs as fj

GH = {"jobs": [
    {"id": 1, "title": "Director, Strategy & Operations", "location": {"name": "Los Angeles, CA"},
     "absolute_url": "https://gh/1", "first_published": "2026-11-08T10:00:00Z",
     "content": "&lt;p&gt;The base salary range is $197,000 - $290,000 USD.&lt;/p&gt;"},
    {"id": 2, "title": "Senior Software Engineer", "location": {"name": "Remote, US"}, "absolute_url": "https://gh/2", "content": ""},
    {"id": 3, "title": "Director, Finance", "location": {"name": "London, UK"}, "absolute_url": "https://gh/3", "content": ""},
    {"id": 4, "title": "Head of Business Operations", "location": {"name": "New York, NY; Remote - US"},
     "absolute_url": "https://gh/4", "updated_at": "2026-11-01T00:00:00Z", "content": "$150,000 - $180,000"},
    {"id": 5, "title": "Associate Director, Corporate Strategy", "location": {"name": "Remote"}, "absolute_url": "https://gh/5", "content": ""},
    {"id": 6, "title": "Strategy Associate", "location": {"name": "Remote"}, "absolute_url": "https://gh/6", "content": ""},
]}
LEVER = [{"id": "abc", "text": "VP, Head of Strategy & Business Operations", "hostedUrl": "https://lever/abc",
          "createdAt": 1762600000000, "categories": {"location": "Los Angeles"},
          "salaryRange": {"min": 250000, "max": 325000, "interval": "per-year-salary"}}]
ASHBY = {"jobs": [{"id": "u1", "title": "Strategy & Operations Director", "location": "Remote (US)", "isRemote": True,
                   "publishedAt": "2026-11-07T00:00:00Z", "jobUrl": "https://ashby/u1",
                   "secondaryLocations": [{"location": None}], "compensation": {"compensationTierSummary": None, "summaryComponents": [{"compensationType": "Salary", "minValue": 281000, "maxValue": 330000}]}}]}
WD_LIST = {"total": 2, "jobPostings": [
    {"title": "Director, CTO FP&A", "externalPath": "/job/United-States-of-America/Director--CTO-FP-A_JR1", "locationsText": "United States of America", "postedOn": "Posted 2 Days Ago", "bulletFields": ["JR1"]},
    {"title": "Sr Director, Operations", "externalPath": "/job/Beverly-Hills/Sr-Dir-Ops_JR2", "locationsText": "Beverly Hills, CA", "postedOn": "Posted Today", "bulletFields": ["JR2"]}]}
WD_DETAIL = {
    "JR1": {"jobPostingInfo": {"jobDescription": "<p>$129,210.00 - $281,305.00</p>", "location": "United States of America", "startDate": "2026-11-07", "canApply": True}},
    "JR2": {"jobPostingInfo": {"jobDescription": "<p>pay $216,000 to $270,000</p>", "location": "Beverly Hills, CA", "startDate": "2026-11-09", "canApply": False}},
}
NFX = {"count": 1, "positions": [{"id": 790, "name": "Manager, Finance & Strategy, Experiences", "locations": ["Los Angeles, California"], "t_create": 1762500000, "canonicalPositionUrl": "https://nfx/790"}]}

second_run = {"on": False}

def fake_http(url, body=None, **kw):
    if "broken" in url: raise RuntimeError("HTTP 404 for " + url)
    if "greenhouse" in url:
        d = json.loads(json.dumps(GH))
        if second_run["on"]:
            d["jobs"] = [j for j in d["jobs"] if j["id"] != 1]  # job 1 closes
            for j in d["jobs"]:
                if j["id"] == 5: j["location"] = {"name": "Dallas, TX"}  # still posted, now filtered out
            d["jobs"].append({"id": 7, "title": "Chief of Staff, Product", "location": {"name": "Santa Monica, CA"}, "absolute_url": "https://gh/7", "first_published": "2026-11-10T00:00:00Z", "content": ""})
        return d
    if "lever" in url: return LEVER
    if "ashby" in url: return ASHBY
    if url.endswith("/jobs"): return WD_LIST if body["offset"] == 0 and body["searchText"] == "strategy" else {"jobPostings": []}
    if "/wday/cxs/" in url: return WD_DETAIL[url.rsplit("_", 1)[-1]]
    if "netflix" in url: return NFX
    if "broken" in url: raise RuntimeError("HTTP 404 for " + url)
    raise AssertionError(url)

fj.http = fake_http
fj.pause = lambda: None
tmp = tempfile.mkdtemp()
fj.DATA, fj.REPORTS = os.path.join(tmp, "data"), os.path.join(tmp, "reports")
wl = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist.json")))
wl["companies"] = [
    {"name": "GH Co", "ats": "greenhouse", "id": "x"},
    {"name": "Lever Co", "ats": "lever", "id": "x"},
    {"name": "Ashby Co", "ats": "ashby", "id": "x"},
    {"name": "WD Co", "ats": "workday", "host": "wd.example", "tenant": "t", "site": "s"},
    {"name": "Netflix", "ats": "netflix"},
    {"name": "Broken", "ats": "greenhouse", "id": "broken"},
    {"name": "Fallback Co", "ats": "greenhouse", "id": "broken2", "try": [{"ats": "ashby", "id": "y"}]},
]
wp = os.path.join(tmp, "wl.json"); json.dump(wl, open(wp, "w"))

new, closed, status = fj.run(wp)
titles = sorted(j["title"] for j in new)
print("run1 new:", titles)
assert "Director, Strategy & Operations" in titles
assert "Associate Director, Corporate Strategy" in titles
assert "Strategy Associate" not in titles
assert "Senior Software Engineer" not in titles
assert "Director, Finance" not in titles            # London
assert "Head of Business Operations" not in titles  # pay top 180K < 200K
assert "Director, CTO FP&A" in titles
assert "Sr Director, Operations" not in titles      # canApply false
assert "Manager, Finance & Strategy, Experiences" in titles  # Netflix manager allowed
assert not status["Broken"]["ok"]
assert status["Fallback Co"]["ok"] and status["Fallback Co"]["board"] == "ashby:y", status["Fallback Co"]
assert sum(1 for j in new if j["company"] == "Fallback Co") == 1
F = fj.Filters(wl["filters"])
L = lambda loc: F.location_ok({"location": loc})
assert L("Los Angeles, CA") and L("Remote - US") and L("United States of America") and L("Distributed; Hybrid")
assert L("New York, NY; Los Angeles, CA") and L("Remote, Canada; Remote, United States")
assert not L("Minneapolis, Minnesota, United States") and not L("Remote, Canada") and not L("London, UK")
assert not L("Dallas, TX") and not L("United States, San Mateo, CA")
T = lambda t, co="X": F.title_ok({"title": t, "company": co})
assert T("Director, Strategy & Operations") and T("Senior Director, Corporate FP&A") and T("Chief of Staff, Product")
assert not T("Regional Vice President, Strategic East Sales") and not T("Director of Strategic Accounts, Western U.S.")
assert not T("Marketing Operations Manager") and not T("Lead Client Director - Strategic Healthcare")
assert T("Manager, Finance & Strategy", "Netflix") and not T("Manager, Finance & Strategy", "X")
gh1 = [j for j in new if j["id"] == "1"][0]
assert gh1["pay"] == "$197–290K" and gh1["posted"] == "2026-11-08", gh1
wd = [j for j in new if j["id"] == "JR1"][0]
assert wd["pay"] == "$129–281K" and wd["posted"] == "2026-11-07", wd

second_run["on"] = True
new2, closed2, _ = fj.run(wp)
print("run2 new:", [j["title"] for j in new2], "closed:", [j["title"] for j in closed2])
assert [j["title"] for j in new2] == ["Chief of Staff, Product"]
assert [j["title"] for j in closed2] == ["Director, Strategy & Operations"], closed2  # job 5 filtered, not closed
print(open(os.path.join(fj.REPORTS, "latest.md")).read()[:1500])
shutil.rmtree(tmp)
print("ALL TESTS PASSED")
