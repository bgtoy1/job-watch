"""Offline tests for the career-site readers added 2026-11-13 and for trial mode.

The sample responses follow each site's expected format; the probe and the trial
runs confirm the real formats.
"""
import json, os, shutil, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_jobs as fj

fj.pause = lambda: None
C = lambda **kw: dict({"name": "Co", "sector": "Tech"}, **kw)
T = ["strategy"]

AMAZON = {"hits": 2, "jobs": [
    {"id_icims": "301", "title": "Sr. Manager, Strategy, Prime Video", "normalized_location": "Culver City, California, USA",
     "job_path": "/en/jobs/301/sr-manager", "posted_date": "November 10, 2026",
     "preferred_qualifications": "Pay $166,400 - $287,700 per year"},
    {"id_icims": "302", "title": "Director, Business Operations", "location": "US, Virtual", "job_path": "/en/jobs/302/x",
     "posted_date": "November 9, 2026", "is_virtual": True}]}
EIGHTFOLD = {"count": 1, "positions": [{"id": 9, "name": "Director, Business Strategy", "locations": ["Redmond, Washington, United States"], "t_create": 1762900000}]}
GCS = {"operationResult": {"result": {"jobs": [{"jobId": "1888", "title": "Director, Strategy", "postingDate": "2026-11-11T00:00:00+00:00",
                                                 "properties": {"locations": ["United States, Multiple Locations, Multiple Locations"], "workSiteFlexibility": "Up to 100% work from home"}}]}}}
UBER = {"data": {"results": [{"id": 1501, "title": "Director, Strategy & Planning", "location": {"city": "San Francisco", "region": "California", "countryName": "United States"},
                              "allLocations": [{"city": "Los Angeles", "region": "California", "countryName": "United States"}],
                              "creationDate": "2026-11-05T12:00:00.000Z", "description": "Base salary $250,000 - $300,000"}]}}
ATL = [{"id": 2222, "title": "Principal, Strategy and Business Operations", "locations": ["Remote - United States"],
        "overview": "<p>US base pay $220,000 - $280,000</p>", "portalJobPost": {"updatedDate": "2026-11-08T00:00:00Z"}}]
GOOGLE_HTML = """<ul><li><div><h3 class="QJPWVe">Director, Strategy and Operations, YouTube</h3>
<span class="r0wTof">Los Angeles, CA, USA</span><a href="jobs/results/123456789012-director-strategy-and-operations-youtube?q=strategy">Learn more</a></div></li></ul>"""
META_HTML = '<script>{"data":{"job_search":[{"id":"1234567890123","title":"Strategy Director, Reality Labs","locations":["Burbank, CA","Remote, US"],"teams":[]}]}}</script>'
SF_RSS = """<rss><channel><item><title>Director, Strategic Planning (Los Angeles, CA, US, 90038)</title>
<link>https://careers.paramount.com/job/Los-Angeles-Director-CA-90038/1234567/</link><pubDate>Tue, 11 Nov 2026 08:00:00 GMT</pubDate>
<description>&lt;p&gt;$190,000 - $240,000&lt;/p&gt;</description></item></channel></rss>"""
SF_HTML = """<table><tr class="data-row"><td><span class="jobTitle hidden-phone"><a href="/job/New-York-VP-Strategy/7654321/" class="jobTitle-link">VP, Strategy</a></span></td>
<td><span class="jobLocation">New York, NY, US</span></td><td><span class="jobDate">Nov 7, 2026</span></td></tr></table>"""
AVATURE = """<article><h3><a href="https://jobs.ea.com/en_US/careers/JobDetail/Director-Business-Strategy/212771" class="link">Director, Business Strategy</a></h3>
<div class="list-item-location">Redwood City, California, United States</div></article>"""
FOX = """<div class="job"><a href="/Search/JobDetail/R50028389/director-corporate-development-fox-corporation">Director, Corporate Development</a>
<span>Los Angeles, CA</span></div>"""
CLINCH = """<a href="/jobs/senior-director-corporate-strategy-united-states">Senior Director Corporate Strategy</a>
<a href="/jobs/search">See Open Roles</a>"""
GH_HTML = '<script>window.__remixContext = {"state":{"loaderData":{"routes/$url_token":{"jobPosts":{"data":[{"id":55,"title":"Director, Strategy & Operations","location":"Los Angeles, CA","absolute_url":"https://job-boards.greenhouse.io/whatnot/jobs/55","published_at":"2026-11-10T00:00:00-08:00"}],"page":1,"total_pages":1}}}}};</script>'
APPLE_HTML = '<script>window.__x={"searchResults":[{"positionId":"200555001","postingTitle":"Director, Services Strategy","postingDate":"Nov 10, 2026","locations":[{"name":"Culver City"}],"transformedPostingTitle":"director-services-strategy"}]}</script>'
PAYLOC = {"jobs": [{"jobId": 4304980, "title": "Director, Strategy", "jobLocation": {"city": "Burbank", "state": "CA"}, "publishedDate": "2026-11-01T00:00:00"}]}


def fake_http(url, body=None, **kw):
    if "amazon.jobs" in url: return AMAZON if "offset=0" in url else {"jobs": []}
    if "apply.careers.microsoft.com" in url: return EIGHTFOLD
    if "gcsservices" in url: return GCS
    if "atlassian.com/endpoint" in url: return ATL
    if "paylocity" in url: return PAYLOC
    raise AssertionError(url)


