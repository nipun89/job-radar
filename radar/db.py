"""SQLite storage: schema and small helper functions.

Golden rule: the automated run never touches what the user owns:
  the pipeline tables, contacts.how_i_know and contacts.strength.
(jobs.status / notes / status_updated are left over from v1 and no longer used.)
"""
import os
import re
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import yaml

# Project root = the folder that contains radar/
ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_CONFIG_PATH = ROOT / "config.example.yaml"

# Demo mode (python -m radar demo) keeps everything in data/demo/, so it never
# touches your real database or config.
DEMO = os.environ.get("JOB_RADAR_DEMO") == "1"
DATA_DIR = ROOT / "data" / "demo" if DEMO else ROOT / "data"
DB_PATH = DATA_DIR / "jobs.db"
CONFIG_PATH = DATA_DIR / "config.yaml" if DEMO else ROOT / "config.yaml"
LOG_PATH = ROOT / "logs" / ("demo.log" if DEMO else "run.log")

STRENGTHS = ["", "Strong", "Medium", "Weak"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_key        TEXT PRIMARY KEY,   -- "{company}|{source_id}"
    company        TEXT NOT NULL,
    title          TEXT NOT NULL,
    location       TEXT,
    url            TEXT,
    date_posted    TEXT,               -- YYYY-MM-DD or NULL
    date_found     TEXT NOT NULL,      -- YYYY-MM-DD
    fit            TEXT,               -- High / Medium
    fit_reason     TEXT,
    status         TEXT NOT NULL DEFAULT 'New',
    status_updated TEXT,
    notes          TEXT DEFAULT '',
    posting_state  TEXT NOT NULL DEFAULT 'Open',  -- Open / Closed
    closed_on      TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
    url          TEXT PRIMARY KEY,     -- LinkedIn profile URL
    first_name   TEXT,
    last_name    TEXT,
    company      TEXT,
    position     TEXT,
    connected_on TEXT,
    email        TEXT,
    how_i_know   TEXT DEFAULT '',
    strength     TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at            TEXT NOT NULL,
    companies_checked INTEGER,
    postings_scanned  INTEGER,
    new_matches       INTEGER,
    errors            TEXT
);

-- Pipeline: roles you're actively pursuing. Either promoted from the radar
-- (job_key set) or added by hand (job_key NULL). Entirely yours: the daily
-- run never writes here.
CREATE TABLE IF NOT EXISTS pipeline (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key         TEXT UNIQUE,          -- links to jobs.job_key, NULL if added by hand
    company         TEXT NOT NULL,
    title           TEXT NOT NULL,
    url             TEXT DEFAULT '',
    location        TEXT DEFAULT '',
    stage           TEXT NOT NULL DEFAULT 'Interested',
    referral        TEXT DEFAULT '',      -- '', Reached out, Invite sent, Referred
    contact         TEXT DEFAULT '',
    date_interested TEXT,
    date_applied    TEXT,
    notes           TEXT DEFAULT '',
    updated_at      TEXT
);

-- Every stage/referral change, with a date, so progress can be measured.
CREATE TABLE IF NOT EXISTS pipeline_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id INTEGER NOT NULL,
    at          TEXT NOT NULL,            -- YYYY-MM-DD
    field       TEXT NOT NULL,            -- 'stage' or 'referral'
    old_value   TEXT,
    new_value   TEXT
);

-- When each Pipeline company's jobs feed was last looked up (radar/tracking.py).
-- ats NULL = looked, found none; re-checked after 30 days.
CREATE TABLE IF NOT EXISTS company_feeds (
    company    TEXT PRIMARY KEY,
    ats        TEXT,
    board      TEXT,
    checked_on TEXT
);

-- Last result per company, shown on the Companies page.
CREATE TABLE IF NOT EXISTS company_results (
    company  TEXT PRIMARY KEY,
    run_at   TEXT,
    ok       INTEGER,      -- 1 = fetched fine, 0 = error
    scanned  INTEGER,
    matched  INTEGER,
    error    TEXT
);
"""


def connect(path=DB_PATH):
    """Open the database (creating it and its tables if needed)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # timeout: if another process (dashboard, scheduled run) is writing, wait up to 60s instead of failing.
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    # WAL mode lets the dashboard read while a run is writing.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def ensure_config():
    """First run: create config.yaml from config.example.yaml. Returns True if it was created."""
    if CONFIG_PATH.exists():
        return False
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(EXAMPLE_CONFIG_PATH, CONFIG_PATH)
    return True


def load_config(path=None):
    """Read config.yaml. (path is looked up at call time so tests can point elsewhere.)"""
    if path is None:
        ensure_config()
    with open(path or CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def clean_company_name(name):
    """'BlackRock (via Archer)' -> 'BlackRock'."""
    return re.sub(r"\s*\(.*?\)", "", name or "").strip()


def untracked_pipeline_names(cfg, conn):
    """Pipeline companies that config.yaml doesn't cover (by name or match names)."""
    known = set()
    for co in cfg["companies"]:
        known.add(co["name"].lower())
        known.update(m.lower() for m in co.get("match") or [])
    out = []
    for (raw,) in conn.execute("SELECT DISTINCT company FROM pipeline ORDER BY company"):
        name = clean_company_name(raw)
        if name and name.lower() not in known and name.lower() not in {n.lower() for n in out}:
            out.append(name)
    return out


def today():
    return date.today().isoformat()


# ---------- jobs ----------

def insert_job_if_new(conn, job):
    """Insert a job only if its job_key isn't already stored. Returns True if inserted."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO jobs
           (job_key, company, title, location, url, date_posted, date_found, fit, fit_reason)
           VALUES (:job_key, :company, :title, :location, :url, :date_posted, :date_found, :fit, :fit_reason)""",
        job,
    )
    return cur.rowcount == 1


def existing_job_keys(conn, company):
    rows = conn.execute("SELECT job_key FROM jobs WHERE company = ?", (company,))
    return {r["job_key"] for r in rows}


def refresh_posting_state(conn, company, seen_keys):
    """For a company fetched successfully: mark missing jobs Closed, seen jobs Open.

    seen_keys = every job_key present in the feed this run (filtered or not).
    """
    for key in existing_job_keys(conn, company):
        if key in seen_keys:
            conn.execute(
                "UPDATE jobs SET posting_state='Open', closed_on=NULL WHERE job_key=? AND posting_state<>'Open'",
                (key,),
            )
        else:
            # closed_on is set once: only when it flips from Open to Closed
            conn.execute(
                "UPDATE jobs SET posting_state='Closed', closed_on=? WHERE job_key=? AND posting_state<>'Closed'",
                (today(), key),
            )


# ---------- contacts ----------

def upsert_contact(conn, c):
    """Insert or update a contact, never touching how_i_know or strength."""
    conn.execute(
        """INSERT INTO contacts (url, first_name, last_name, company, position, connected_on, email)
           VALUES (:url, :first_name, :last_name, :company, :position, :connected_on, :email)
           ON CONFLICT(url) DO UPDATE SET
             first_name=excluded.first_name, last_name=excluded.last_name,
             company=excluded.company, position=excluded.position,
             connected_on=excluded.connected_on, email=excluded.email""",
        c,
    )


def update_contact(conn, url, how_i_know, strength):
    conn.execute(
        "UPDATE contacts SET how_i_know=?, strength=? WHERE url=?",
        (how_i_know, strength, url),
    )
    conn.commit()
