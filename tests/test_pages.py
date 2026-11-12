"""ats: page -- reading jobs off careers pages (canned HTML, no network)."""
import json

import pytest

from radar import fetchers, pages
from radar.tracking import feed_from_urls


class Page:
    def __init__(self, text, url="", ok=True):
        self.text, self.url, self.ok = text, url, ok
    def raise_for_status(self):
        pass


RADANCY = """<ul class="search-list">
<li><a href="/job/mountain-view/principal-product-manager/27595/111" data-title="Principal Product Manager, Growth">
  <h2>Principal Product Manager, Growth</h2><span class="job-location">Mountain View, California</span></a></li>
<li><a href="/job/remote/staff-pm/27595/222"><h2>Staff Product Manager, Identity</h2>
  <span class="job-location">Multiple Locations</span></a></li>
</ul>"""

# SuccessFactors lists each job twice (desktop + phone); the location comes after both links
SUCCESSFACTORS = """<tr class="data-row"><td>
<a href="/ey/job/NY-Director-PM/123/" class="jobTitle-link">EY-Parthenon - Product Manager - Director</a>
<a class="jobTitle-link" href="/ey/job/NY-Director-PM/123/">EY-Parthenon - Product Manager - Director</a></td>
<td class="colLocation"><span class="jobLocation"> New York, NY, US, 10001 <small>+1 more&hellip;</small></span></td></tr>
<tr class="data-row"><td><a href="/ey/job/Hoboken-PM/456/" class="jobTitle-link">Director, Business Product Manager</a></td>
<td class="colLocation"><span class="jobLocation">Hoboken, NJ, US</span></td></tr>"""

def test_job_links_radancy():
    jobs = pages.read_page(RADANCY, "https://jobs.intuit.com/search-jobs/product%20manager")
    assert [j["title"] for j in jobs] == ["Principal Product Manager, Growth", "Staff Product Manager, Identity"]
    assert jobs[0]["location"] == "Mountain View, California"
    assert jobs[0]["url"] == "https://jobs.intuit.com/job/mountain-view/principal-product-manager/27595/111"


def test_job_links_duplicate_links_and_location_after():
    jobs = pages.read_page(SUCCESSFACTORS, "https://careers.ey.com/ey/search/?q=pm")
    assert len(jobs) == 2
    assert jobs[0]["location"] == "New York, NY, US, 10001"
    assert jobs[1]["location"] == "Hoboken, NJ, US"


def test_revolut_embedded_positions():
    data = {"props": {"pageProps": {"positions": [
        {"id": "aa57", "text": "Head of Product (Fraud)", "locations": [{"name": "London"}, {"name": "UK - Remote"}]}]}}}
    page = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
    [job] = pages.read_page(page, "https://www.revolut.com/careers/")
    assert job["title"] == "Head of Product (Fraud)" and job["location"] == "London; UK - Remote"
    assert job["url"] == "https://www.revolut.com/careers/position/head-of-product-fraud-aa57/"


def test_jsonld_jobposting():
    block = {"@type": "JobPosting", "title": "Director of Product", "url": "/jobs/9", "identifier": {"value": "R9"},
             "datePosted": "2026-11-01T00:00:00Z",
             "jobLocation": {"address": {"addressLocality": "Seattle", "addressRegion": "WA", "addressCountry": "US"}}}
    page = f'<script type="application/ld+json">{json.dumps(block)}</script>'
    [job] = pages.read_page(page, "https://example.com/careers")
    assert job == {"id": "R9", "title": "Director of Product", "location": "Seattle, WA, US",
                   "url": "https://example.com/jobs/9", "posted": "2026-11-01"}


def fake_site(served, robots=""):
    """A get() that serves `served` pages plus a robots.txt, and records what was asked for."""
    asked = []
    def get(url, headers, timeout):
        if url.endswith("/robots.txt"):
            return Page(robots, url, ok=bool(robots))
        asked.append(url)
        return Page(served[url], url)
    return get, asked


def test_follows_pages_until_nothing_new():
    base = "https://careers.ey.com/ey/search/?q=pm"
    second = SUCCESSFACTORS.replace("/123/", "/789/").replace("/456/", "/790/")
    get, asked = fake_site({base: SUCCESSFACTORS, base + "&startrow=25": second,
                            base + "&startrow=50": second})       # site repeats the last page
    jobs = pages.fetch_page(base, {}, get=get)
    assert len(jobs) == 4
    assert asked[-1].endswith("startrow=50")


