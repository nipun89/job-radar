"""ats: page -- read the job list straight off a careers page, for companies with no
public jobs feed. board = the page's address (best: one already searched for your roles),
or the site's sitemap.xml when its search pages are closed to automated readers.

Each page becomes ordinary postings, so the filters, "new" and "posting closed" all
work as for any feed. Readers are tried in order and the first that finds jobs wins:

  1. Sitemaps listing job pages (/job/<city>/<title>/...)
  2. Site-specific readers (Revolut's embedded job list)
  3. JobPosting data embedded for search engines (JSON-LD, used by many career sites)
  4. Job links: <a> tags pointing at /job/..., with the title and location next to them

Every address is checked against the site's robots.txt first; a disallowed page is an
error, never fetched. Search results are followed for a few more pages on SuccessFactors
sites. A page with no jobs found raises an error instead of returning nothing, so a
redesign never marks every role Closed.
"""
import html
import json
import re
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests

TIMEOUT = 25
MAX_PAGES = 8
# Say who we are (robots.txt rules are matched against this name too)
AGENT = "job-radar"
HEADERS = {"User-Agent": f"{AGENT}/1.0 (+https://github.com/nipun89/job-radar)",
           "Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}


def text_of(fragment):
    """Visible text of an HTML fragment, whitespace squashed."""
    fragment = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment or "")
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ---------- readers: (html, url) -> list of postings ----------

_SITEMAP_JOB = re.compile(r"<loc>\s*(https?://[^<]*?/job/([^/<]+)/([^/<]+)/(?:[^<]*/)?([\w-]+))/?\s*</loc>")


def _unslug(slug):
    """'staff-product-manager-ai' -> 'Staff Product Manager Ai' (sitemaps only give the slug)."""
    return " ".join(w.capitalize() for w in unquote(slug).replace("_", "-").split("-") if w)


def read_sitemap(page, url):
    """A sitemap.xml whose job pages look like /job/<city>/<title>/.../<id> (Radancy sites
    such as jobs.intuit.com). Title and city come from the address, so they're approximate."""
    if "<urlset" not in page[:2000]:
        return []
    return [{"id": m.group(4), "title": _unslug(m.group(3)), "location": _unslug(m.group(2)),
             "url": m.group(1), "posted": None} for m in _SITEMAP_JOB.finditer(page)]


