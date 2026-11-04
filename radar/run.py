"""The daily job: fetch -> filter -> insert new -> refresh open/closed -> log.

Run it with:  python -m radar.run
Safe to run many times a day: existing jobs are never duplicated or overwritten.
"""
from datetime import datetime

from radar import db
from radar.fetchers import fetch_company
from radar.filters import evaluate, too_old
from radar.tracking import refresh_feeds


def run_check(config=None, conn=None):
    """Run one full check. Returns a summary dict (also used by the dashboard button)."""
    own_conn = conn is None
    conn = conn or db.connect()
    if config is None:
        # Companies you add to the Pipeline get tracked too: look up their feeds first
        try:
            refresh_feeds(conn)
        except Exception as e:  # never let this stop the daily run
            print(f"Feed lookup skipped: {e}")
        config = db.load_config()
    prefs = config["preferences"]
    run_at = datetime.now().isoformat(timespec="seconds")
    today = db.today()

    per_company = {}   # name -> {"ok", "scanned", "matched", "new", "error"}
    errors = []
    total_scanned = 0
    total_new = 0

    for company in config["companies"]:
        name = company["name"]
        if company.get("ats") == "manual" or company.get("paused"):
            continue  # manual = check by hand; paused = not looking there right now
        try:
            postings = fetch_company(company, prefs)
        except Exception as e:  # one broken feed must never stop the run
            msg = f"{type(e).__name__}: {e}"[:300]
            errors.append(f"{name}: {msg}")
            per_company[name] = {"ok": False, "scanned": 0, "matched": 0, "new": 0, "error": msg}
            conn.execute(
                "INSERT OR REPLACE INTO company_results VALUES (?,?,?,?,?,?)",
                (name, run_at, 0, 0, 0, msg),
            )
            continue

        already = db.existing_job_keys(conn, name)
        seen_keys = set()
        matched = new = 0
        for p in postings:
            key = f"{name}|{p['id']}"
            seen_keys.add(key)
            keep, fit, reason, _ = evaluate(p["title"], p["location"], prefs,
                                            skip_seniority=company.get("skip_seniority", False))
            if not keep:
                continue
            matched += 1
            if key in already:
                continue  # never touch an existing row's status / notes
            if too_old(p["posted"], prefs):
                continue  # age limit only applies when first discovered
            inserted = db.insert_job_if_new(conn, {
                "job_key": key, "company": name, "title": p["title"].strip(),
                "location": p["location"], "url": p["url"], "date_posted": p["posted"],
                "date_found": today, "fit": fit, "fit_reason": reason,
            })
            new += inserted

        # Only a successful fetch is allowed to mark jobs Closed
        db.refresh_posting_state(conn, name, seen_keys)
        conn.execute(
            "INSERT OR REPLACE INTO company_results VALUES (?,?,?,?,?,?)",
            (name, run_at, 1, len(postings), matched, None),
        )
        per_company[name] = {"ok": True, "scanned": len(postings), "matched": matched, "new": new, "error": None}
        total_scanned += len(postings)
        total_new += new

    checked = len(per_company)
    conn.execute(
        "INSERT INTO runs (run_at, companies_checked, postings_scanned, new_matches, errors) VALUES (?,?,?,?,?)",
        (run_at, checked, total_scanned, total_new, "; ".join(errors) or None),
    )
    conn.commit()
    if own_conn:
        conn.close()

    summary = {"run_at": run_at, "companies": per_company, "checked": checked,
               "scanned": total_scanned, "new": total_new, "errors": errors}
    _append_log(summary)
    return summary


def _append_log(s):
    db.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = (f"{s['run_at']}  checked={s['checked']} scanned={s['scanned']} new={s['new']} "
            f"errors={len(s['errors'])}" + (f"  [{' | '.join(s['errors'])}]" if s["errors"] else ""))
    with open(db.LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def format_summary(s):
    lines = [f"Run at {s['run_at']}: {s['new']} new role(s) from {s['scanned']} postings across {s['checked']} companies."]
    for name, r in s["companies"].items():
        if r["ok"]:
            lines.append(f"  OK    {name:<12} scanned {r['scanned']:>4}  matching {r['matched']:>3}  new {r['new']:>3}")
        else:
            lines.append(f"  ERROR {name:<12} {r['error']}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_summary(run_check()))