def test_robots_txt_is_respected():
    url = "https://jobs.example.com/search-jobs/pm"
    get, asked = fake_site({url: RADANCY}, robots="User-agent: *\nDisallow:/search-jobs/")
    with pytest.raises(PermissionError, match="robots.txt"):
        pages.fetch_page(url, {}, get=get)
    assert asked == []                                  # the page itself was never opened
    # Paging stops at the first disallowed results page
    base = "https://careers.ey.com/ey/search/?q=pm"
    get, asked = fake_site({base: SUCCESSFACTORS}, robots="User-agent: *\nDisallow: /ey/search/?q=pm&startrow=")
    assert len(pages.fetch_page(base, {}, get=get)) == 2 and asked == [base]


def test_sitemap_job_pages():
    xml = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://jobs.intuit.com</loc></url>
<url><loc>https://jobs.intuit.com/category/data-jobs/27595/68338/1</loc></url>
<url><loc>https://jobs.intuit.com/job/mountain-view/staff-product-manager-intuit-business-credit-card/27595/103124203456</loc></url>
<url><loc>https://jobs.intuit.com/job/bengaluru/staff-product-manager-time-and-attendance/27595/103170059872</loc></url>
</urlset>"""
    jobs = pages.read_page(xml, "https://jobs.intuit.com/sitemap.xml")
    assert len(jobs) == 2
    assert jobs[0] == {"id": "103124203456", "title": "Staff Product Manager Intuit Business Credit Card",
                       "location": "Mountain View", "posted": None,
                       "url": "https://jobs.intuit.com/job/mountain-view/staff-product-manager-intuit-business-credit-card/27595/103124203456"}


def test_page_url_patterns():
    assert pages.page_url("https://careers.ey.com/ey/search/?q=pm", 3).endswith("startrow=50")
    assert pages.page_url("https://www.revolut.com/careers/", 2) is None


def test_no_jobs_found_is_an_error():
    # An error (not an empty list) so a redesigned page never marks every role Closed
    get, _ = fake_site({"https://example.com/careers": "<p>Join us</p>"})
    with pytest.raises(ValueError, match="no job listings"):
        pages.fetch_page("https://example.com/careers", {}, get=get)


class FakeJSON:
    def __init__(self, data):
        self.data = data
    def raise_for_status(self):
        pass
    def json(self):
        return self.data


def test_atlassian(monkeypatch):
    monkeypatch.setattr(fetchers.requests, "get", lambda url, headers, timeout: FakeJSON([
        {"id": 7, "title": "Principal Product Manager", "locations": [
            "Seattle - United States -   Seattle,  98101 United States", "Remote - APAC - Remote", "San Francisco"],
         "portalJobPost": {"portalUrl": "https://careers-atlassian.icims.com/jobs/7/pm/job"}}]))
    [job] = fetchers.fetch_atlassian("atlassian", {})
    assert job["location"] == "Seattle - United States; Remote - APAC; San Francisco"
    assert job["url"] == "https://careers-atlassian.icims.com/jobs/7/pm/job"


def test_workable(monkeypatch):
    monkeypatch.setattr(fetchers.requests, "get", lambda url, headers, timeout: FakeJSON({"jobs": [
        {"title": "Head of Product", "shortcode": "AB12", "url": "https://apply.workable.com/j/AB12", "telecommuting": True,
         "published_on": "2026-11-02", "city": "New York", "state": "NY", "country": "United States"}]}))
    [job] = fetchers.fetch_workable("youlend-1", {})
    assert job == {"id": "AB12", "title": "Head of Product", "location": "New York, NY, United States; Remote",
                   "url": "https://apply.workable.com/j/AB12", "posted": "2026-11-02"}


def test_dejobs(monkeypatch):
    def get(url, params, headers, timeout):
        assert url == "https://careers.alaskaair.com/jobs/feed/json"
        return FakeJSON([{"guid": "E17", "title": "Director Product Management", "city": "SeaTac", "state_short": "WA",
                          "country": "United States", "url": "https://careers.alaskaair.com/E17", "date_new": "2026-11-11 04:00:05"}])
    monkeypatch.setattr(fetchers.requests, "get", get)
    [job] = fetchers.fetch_dejobs("careers.alaskaair.com", {})
    assert job["location"] == "SeaTac, WA, United States" and job["posted"] == "2026-11-11"


def test_feed_from_new_link_styles():
    assert feed_from_urls(["https://wd5.myworkdaysite.com/recruiting/chewy/External/job/Plantation-FL/x_R1/apply"]) \
        == ("workday", "chewy|wd5|External")
    assert feed_from_urls(["https://apply.workable.com/youlend-1/"]) == ("workable", "youlend-1")
    assert feed_from_urls(["https://apply.workable.com/j/AB12"]) is None
