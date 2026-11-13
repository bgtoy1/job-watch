# Probe 2026-11-13T19:33Z

## Sphere Entertainment
- page https://www.sphereentertainmentco.com/careers/: 200, 164989 bytes, title 'Careers | Sphere Entertainment Co.'; job systems: none found
- page https://www.sphereentertainmentco.com/?p=6105: ERROR HTTP 404 for https://www.sphereentertainmentco.com/?p=6105

## UTA
- page https://www.unitedtalent.com/careers/: 200, 386520 bytes, title ''; job systems: {'ashby': ['united-talent-agency']}
  - ashby:united-talent-agency: {'ok': True, 'jobs': 46, 'sample': ['Software Development Engineer - Front End | Los Angeles Office', 'Executive Assistant - Technology | Nashville, TN', 'Coordinator, Cash Management & Client Payments | London, UK', 'Associate, Client Accounting [Future Opportunities] | Los Angeles, CA; New York, NY']}
- page https://www.unitedtalent.com/careers/job-openings/: 200, 369080 bytes, title ''; job systems: none found

## Cosm
- page https://www.cosm.com/careers: 200, 36341 bytes, title 'Careers | Cosm'; job systems: none found
- page https://jobs.dayforcehcm.com/en-US/cosm/CANDIDATEPORTAL: 200, 466991 bytes, title 'Job Board | Dayforce Jobs'; job systems: none found

## Legendary Entertainment
- page https://www.legendary.com/careers/: ERROR HTTP 404 for https://www.legendary.com/careers/
- page https://recruiting.paylocity.com/recruiting/jobs/details/4304980: 200, 4632 bytes, title 'Job Not Found'; job systems: none found

## Niantic Spatial
- page https://www.nianticspatial.com/careers: 200, 61638 bytes, title 'Careers | Life at Niantic Spatial | Niantic Spatial, Inc.'; job systems: none found
- page https://nianticlabs.com/careers: 200, 94695 bytes, title 'Scopely: Inspire Play, Every Day'; job systems: {'phenom': ['cdn.phenompeople.com', 'phenom']}

## Whatnot
- page https://www.whatnot.com/careers: 200, 739790 bytes, title 'Careers · Whatnot: Shop, Sell, Connect'; job systems: none found
- page https://job-boards.greenhouse.io/whatnot: ERROR HTTP 404 for https://job-boards.greenhouse.io/whatnot
- api boards-api: ERROR HTTP 404 for https://boards-api.greenhouse.io/v1/boards/whatnot/jobs
- api api.greenhouse.io: ERROR HTTP 404 for https://api.greenhouse.io/v1/boards/whatnot/jobs

## Superhuman (Grammarly)
- page https://superhuman.com/careers: 200, 939739 bytes, title 'Explore career opportunities at Superhuman'; job systems: {'greenhouse': ['grammarly']}
  - greenhouse:grammarly: {'ok': False, 'error': 'HTTP 404 for https://boards-api.greenhouse.io/v1/boards/grammarly/jobs?content=true'}
- page https://job-boards.greenhouse.io/grammarly: ERROR HTTP 404 for https://job-boards.greenhouse.io/grammarly
- api boards-api grammarly: ERROR HTTP 404 for https://boards-api.greenhouse.io/v1/boards/grammarly/jobs
- api boards-api superhuman: ERROR HTTP 404 for https://boards-api.greenhouse.io/v1/boards/superhuman/jobs

## Disney
- page https://jobs.disneycareers.com/search-jobs/strategy: 200, 205871 bytes, title 'Careers at Disney | Disney Careers'; job systems: {'phenom': ['cdn.phenompeople.com', 'phenom']}
- api workday disneycareer 'strategy': 200 application/json 2306 bytes keys=['total', 'jobPostings', 'facets', 'userAuthenticated']
- api workday disneycareer all: 200 application/json 6294 bytes keys=['total', 'jobPostings', 'facets', 'userAuthenticated']

## GoDaddy
- page https://careers.godaddy/jobs/search?query=strategy: 200, 122559 bytes, title 'Search Page'; job systems: {'clinch': ['clinchtalent.com']}
- api greenhouse godaddy: 200 application/json 122684 bytes keys=['jobs', 'meta']

## Fox
- page https://www.foxcareers.com/Search/SearchResults?keyword=strategy: 200, 60202 bytes, title 'Job Search Results – Job Search | FOX Careers'; job systems: none found
- page https://www.foxcareers.com/Search/JobDetail/R50030010/director-strategic-growth-fox-corporation: 200, 35591 bytes, title 'Director, Strategic Growth in Los Angeles, California | FOX Careers'; job systems: none found

