"""Track every company you add to the Pipeline, by adding it to config.yaml.

For each Pipeline company that config.yaml doesn't cover, look for a public jobs
feed: first in the job links you saved, then by trying likely board names on
Greenhouse, Ashby and Lever. Then append one line to the end of config.yaml,
marked `auto: true`. The rest of the file (your comments, layout) is never
rewritten. A company with no feed is added as `ats: manual` and re-checked
every 30 days; if a feed turns up, its line is updated.
Edit or pause auto-added lines like any other line.
"""
import json
import re
from urllib.parse import quote_plus

import yaml
from datetime import date, timedelta

import requests

from radar import db

TIMEOUT = 15
HEADERS = {"User-Agent": "job-radar/1.0"}
RECHECK_DAYS = 30


def _squash(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def slug_candidates(name):
    """'Maven Clinic' -> ['mavenclinic', 'maven-clinic']. Whole name only, so we
    don't grab some other company that shares a first word."""
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    return list(dict.fromkeys(["".join(words), "-".join(words)]))


def _get_json(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        return r.json() if r.ok else None
    except (requests.RequestException, ValueError):
        return None


# Feed addresses that can be read straight from a job link or a board's own page
_URL_PATTERNS = [
    ("workday", re.compile(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?(?!wday/)([\w-]+)"),
     lambda m: f"{m.group(1)}|{m.group(2)}|{m.group(3)}"),
    # Same Workday feed, other address style: wd5.myworkdaysite.com/recruiting/<tenant>/<site>
    ("workday", re.compile(r"https?://(wd\d+)\.myworkdaysite\.com/(?:[a-z]{2}-[A-Z]{2}/)?recruiting/([\w-]+)/([\w-]+)"),
     lambda m: f"{m.group(2)}|{m.group(1)}|{m.group(3)}"),
    ("workable", re.compile(r"apply\.workable\.com/(?!api/|j/)([\w-]+)"), lambda m: m.group(1)),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([\w-]+)"), lambda m: m.group(1)),
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.(?:eu\.)?greenhouse\.io/(?!embed)([\w-]+)"), lambda m: m.group(1)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w-]+)"), lambda m: m.group(1)),
    ("lever", re.compile(r"jobs\.lever\.co/([\w-]+)"), lambda m: m.group(1)),
]


def feed_from_urls(urls):
    for url in urls:
        for ats, pattern, board in _URL_PATTERNS:
            m = pattern.search(url or "")
            if m:
                return ats, board(m)
    return None


def detect_feed(name, urls=()):
    """Return (ats, board) for the first feed found, else None.
    Checks your saved job links first, then guesses board names."""
    found = feed_from_urls(urls)
    if found:
        return found
    for slug in slug_candidates(name):
        # Greenhouse tells us the board's company name, so we can confirm it's the right one
        info = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
        if info and _squash(info.get("name")).startswith(_squash(name)[:6]):
            return "greenhouse", slug
        jobs = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        if jobs and jobs.get("jobs"):
            return "ashby", slug
        jobs = _get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        if isinstance(jobs, list) and jobs:
            return "lever", slug
    return None


def feed_from_page(url):
    """Open a careers page and look for a link or embed pointing at a known ATS
    (many company sites embed Greenhouse or link out to Workday)."""
    if not (url or "").startswith("http"):
        return None
    try:
        r = requests.get(url, headers={**HEADERS, "User-Agent": "Mozilla/5.0 (job-radar)"}, timeout=TIMEOUT)
    except requests.RequestException:
        return None
    return feed_from_urls([r.url, r.text]) if r.ok else feed_from_urls([r.url])


def find_feed(name, link=""):
    """Everything we know how to try, for the Companies page: the link itself,
    then the page it points to, then board-name guesses from the company name."""
    return feed_from_urls([link]) or feed_from_page(link) or detect_feed(name)


def verify_feed(feed, prefs, skip_seniority=False):
    """Fetch the feed once. Returns {ok, scanned, matched, sample, error}."""
    from radar.fetchers import fetch_company
    from radar.filters import evaluate
    try:
        postings = fetch_company({"name": "?", "ats": feed[0], "board": feed[1]}, prefs)
    except Exception as e:
        return {"ok": False, "scanned": 0, "matched": 0, "sample": [], "error": f"{type(e).__name__}: {e}"[:300]}
    hits = [p["title"] for p in postings
            if evaluate(p["title"], p["location"], prefs, skip_seniority, p.get("pay_max"))[0]]
    # No matches: show role-looking titles instead, so you can see why (e.g. no seniority word)
    near = [p["title"] for p in postings
            if any(k in p["title"].lower() for k in prefs.get("role_keywords", []))]
    return {"ok": True, "scanned": len(postings), "matched": len(hits),
            "sample": (hits or near or [p["title"] for p in postings])[:5], "error": None}


AUTO_HEADER = "  # ---- Added automatically from your Pipeline (edit or pause like any other line) ----"


def _q(text):
    """A YAML-safe double-quoted string (JSON strings are valid YAML)."""
    return json.dumps(text, ensure_ascii=False)


def config_line(name, feed):
    if feed:
        ats, board = feed
        return f"  - {{name: {_q(name)}, ats: {ats}, board: {_q(board)}, match: [{_q(name)}], auto: true}}"
    url = "https://www.google.com/search?q=" + quote_plus(f"{name} careers product manager")
    return f"  - {{name: {_q(name)}, ats: manual, url: {_q(url)}, match: [{_q(name)}], auto: true}}"


def company_line(name, ats, board="", url="", match=(), skip_seniority=False):
    """One config.yaml line for a company added on the Companies page."""
    parts = [f"name: {_q(name)}", f"ats: {ats}"]
    if ats == "manual":
        parts.append(f"url: {_q(url or 'https://www.google.com/search?q=' + quote_plus(name + ' careers'))}")
    else:
        parts.append(f"board: {_q(board)}")
    parts.append("match: [" + ", ".join(_q(m) for m in (match or [name])) + "]")
    if skip_seniority:
        parts.append("skip_seniority: true")
    return "  - {" + ", ".join(parts) + "}"


def add_company(line):
    """Add a line to the end of your own company list: above the auto-added block
    if there is one, so that block stays at the bottom. Nothing else is rewritten."""
    text = db.CONFIG_PATH.read_text(encoding="utf-8").rstrip(NL)
    i = text.find(AUTO_HEADER)
    if i == -1:
        _write_config(text + NL + line + NL)
    else:
        _write_config(text[:i].rstrip(NL) + NL + line + NL + NL + text[i:] + NL)


def _company_line_index(lines, name):
    """Index of the one-line `- {name: ..., ...}` entry for `name`, else None."""
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("- {"):
            continue
        try:
            entry = yaml.safe_load(line.strip())[0]
        except (yaml.YAMLError, IndexError, TypeError, KeyError):
            continue
        if isinstance(entry, dict) and str(entry.get("name", "")).lower() == name.lower():
            return i
    return None


def _edit_company(name, change):
    """Apply change(line) -> new line or None (= delete) to a company's line.
    Returns False if its entry isn't on one line (then it must be edited by hand)."""
    lines = db.CONFIG_PATH.read_text(encoding="utf-8").split(NL)
    i = _company_line_index(lines, name)
    if i is None:
        return False
    new = change(lines[i])
    if new is None:
        del lines[i]
    else:
        lines[i] = new
    _write_config(NL.join(lines))
    return True


_PAUSED = re.compile(r",\s*paused:\s*(true|false|yes|no)\b", re.IGNORECASE)


def set_paused(name, paused):
    def change(line):
        line = _PAUSED.sub("", line)
        if paused:
            end = line.rindex("}")   # before any trailing comment
            line = line[:end].rstrip() + ", paused: true" + line[end:]
        return line
    return _edit_company(name, change)


def remove_company(name):
    return _edit_company(name, lambda line: None)


def _write_config(new_text):
    """Write config.yaml only if the result still parses; otherwise leave it alone."""
    data = yaml.safe_load(new_text)
    if not isinstance(data, dict) or not isinstance(data.get("companies"), list):
        raise ValueError("config.yaml would no longer be valid; not writing it")
    db.CONFIG_PATH.write_text(new_text, encoding="utf-8")


NL = "\n"


def _append_lines(lines):
    text = db.CONFIG_PATH.read_text(encoding="utf-8").rstrip(NL)
    if AUTO_HEADER not in text:
        text += NL + NL + AUTO_HEADER
    _write_config(text + NL + NL.join(lines) + NL)


def _replace_line(name, new_line):
    """Swap the auto-added line for `name` (used when a feed is found later)."""
    out, done = [], False
    for line in db.CONFIG_PATH.read_text(encoding="utf-8").split(NL):
        if not done and "auto: true" in line and f"name: {_q(name)}," in line:
            out.append(new_line)
            done = True
        else:
            out.append(line)
    if done:
        _write_config(NL.join(out))


def _cache(conn, name, feed):
    conn.execute(
        "INSERT OR REPLACE INTO company_feeds (company, ats, board, checked_on) VALUES (?,?,?,?)",
        (name, feed[0] if feed else None, feed[1] if feed else None, db.today()),
    )


def _lookup(conn, name):
    urls = [r[0] for r in conn.execute("SELECT url FROM pipeline WHERE company LIKE ?", (name + "%",))]
    return detect_feed(name, urls)


def refresh_feeds(conn):
    """Add untracked Pipeline companies to config.yaml, and re-check auto-added
    no-feed companies every 30 days. Returns {company: feed or None} for what changed."""
    cfg = db.load_config()
    cached = {r["company"].lower(): dict(r) for r in conn.execute("SELECT * FROM company_feeds")}
    stale_before = (date.today() - timedelta(days=RECHECK_DAYS)).isoformat()
    changed, new_lines = {}, []

    # 1. Pipeline companies config.yaml doesn't cover yet -> append a line each
    for name in db.untracked_pipeline_names(cfg, conn):
        c = cached.get(name.lower())
        if c and (c["ats"] or c["checked_on"] >= stale_before):
            feed = (c["ats"], c["board"]) if c["ats"] else None   # already looked recently
        else:
            feed = _lookup(conn, name)
            _cache(conn, name, feed)
        new_lines.append(config_line(name, feed))
        changed[name] = feed
    if new_lines:
        _append_lines(new_lines)

    # 2. Auto-added companies with no feed: look again every 30 days
    for co in cfg["companies"]:
        if not (co.get("auto") and co.get("ats") == "manual"):
            continue
        c = cached.get(co["name"].lower())
        if c and c["checked_on"] >= stale_before:
            continue
        feed = _lookup(conn, co["name"])
        _cache(conn, co["name"], feed)
        if feed:
            _replace_line(co["name"], config_line(co["name"], feed))
            changed[co["name"]] = feed

    conn.commit()
    return changed