def fake_raw(url, body=None, headers=None, form=False, **kw):
    if "jobs.apple.com/api" in url: raise RuntimeError("HTTP 404 for " + url)  # force HTML fallback
    if "jobs.apple.com/en-us/search" in url: return 200, {}, APPLE_HTML
    if "uber.com/api" in url:
        assert headers["x-csrf-token"] == "x"
        return 200, {}, json.dumps(UBER) if body["page"] == 0 else json.dumps({"data": {"results": []}})
    if "google.com/about/careers" in url: return 200, {}, GOOGLE_HTML
    if "metacareers.com" in url: return 200, {}, META_HTML
    if "/services/rss/job" in url: return 200, {}, SF_RSS if "strategy" in url else "<rss></rss>"
    if "careers.paramount.com/search" in url: return 200, {}, SF_HTML
    if "jobs.ea.com" in url: return 200, {}, AVATURE
    if "foxcareers.com" in url: return 200, {}, FOX
    if "careers.godaddy" in url: return 200, {}, CLINCH
    if "job-boards.greenhouse.io" in url: return 200, {}, GH_HTML
    raise AssertionError(url)


fj.http = fake_http
fj.http_raw = fake_raw

def one(fetch, c, terms=T):
    jobs = fetch(c, terms)
    assert jobs, fetch.__name__
    return jobs

a = one(fj.fetch_amazon, C())
assert a[0]["title"] == "Sr. Manager, Strategy, Prime Video" and a[0]["pay"] == "$166–288K" and a[0]["posted"] == "2026-11-10", a[0]
assert a[1]["location"].endswith("Remote") and a[1]["url"] == "https://www.amazon.jobs/en/jobs/302/x"
e = one(fj.fetch_eightfold, C(host="apply.careers.microsoft.com", domain="microsoft.com"))
assert e[0]["title"] == "Director, Business Strategy"
g = one(fj.fetch_microsoft_gcs, C())
assert g[0]["location"].endswith("; Remote") and g[0]["posted"] == "2026-11-11", g[0]
ap = one(fj.fetch_apple, C())
assert ap[0]["title"] == "Director, Services Strategy" and ap[0]["location"] == "Culver City" and ap[0]["posted"] == "2026-11-10", ap[0]
u = one(fj.fetch_uber, C())
assert "Los Angeles, California" in u[0]["location"] and u[0]["pay"] == "$250–300K", u[0]
at = one(fj.fetch_atlassian, C())
assert at[0]["pay"] == "$220–280K" and at[0]["location"] == "Remote - United States", at[0]
go = one(fj.fetch_google, C())
assert go[0]["title"] == "Director, Strategy and Operations, YouTube" and go[0]["location"] == "Los Angeles, CA, USA", go[0]
me = one(fj.fetch_meta, C())
assert me[0]["location"] == "Burbank, CA; Remote, US", me[0]
sf = one(fj.fetch_successfactors, C(host="careers.paramount.com"))
assert sf[0]["title"] == "Director, Strategic Planning" and sf[0]["location"] == "Los Angeles, CA, US, 90038" and sf[0]["id"] == "1234567", sf[0]
assert sf[0]["pay"] == "$190–240K" and sf[0]["posted"] == "2026-11-11", sf[0]
sf2 = one(fj.fetch_successfactors, C(host="careers.paramount.com"), ["planning"])  # RSS empty -> HTML
assert sf2[0]["title"] == "VP, Strategy" and sf2[0]["location"] == "New York, NY, US" and sf2[0]["posted"] == "2026-11-07", sf2[0]
av = one(fj.fetch_avature, C(base="https://jobs.ea.com/en_US/careers"))
assert av[0]["id"] == "212771" and "Redwood City" in av[0]["location"], av[0]
fx = one(fj.fetch_foxcareers, C())
assert fx[0]["title"] == "Director, Corporate Development" and fx[0]["location"] == "Los Angeles, CA", fx[0]
cl = one(fj.fetch_clinch, C(host="careers.godaddy"))
assert len(cl) == 1 and cl[0]["location"] == "United States", cl
gh = one(fj.fetch_greenhouse_html, C(id="whatnot"))
assert gh[0]["title"] == "Director, Strategy & Operations" and gh[0]["posted"] == "2026-11-10", gh[0]
pl = one(fj.fetch_paylocity, C(id="abc"))
assert pl[0]["location"] == "Burbank, CA", pl[0]

# trial mode: roles reported in company_status, not logged as events
tmp = tempfile.mkdtemp()
fj.DATA, fj.REPORTS = os.path.join(tmp, "data"), os.path.join(tmp, "reports")
wl = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist.json")))
wl["companies"] = [{"name": "Amazon", "ats": "amazon", "trial": True},
                   {"name": "Atlassian", "ats": "atlassian"}]
wp = os.path.join(tmp, "wl.json"); json.dump(wl, open(wp, "w"))
new, closed, status = fj.run(wp)
assert status["Amazon"]["trial"] and status["Amazon"]["matches"] == 2, status["Amazon"]
assert all(j["company"] != "Amazon" for j in new)
assert [j["company"] for j in new] == ["Atlassian"]
rep = open(os.path.join(fj.REPORTS, "latest.md")).read()
assert "Trial readers" in rep and "Amazon: OK via amazon" in rep, rep
shutil.rmtree(tmp)
print("ALL READER TESTS PASSED")
