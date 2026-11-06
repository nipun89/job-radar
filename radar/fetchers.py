"""One function per careers system (ATS). Each returns a list of dicts:

    {"id": str, "title": str, "location": str, "url": str, "posted": "YYYY-MM-DD" or None}

These all read public JSON feeds. No HTML scraping, and never LinkedIn.
"""
import re
from datetime import date, datetime, timedelta, timezone

import requests

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


def fetch_ashby(board, prefs):
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}", headers=HEADERS, timeout=TIMEOUT)
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
        })
    return out


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
            "posted": datetime.fromtimestamp(created / 1000, tz=timezone.utc).date().isoformat() if created else None,
        })
    return out


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


def fetch_microsoft(board, prefs, pages=10, pause=1.0):
    """Microsoft's careers site search (apply.careers.microsoft.com). Like Amazon,
    board = a list of search phrases. US only, 10 results a page, up to `pages` pages
    per phrase, one request a second: the site rate-limits fast clients (HTTP 429)."""
    import time
    phrases = board if isinstance(board, list) else [board]
    base = "https://apply.careers.microsoft.com"
    seen = {}
    for phrase in phrases:
        for page in range(pages):
            if seen or page:
                time.sleep(pause)
            r = requests.get(f"{base}/api/pcsx/search", headers=HEADERS, timeout=TIMEOUT, params={
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


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "ashby": fetch_ashby,
    "lever": fetch_lever,
    "workday": fetch_workday,
    "amazon": fetch_amazon,
    "microsoft": fetch_microsoft,
}


def fetch_company(company, prefs):
    """Fetch postings for one config.yaml company entry."""
    fn = FETCHERS.get(company["ats"])
    if fn is None:
        raise ValueError(f"Unknown ats '{company['ats']}'")
    return fn(company["board"], prefs)
