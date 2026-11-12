"""One function per careers system (ATS). Each returns a list of dicts:

    {"id": str, "title": str, "location": str, "url": str, "posted": "YYYY-MM-DD" or None,
     "pay_max": int or None}   # only where the feed itself includes pay (Ashby, Lever)

For the rest, lookup_pay() reads one posting's page. The daily run only calls it for
postings that pass every filter except seniority, and caches the answer.

These all read public JSON feeds, except `page` (radar/pages.py), which reads the job
list off a careers page for companies with no feed. Never LinkedIn.
"""
import html
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests

from radar.pages import fetch_page

TIMEOUT = 20
HEADERS = {"User-Agent": "job-radar/1.0", "Accept": "application/json"}


def _iso_to_date(value):
    """'2026-10-28T14:03:00-04:00' -> '2026-10-28'. Returns None if missing/unparseable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return str(value)[:10] or None


def fetch_greenhouse(board, prefs):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        out.append({
            "id": str(j["id"]),
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "posted": _iso_to_date(j.get("first_published") or j.get("updated_at")),
        })
    return out


# One amount: "$190,800", "$190.8K", "$ 305,000"
_AMOUNT = r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?\s?[Kk]\b)"
# A pay range: "$184,000 - $230,000", "$190.8K – $262.8K", "$139,900 to USD $274,800"
_RANGE = re.compile(_AMOUNT + r"\s*(?:-|–|—|to)\s*(?:USD\s*)?" + _AMOUNT)


def _dollars(raw):
    raw = raw.replace(",", "").replace(" ", "")
    return float(raw[:-1]) * 1000 if raw[-1] in "Kk" else float(raw)


def pay_from_text(text):
    """Top of the highest annual pay range in a job description, else None.
    Only ranges count, so a stray "$400K in savings" in the pitch is ignored,
    as are hourly ranges ($30K-$2M only)."""
    plain = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(text or "")))
    tops = [int(_dollars(m.group(2))) for m in _RANGE.finditer(plain)]
    tops = [t for t in tops if 30_000 <= t <= 2_000_000]
    return max(tops) if tops else None


def fetch_ashby(board, prefs):
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}", params={"includeCompensation": "true"},
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""]
        locs += [s.get("location", "") for s in (j.get("secondaryLocations") or [])]
        if j.get("isRemote") and not any("remote" in l.lower() for l in locs):
            locs.append("Remote")
        out.append({
            "id": str(j["id"]),
            "title": j.get("title", ""),
            "location": "; ".join(l for l in locs if l),
            "url": j.get("jobUrl", ""),
            "posted": _iso_to_date(j.get("publishedAt")),
            "pay_max": _ashby_pay(j),
        })
    return out


def _ashby_pay(job):
    comps = (job.get("compensation") or {}).get("summaryComponents") or []
    tops = [c.get("maxValue") for c in comps if c.get("compensationType") == "Salary"
            and c.get("interval") == "1 YEAR" and (c.get("currencyCode") or "USD") == "USD" and c.get("maxValue")]
    return int(max(tops)) if tops else None


def fetch_lever(board, prefs):
    r = requests.get(f"https://api.lever.co/v0/postings/{board}?mode=json", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json():
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location") or ""]
        created = j.get("createdAt")
        out.append({
            "id": str(j["id"]),
            "title": j.get("text", ""),
            "location": "; ".join(l for l in locs if l),
            "url": j.get("hostedUrl", ""),
            "pay_max": _lever_pay(j.get("salaryRange")),
            "posted": datetime.fromtimestamp(created / 1000, tz=timezone.utc).date().isoformat() if created else None,
        })
    return out


def _lever_pay(salary):
    if salary and "year" in (salary.get("interval") or "") and (salary.get("currency") or "USD") == "USD" and salary.get("max"):
        return int(salary["max"])
    return None


def _workday_posted(text):
    """Workday gives 'Posted Today', 'Posted Yesterday', 'Posted 3 Days Ago', 'Posted 30+ Days Ago'."""
    t = (text or "").lower()
    if "today" in t:
        return date.today().isoformat()
    if "yesterday" in t:
        return (date.today() - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*days?", t)
    if m:
        return (date.today() - timedelta(days=int(m.group(1)))).isoformat()
    return None


def _workday_locations(base, tenant, site, external_path):
    """Fetch one Workday posting's detail and return 'Primary; Additional; ...' (or None)."""
    try:
        r = requests.get(f"{base}/wday/cxs/{tenant}/{site}{external_path}", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        info = r.json().get("jobPostingInfo", {})
        locs = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
        return "; ".join(l for l in locs if l) or None
    except Exception:
        return None  # keep the "N Locations" text rather than fail the whole company


def fetch_workday(board, prefs):
    tenant, wd, site = board.split("|")
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    out = []
    for offset in range(0, 200, 20):
        body = {"appliedFacets": {}, "limit": 20, "offset": offset,
                "searchText": prefs.get("workday_search_text", "")}
        r = requests.post(api, json=body, headers={**HEADERS, "Content-Type": "application/json"}, timeout=TIMEOUT)
        r.raise_for_status()
        postings = r.json().get("jobPostings", [])
        for j in postings:
            bullets = j.get("bulletFields") or []
            location = j.get("locationsText", "")
            # Multi-location roles just say "2 Locations". For PM-looking titles,
            # look up the real list so the US/non-US filter can work.
            title_l = j.get("title", "").lower()
            if re.fullmatch(r"\d+ Locations", location.strip()) and any(
                k in title_l for k in prefs.get("role_keywords", [])
            ):
                location = _workday_locations(base, tenant, site, j.get("externalPath", "")) or location
            out.append({
                "id": str(bullets[0] if bullets else j.get("externalPath")),
                "title": j.get("title", ""),
                "location": location,
                "url": f"{base}/{site}{j.get('externalPath', '')}",
                "posted": _workday_posted(j.get("postedOn")),
            })
        if len(postings) < 20:
            break
    return out


def _amazon_date(text):
    """'April  8, 2026' -> '2026-04-08'."""
    if not text:
        return None
    try:
        return datetime.strptime(re.sub(r"\s+", " ", text.strip()), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def fetch_amazon(board, prefs):
    phrases = board if isinstance(board, list) else [board]
    seen = {}
    for phrase in phrases:
        r = requests.get(
            "https://www.amazon.jobs/en/search.json",
            params={"result_limit": 100, "sort": "recent", "base_query": phrase},
            headers=HEADERS, timeout=TIMEOUT,
        )
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            jid = str(j.get("id_icims") or j.get("id"))
            if jid in seen:
                continue
            seen[jid] = {
                "id": jid,
                "title": j.get("title", ""),
                "location": j.get("normalized_location") or j.get("location", ""),
                "url": "https://www.amazon.jobs" + j.get("job_path", ""),
                "posted": _amazon_date(j.get("posted_date")),
            }
    return list(seen.values())


def _get_patiently(url, params, waits=(15, 30, 60, 90), sleep=time.sleep):
    """GET, and when the site says "too many requests" (HTTP 429), wait and try again."""
    for wait in (*waits, None):
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, params=params)
        if r.status_code != 429 or wait is None:
            return r
        sleep(wait)


def fetch_microsoft(board, prefs, pages=5, pause=3.0, sleep=time.sleep):
    """Microsoft's careers site search (apply.careers.microsoft.com). Like Amazon,
    board = a list of search phrases. US only, 10 results a page, up to `pages` pages
    per phrase. The site rate-limits hard (HTTP 429), so requests are spaced out and
    retried; if it still refuses, the company shows an error and nothing is closed."""
    phrases = board if isinstance(board, list) else [board]
    base = "https://apply.careers.microsoft.com"
    seen = {}
    for phrase in phrases:
        for page in range(pages):
            if seen or page:
                sleep(pause)
            r = _get_patiently(f"{base}/api/pcsx/search", sleep=sleep, params={
                "domain": "microsoft.com", "query": phrase, "location": "United States", "start": page * 10})
            r.raise_for_status()
            positions = (r.json().get("data") or {}).get("positions") or []
            for j in positions:
                jid = str(j.get("id"))
                ts = j.get("postedTs") or j.get("creationTs")
                seen.setdefault(jid, {
                    "id": jid,
                    "title": j.get("name", ""),
                    "location": "; ".join(j.get("standardizedLocations") or j.get("locations") or []),
                    "url": base + (j.get("positionUrl") or f"/careers/job/{jid}"),
                    "posted": datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat() if ts else None,
                })
            if len(positions) < 10:
                break
    return list(seen.values())


def fetch_atlassian(board, prefs):
    """Atlassian's own careers site, which serves its whole job list as JSON. board is unused."""
    r = requests.get("https://www.atlassian.com/endpoint/careers/listings", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json():
        # "Seattle - United States -   Seattle,  98101 United States" -> "Seattle - United States"
        locs = [re.sub(r"\s+-\s+[^-]*$", "", l).strip() if l.count(" - ") > 1 else l.strip()
                for l in j.get("locations") or []]
        out.append({
            "id": str(j["id"]),
            "title": j.get("title", ""),
            "location": "; ".join(l for l in locs if l),
            "url": (j.get("portalJobPost") or {}).get("portalUrl") or f"https://www.atlassian.com/company/careers/details/{j['id']}",
            "posted": None,
        })
    return out


def fetch_workable(board, prefs):
    """Workable job boards (apply.workable.com/<board>)."""
    r = requests.get(f"https://apply.workable.com/api/v1/widget/accounts/{board}", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        places = [", ".join(x for x in (l.get("city"), l.get("region"), l.get("country")) if x)
                  for l in j.get("locations") or []] or [", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)]
        if j.get("telecommuting"):
            places.append("Remote")
        out.append({
            "id": j.get("shortcode") or j.get("url"),
            "title": j.get("title", ""),
            "location": "; ".join(p for p in dict.fromkeys(places) if p),
            "url": j.get("url") or f"https://apply.workable.com/{board}/j/{j.get('shortcode')}",
            "posted": (j.get("published_on") or "")[:10] or None,
        })
    return out


def fetch_dejobs(board, prefs):
    """Career sites run by DirectEmployers (board = the site's host, e.g. careers.alaskaair.com),
    which publish every job as a JSON feed."""
    r = requests.get(f"https://{board}/jobs/feed/json", params={"num_items": 1000},
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return [{
        "id": j.get("guid") or j.get("reqid") or j["url"],
        "title": j.get("title", ""),
        "location": ", ".join(x for x in (j.get("city"), j.get("state_short") or j.get("state"), j.get("country")) if x)
                    or j.get("location", ""),
        "url": j.get("url", ""),
        "posted": (j.get("date_new") or "")[:10] or None,
    } for j in r.json()]


def _pay_greenhouse(company, posting):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{company['board']}/jobs/{posting['id']}",
                     params={"pay_transparency": "true"}, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    tops = [x["max_cents"] / 100 for x in d.get("pay_input_ranges") or []
            if x.get("max_cents") and (x.get("currency_type") or "USD") == "USD"]
    tops = [t for t in tops if t >= 30_000]   # skip hourly ranges
    return int(max(tops)) if tops else pay_from_text(d.get("content"))


def _pay_workday(company, posting):
    tenant, wd, site = company["board"].split("|")
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    path = posting["url"].split(f"{base}/{site}", 1)[-1]
    r = requests.get(f"{base}/wday/cxs/{tenant}/{site}{path}", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return pay_from_text(r.json().get("jobPostingInfo", {}).get("jobDescription"))


# Not Microsoft: its titles always state a level (Product Manager II, Senior, Principal),
# and its site rate-limits too hard for per-posting lookups.
PAY_LOOKUPS = {"greenhouse": _pay_greenhouse, "workday": _pay_workday}


def lookup_pay(company, posting):
    """Highest annual pay on one posting's page, or None (not stated, or this ATS isn't supported)."""
    fn = PAY_LOOKUPS.get(company.get("ats"))
    return fn(company, posting) if fn else None


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "ashby": fetch_ashby,
    "lever": fetch_lever,
    "workday": fetch_workday,
    "amazon": fetch_amazon,
    "microsoft": fetch_microsoft,
    "atlassian": fetch_atlassian,
    "workable": fetch_workable,
    "dejobs": fetch_dejobs,
    "page": fetch_page,
}


def fetch_company(company, prefs):
    """Fetch postings for one config.yaml company entry."""
    fn = FETCHERS.get(company["ats"])
    if fn is None:
        raise ValueError(f"Unknown ats '{company['ats']}'")
    return fn(company["board"], prefs)
