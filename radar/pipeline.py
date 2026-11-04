"""Pipeline: the roles you're actively pursuing, and the history of how they moved.

A role gets here two ways:
  - "Interested" on the Radar page  -> add_from_job()
  - "Add a role I found elsewhere"   -> add_manual()
Every stage or referral change is written to pipeline_events with a date, so we
can show progress ("3 applications this week") without guessing.
"""
from datetime import date, timedelta

from radar import db

STAGES_ACTIVE = ["Interested", "Applied", "Interviewing", "Offer"]
STAGES_CLOSED = ["Rejected", "Withdrawn", "Closed"]
STAGES = STAGES_ACTIVE + STAGES_CLOSED
REFERRALS = ["", "Reached out", "Invite sent", "Referred"]

# Moving into any of these stages stamps date_applied if it's still blank
_APPLIED_OR_LATER = {"Applied", "Interviewing", "Offer"}


def _log(conn, pipeline_id, field, old, new, at=None):
    conn.execute(
        "INSERT INTO pipeline_events (pipeline_id, at, field, old_value, new_value) VALUES (?,?,?,?,?)",
        (pipeline_id, at or db.today(), field, old, new),
    )


def add_from_job(conn, job_key):
    """Promote a radar job. Returns the pipeline id (existing id if it's already there)."""
    existing = conn.execute("SELECT id FROM pipeline WHERE job_key=?", (job_key,)).fetchone()
    if existing:
        return existing["id"]
    job = conn.execute("SELECT * FROM jobs WHERE job_key=?", (job_key,)).fetchone()
    if job is None:
        raise KeyError(job_key)
    cur = conn.execute(
        """INSERT INTO pipeline (job_key, company, title, url, location, stage, date_interested, updated_at)
           VALUES (?,?,?,?,?, 'Interested', ?, ?)""",
        (job_key, job["company"], job["title"], job["url"] or "", job["location"] or "", db.today(), db.today()),
    )
    _log(conn, cur.lastrowid, "stage", "", "Interested")
    conn.commit()
    return cur.lastrowid


def add_manual(conn, company, title, url="", location="", stage="Interested", referral="",
               contact="", date_applied=None, notes="", job_key=None, at=None):
    """Add a role found outside the radar (or import one). `at` backdates the events."""
    at = at or db.today()
    if stage in _APPLIED_OR_LATER and not date_applied:
        date_applied = at
    cur = conn.execute(
        """INSERT INTO pipeline (job_key, company, title, url, location, stage, referral, contact,
                                 date_interested, date_applied, notes, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (job_key, company.strip(), title.strip(), url.strip(), location.strip(), stage, referral,
         contact.strip(), at, date_applied, notes.strip(), db.today()),
    )
    pid = cur.lastrowid
    _log(conn, pid, "stage", "", stage, at)
    if referral:
        _log(conn, pid, "referral", "", referral, at)
    conn.commit()
    return pid


def update_item(conn, pipeline_id, stage, referral, contact, date_applied, notes):
    old = conn.execute("SELECT * FROM pipeline WHERE id=?", (pipeline_id,)).fetchone()
    if old is None:
        raise KeyError(pipeline_id)
    if stage != old["stage"]:
        _log(conn, pipeline_id, "stage", old["stage"], stage)
        if stage in _APPLIED_OR_LATER and not date_applied:
            date_applied = db.today()
    if referral != (old["referral"] or ""):
        _log(conn, pipeline_id, "referral", old["referral"], referral)
    conn.execute(
        """UPDATE pipeline SET stage=?, referral=?, contact=?, date_applied=?, notes=?, updated_at=?
           WHERE id=?""",
        (stage, referral, contact, date_applied or None, notes, db.today(), pipeline_id),
    )
    conn.commit()


def remove_item(conn, pipeline_id):
    """Delete a pipeline row. If it came from the radar, it reappears there."""
    conn.execute("DELETE FROM pipeline_events WHERE pipeline_id=?", (pipeline_id,))
    conn.execute("DELETE FROM pipeline WHERE id=?", (pipeline_id,))
    conn.commit()


def list_items(conn):
    """All pipeline rows, with the radar's fit and open/closed state where linked."""
    rows = conn.execute(
        """SELECT p.*, j.fit, j.fit_reason, j.posting_state, j.date_posted
           FROM pipeline p LEFT JOIN jobs j ON p.job_key = j.job_key
           ORDER BY COALESCE(p.date_applied, p.date_interested) DESC, p.id DESC"""
    )
    return [dict(r) for r in rows]


# Interested roles are split by where the outreach stands
WAITING = {"Reached out", "Invite sent"}


def group_items(items):
    """Sections shown on the Pipeline page, in order. `always` = show even when empty."""
    interested = [i for i in items if i["stage"] == "Interested"]
    groups = [
        {"id": "need-outreach", "title": "Interested: need to reach out", "always": True,
         "hint": "No contact approached yet. Pick someone and set Referral to Reached out.",
         "items": [i for i in interested if not i["referral"]]},
        {"id": "waiting", "title": "Interested: waiting on contact", "always": True,
         "hint": "You've reached out or sent an invite. Waiting to hear back.",
         "items": [i for i in interested if i["referral"] in WAITING]},
        {"id": "referred", "title": "Interested: referred, ready to apply", "always": False,
         "hint": "Your contact has referred you. Time to apply.",
         "items": [i for i in interested if i["referral"] == "Referred"]},
    ]
    for stage in STAGES_ACTIVE[1:]:
        groups.append({"id": f"stage-{stage}", "title": stage, "always": True, "hint": "",
                       "items": [i for i in items if i["stage"] == stage]})
    return [g for g in groups if g["items"] or g["always"]]


def stats(conn, days=7):
    """Counts per stage, plus what moved in the last `days` days."""
    by_stage = dict(conn.execute("SELECT stage, COUNT(*) FROM pipeline GROUP BY stage").fetchall())
    since = (date.today() - timedelta(days=days)).isoformat()
    moved = dict(conn.execute(
        """SELECT new_value, COUNT(DISTINCT pipeline_id) FROM pipeline_events
           WHERE field='stage' AND at >= ? GROUP BY new_value""", (since,)).fetchall())
    outreach = conn.execute(
        """SELECT COUNT(DISTINCT pipeline_id) FROM pipeline_events
           WHERE field='referral' AND new_value <> '' AND at >= ?""", (since,)).fetchone()[0]
    interested = conn.execute("SELECT referral FROM pipeline WHERE stage='Interested'").fetchall()
    return {
        "by_stage": {s: by_stage.get(s, 0) for s in STAGES},
        "need_outreach": sum(1 for r in interested if not r["referral"]),
        "waiting": sum(1 for r in interested if r["referral"] in WAITING),
        "active": sum(by_stage.get(s, 0) for s in STAGES_ACTIVE),
        "week": {
            "interested": moved.get("Interested", 0),
            "outreach": outreach,
            "applied": moved.get("Applied", 0),
            "interviewing": moved.get("Interviewing", 0),
        },
    }