## Electronic Arts
- page https://jobs.ea.com/en_US/careers/SearchJobs/?keyword=strategy&jobRecordsPerPage=50&jobOffset=0&listFilterMode=1: 200, 224727 bytes, title 'Job Search | Electronic Arts'; job systems: none found
- page https://ea.gr8people.com/jobs: ERROR HTTP 403 for https://ea.gr8people.com/jobs

## Paramount Skydance
- page https://careers.paramount.com/search/?q=strategy: 200, 328094 bytes, title 'Strategy - Paramount Jobs'; job systems: none found
- api rss: 200 application/rss+xml 208368 bytes keys=None

## NBCUniversal
- page https://www.nbcunicareers.com/find-a-job: ERROR HTTP 403 for https://www.nbcunicareers.com/find-a-job
- api smartrecruiters NBCUniversal3: 200 application/json; charset=utf-8 16562 bytes keys=['offset', 'limit', 'totalFound', 'content']

## IMAX
- api workday: 200 application/json 4829 bytes keys=['total', 'jobPostings', 'facets', 'userAuthenticated']

## Microsoft
- page https://careers.microsoft.com/v2/global/en/home.html: 200, 29411 bytes, title 'Home | Microsoft Careers'; job systems: none found
- api eightfold: ERROR HTTP 403 for https://apply.careers.microsoft.com/api/apply/v2/jobs?domain=microsoft.com&query=strategy&location=United%20States&start=0&num=10
- api gcs: ERROR URLError: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate is not valid for 'gcsservices.careers.microsoft.com'. (_ssl.c:1011)> for https://gcsservices.careers.microsoft.com/search/api/v1/search?q=strategy&lc=United%20States&l=en_us&pg=1&pgSz=

## Amazon
- api search.json: 200 application/json; charset=utf-8 48859 bytes keys=['error', 'hits', 'facets', 'content', 'jobs', 'job_posting_search_request']

## Apple
- page https://jobs.apple.com/en-us/search?search=strategy&location=united-states-USA: 200, 318450 bytes, title 'Search Jobs - United States - Jobs - Careers at Apple'; job systems: {'phenom': ['phenom']}
- api csrf v1: 200  0 bytes keys=None
- api csrf old: ERROR HTTP 404 for https://jobs.apple.com/api/csrfToken

## Uber
- api loadSearchJobsResults: ERROR HTTP 404 for https://www.uber.com/api/loadSearchJobsResults?localeCode=en

## Atlassian
- api listings: 200 application/json; charset=utf-8 2022543 bytes keys=list[287]

## Google / YouTube
- page https://www.google.com/about/careers/applications/jobs/results?q=strategy&location=Los%20Angeles%2C%20CA%2C%20USA&target_level=DIRECTOR: 200, 1307155 bytes, title 'Search Jobs — Google Careers'; job systems: none found

## Meta
- page https://www.metacareers.com/jobs?q=strategy&offices[0]=Los%20Angeles%2C%20CA: ERROR HTTP 400 for https://www.metacareers.com/jobs?q=strategy&offices[0]=Los%20Angeles%2C%20CA

## Mattel
- page https://jobs.mattel.com/: 200, 1849493 bytes, title 'Join Our Team! | Careers | Mattel, Inc.'; job systems: none found

## HubSpot
- page https://www.hubspot.com/careers/jobs: 200, 497309 bytes, title 'HubSpot Careers | All Openings'; job systems: none found

## Lionsgate
- page https://www.lionsgate.com/careers: 200, 60240 bytes, title ''; job systems: {'successfactors': ['career4.successfactors.com']}

## TikTok
- page https://lifeattiktok.com/search?keyword=strategy: 200, 110313 bytes, title 'Job Search'; job systems: none found