def read_revolut(page, url):
    """Revolut: every open role is in the page's embedded Next.js data."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return []
    positions = json.loads(m.group(1)).get("props", {}).get("pageProps", {}).get("positions") or []
    return [{"id": p["id"], "title": p.get("text", ""),
             "location": "; ".join(l.get("name", "") for l in p.get("locations") or []),
             "url": f"https://www.revolut.com/careers/position/{_slug(p.get('text', ''))}-{p['id']}/",
             "posted": None} for p in positions if p.get("id")]


def read_jsonld(page, url):
    """schema.org JobPosting blocks, which many career sites embed for search engines."""
    out = []
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph") or [data]
        for d in items:
            if not isinstance(d, dict) or d.get("@type") != "JobPosting":
                continue
            locs = d.get("jobLocation") or []
            locs = locs if isinstance(locs, list) else [locs]
            places = []
            for l in locs:
                a = (l or {}).get("address") or {}
                places.append(", ".join(x for x in (a.get("addressLocality"), a.get("addressRegion"),
                                                       a.get("addressCountry")) if isinstance(x, str) and x))
            link = urljoin(url, d.get("url") or "")
            out.append({"id": str(d.get("identifier", {}).get("value") if isinstance(d.get("identifier"), dict)
                                  else d.get("identifier") or urlsplit(link).path),
                        "title": text_of(d.get("title", "")), "location": "; ".join(p for p in places if p),
                        "url": link, "posted": (d.get("datePosted") or "")[:10] or None})
    return out


_JOB_HREF = re.compile(r'<a\b([^>]*?)href="([^"]*?/(?:job|jobs|position|positions|careers/job)/[^"#]+)"([^>]*)>(.*?)</a>',
                       re.I | re.S)


def read_job_links(page, url):
    """Links to individual job pages. Title: the link's data-title, heading or text.
    Location: the first element whose class mentions "location", between this job
    link and the next one."""
    links = list(_JOB_HREF.finditer(page))
    paths = [urlsplit(urljoin(url, html.unescape(m.group(2)))).path.rstrip("/") for m in links]
    out, seen = [], set()
    for i, m in enumerate(links):
        attrs, inner = m.group(1) + m.group(3), m.group(4)
        link, key = urljoin(url, html.unescape(m.group(2))), paths[i]
        if key in seen:
            continue
        title = re.search(r'data-title="([^"]+)"', attrs)
        heading = re.search(r"<h[1-4][^>]*>(.*?)</h[1-4]>", inner, re.S)
        title = html.unescape(title.group(1)) if title else text_of(heading.group(1) if heading else inner)
        if not (3 <= len(title) <= 150):
            continue
        # Up to the next link to a *different* job (sites often link each job twice)
        nxt = next((links[k].start() for k in range(i + 1, len(links)) if paths[k] != key), m.end() + 3000)
        tail = page[m.start():nxt]
        loc = re.search(r'class="[^"]*location[^"]*"[^>]*>(.*?)</(?:span|div|p|li|td)>', tail, re.I | re.S)
        location = re.sub(r"\s*\+\s*\d+ more.*$", "", text_of(loc.group(1))) if loc else ""
        seen.add(key)
        out.append({"id": key, "title": re.sub(r"\s+", " ", title).strip(),
                    "location": location, "url": link, "posted": None})
    return out


READERS_BY_HOST = {"www.revolut.com": read_revolut}
GENERIC_READERS = [read_sitemap, read_jsonld, read_job_links]


def read_page(page, url):
    """Postings found on one page, by the first reader that finds any."""
    host = urlsplit(url).netloc.lower()
    readers = ([READERS_BY_HOST[host]] if host in READERS_BY_HOST else []) + GENERIC_READERS
    for reader in readers:
        jobs = reader(page, url)
        if jobs:
            return jobs
    return []


# ---------- following search results to the next page ----------

def _with_params(url, **params):
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({k: str(v) for k, v in params.items()})
    return urlunsplit(parts._replace(query=urlencode(query)))


def page_url(url, n):
    """Address of results page n (n >= 2) for sites we know how to page through, else None."""
    parts = urlsplit(url)
    if re.search(r"/search/?$", parts.path) and "q=" in parts.query:  # SuccessFactors (e.g. careers.ey.com)
        return _with_params(url, startrow=(n - 1) * 25)
    return None


# ---------- robots.txt ----------

class Robots:
    """What each site's robots.txt allows, read once per site per run. A site with no
    robots.txt (or one that can't be read) allows everything, as the standard says."""
    def __init__(self, get):
        self.get, self.rules = get, {}

    def allows(self, url):
        parts = urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in self.rules:
            rp = RobotFileParser()
            try:
                r = self.get(root + "/robots.txt", headers=HEADERS, timeout=TIMEOUT)
                rp.parse(r.text.splitlines() if r.ok else [])
            except requests.RequestException:
                rp.parse([])
            self.rules[root] = rp
        return self.rules[root].can_fetch(AGENT, url)   # rules for "job-radar", else for "*"


def fetch_page(board, prefs, get=requests.get, max_pages=MAX_PAGES):
    url = board
    robots = Robots(get)
    if not robots.allows(url):
        raise PermissionError("this site's robots.txt asks automated readers not to open this page; "
                              "use its sitemap.xml or another page, or check it by hand")
    r = get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    jobs = {j["id"]: j for j in read_page(r.text, r.url or url)}
    if not jobs:
        raise ValueError("found no job listings on the page (it may load them with JavaScript, "
                         "or its layout changed)")
    for n in range(2, max_pages + 1):
        nxt = page_url(url, n)
        if not nxt or not robots.allows(nxt):
            break
        r = get(nxt, headers=HEADERS, timeout=TIMEOUT)
        if not r.ok:
            break
        more = [j for j in read_page(r.text, nxt) if j["id"] not in jobs]
        if not more:
            break   # past the last page (many sites repeat the last page or show none)
        jobs.update((j["id"], j) for j in more)
    return list(jobs.values())