## Trial readers
- Grammarly / greenhouse:grammarly: ERROR HTTP 404 for https://boards-api.greenhouse.io/v1/boards/grammarly/jobs?content=true
- Grammarly / greenhouse:superhuman: ERROR HTTP 404 for https://boards-api.greenhouse.io/v1/boards/superhuman/jobs?content=true
- Grammarly / greenhouse_html:grammarly: ERROR HTTP 404 for https://job-boards.greenhouse.io/grammarly?page=1
- Grammarly / ashby:superhuman: ERROR HTTP 404 for https://api.ashbyhq.com/posting-api/job-board/superhuman?includeCompensation=true
- Whatnot / greenhouse:whatnot: ERROR HTTP 404 for https://boards-api.greenhouse.io/v1/boards/whatnot/jobs?content=true
- Whatnot / greenhouse_html:whatnot: ERROR HTTP 404 for https://job-boards.greenhouse.io/whatnot?page=1
- Whatnot / ashby:whatnot: ERROR HTTP 404 for https://api.ashbyhq.com/posting-api/job-board/whatnot?includeCompensation=true
- Amazon / amazon:: 539 jobs; ['Sr. Software Development Engineer, Amazon Quick | Seattle, Washington, USA | https://www.amazon.jobs/en/jobs/10604744/sr-software-development-engineer-amazon-quick', 'Sr. Manager, Software Development | North Reading, Massachusetts, USA | https://www.amazon.jobs/en/jobs/10604626/sr-manager-software-development']
- Microsoft / eightfold:apply.careers.microsoft.com: ERROR HTTP 403 for https://apply.careers.microsoft.com/api/apply/v2/jobs?domain=microsoft.com&sort_by=timestamp&location=United%20States&query=strategy&start=0&num=50
- Microsoft / microsoft_gcs:apply.careers.microsoft.com: ERROR URLError: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate is not valid for 'gcsservices.careers.microsoft.com'. (_ssl.c:1011)> for https://gcsservices.careers.microsoft.com/search/api/v1/search?q=strategy&lc=United%20States&l=en_us&pg=1&pgSz=
- Apple / apple:: 0 jobs; []
- Uber / uber:: ERROR HTTP 404 for https://www.uber.com/api/loadSearchJobsResults?localeCode=en
- Atlassian / atlassian:: 287 jobs; ['Account Executive | Manila - Philippines -   Manila,  1634 Philippines; Remote - APAC - Remote | https://www.atlassian.com/company/careers/details/27339', 'Account Executive, Mid Market | Remote - India - Remote; Remote - Remote | https://www.atlassian.com/company/careers/details/25482']
- Google / YouTube / google:: 18 jobs; ['Strategy and Operations Principal Lead, Global Partnerships, Third-Party Data and DeepMind | New York, NY, USA; Chicago, IL, USA | https://www.google.com/about/careers/applications/jobs/results/106999002297180870-strategy-and-operations-principal-lead-global-partnerships-thirdparty-data-and-deepmind', 'Global Content Strategy Lead, YouTube Business | San Bruno, CA, USA; Los Angeles, CA, USA | https://www.google.com/about/careers/applications/jobs/results/79032543134786246-global-content-strategy-lead-youtube-business']
- Meta / meta:: ERROR HTTP 400 for https://www.metacareers.com/jobs?q=strategy&offices[0]=Los%20Angeles%2C%20CA&offices[1]=Burbank%2C%20CA&offices[2]=Remote%2C%20US
- Paramount Skydance / successfactors:careers.paramount.com: 34 jobs; ['Commercial Growth and Retail Sr Manager | Warsaw, MZ, PL, 00-560 | https://careers.paramount.com/job/Warsaw-Commercial-Growth-and-Retail-Sr-Manager-MZ-00-560/1420459800/?feedId=null&utm_source=J2WRSS&utm_medium=rss&utm_campaign=J2W_RSS', 'Growth Marketer, The Free Press | New York, NY, US, 10036 | https://careers.paramount.com/job/New-York-Growth-Marketer%2C-The-Free-Press-NY-10036/1430772300/?feedId=null&utm_source=J2WRSS&utm_medium=rss&utm_campaign=J2W_RSS']
- Electronic Arts / avature:https://jobs.ea.com/en_US/careers: 20 jobs; ['Site Reliability Engineer II | Hyderabad, India | https://jobs.ea.com/en_US/careers/JobDetail/Site-Reliability-Engineer-II/216650', 'Software Engineer Intern | Kuala Lumpur, Malaysia | https://jobs.ea.com/en_US/careers/JobDetail/Software-Engineer-Intern/215931']
- Fox / foxcareers:: 0 jobs; []
- GoDaddy / greenhouse:godaddy: 29 jobs; ['Aftermarket - Technical Support I | Bulgaria | https://careers.godaddy/jobs?gh_jid=7728168003', 'Corporate Domain Client Success Manager | United States | https://careers.godaddy/jobs?gh_jid=8043862003']
- GoDaddy / clinch:godaddy: 27 jobs; ['Senior Director Corporate Strategy | United States | https://careers.godaddy/jobs/senior-director-corporate-strategy-united-states', 'Principal Product Manager - GoDaddy Capital | Ontario Canada | https://careers.godaddy/jobs/principal-product-manager-godaddy-capital-ontario-canada']
