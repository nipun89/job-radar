"""Job Radar web app (Flask).

Start:  python -m radar serve   (Windows: double-click "Job Radar.bat")
Then:   http://localhost:5050   (change the port with the JOB_RADAR_PORT environment variable)

How it works: each page is a plain HTML file in templates/. Every button is an
ordinary HTML <form>. Clicking it POSTs to one of the routes below, which updates
SQLite and redirects back to the page (web 1.0 style; no JavaScript framework).
"""
import os
import threading
import webbrowser
from datetime import date, timedelta

from flask import Flask, flash, g, redirect, render_template, request, url_for

from radar import db, pipeline
from radar.contacts import CSV_PATH, contacts_by_company, contacts_for_name, import_connections
from radar.run import format_summary, run_check
from radar.tracking import refresh_feeds

PORT = int(os.environ.get("JOB_RADAR_PORT", "5050"))
app = Flask(__name__)
app.secret_key = "job-radar-local-only"  # only signs the one-off messages; the app only listens on 127.0.0.1


# ---------- database connection: one per request ----------

def conn():
    if "conn" not in g:
        g.conn = db.connect()
    return g.conn


@app.teardown_appcontext
def close_conn(exc):
    c = g.pop("conn", None)
    if c is not None:
        c.close()


def back(default_endpoint):
    """Where to go after a form: the page it came from (only local paths), else a default."""
    nxt = request.form.get("next", "")
    return redirect(nxt if nxt.startswith("/") else url_for(default_endpoint))


def radar_jobs(config):
    """Jobs not yet in the pipeline (linked, or added by hand with the same link), excluding paused companies."""
    paused = {co["name"] for co in config["companies"] if co.get("paused")}
    rows = conn().execute(
        """SELECT * FROM jobs
           WHERE job_key NOT IN (SELECT job_key FROM pipeline WHERE job_key IS NOT NULL)
             AND url NOT IN (SELECT url FROM pipeline WHERE url <> '')
           ORDER BY date_found DESC, CASE fit WHEN 'High' THEN 0 ELSE 1 END, date_posted DESC"""
    )
    return [dict(r) for r in rows if r["company"] not in paused]


@app.context_processor
def nav_counts():
    """Numbers shown in the top navigation on every page."""
    config = db.load_config()
    open_radar = [j for j in radar_jobs(config) if j["posting_state"] == "Open"]
    last = conn().execute("SELECT run_at, new_matches FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "nav_radar": len(open_radar),
        "nav_pipeline": pipeline.stats(conn())["active"],
        "last_run": dict(last) if last else None,
    }


# ---------- Radar (home): prospects ----------

@app.get("/")
def radar_page():
    config = db.load_config()
    f = {
        "fit": request.args.get("fit", ""),
        "company": request.args.get("company", ""),
        "posting": request.args.get("posting", "Open"),
        "found": request.args.get("found", ""),
        "q": request.args.get("q", "").strip(),
    }
    rows = radar_jobs(config)
    companies = sorted({r["company"] for r in rows})
    if f["fit"]:
        rows = [r for r in rows if r["fit"] == f["fit"]]
    if f["company"]:
        rows = [r for r in rows if r["company"] == f["company"]]
    if f["posting"]:
        rows = [r for r in rows if r["posting_state"] == f["posting"]]
    if f["found"]:
        since = (date.today() - timedelta(days=int(f["found"]))).isoformat()
        rows = [r for r in rows if r["date_found"] >= since]
    if f["q"]:
        q = f["q"].lower()
        rows = [r for r in rows if q in f"{r['company']} {r['title']} {r['location']}".lower()]

    by_co = contacts_by_company(conn(), config["companies"])
    for r in rows:
        r["contacts"] = by_co.get(r["company"], [])

    # Column sort from the clicked header (?sort=company&dir=asc). Python's sort is
    # stable, so ties keep the default order (newest found, High fit first).
    sort = request.args.get("sort", "")
    if sort in RADAR_SORTS:
        direction = request.args.get("dir") or RADAR_SORTS[sort][1]
        rows.sort(key=RADAR_SORTS[sort][0], reverse=(direction == "desc"))
    else:
        sort, direction = "", ""

    manual = [co for co in config["companies"] if co.get("ats") == "manual" and not co.get("paused")]
    return render_template("radar.html", rows=rows, f=f, companies=companies, manual=manual,
                           today=db.today(), sort=sort, direction=direction, sorts=RADAR_SORTS)


# Sortable Radar columns: key -> (how to sort, direction used on first click)
RADAR_SORTS = {
    "found":    (lambda r: r["date_found"] or "", "desc"),
    "posted":   (lambda r: r["date_posted"] or "", "desc"),
    "company":  (lambda r: r["company"].lower(), "asc"),
    "role":     (lambda r: r["title"].lower(), "asc"),
    "location": (lambda r: (r["location"] or "").lower(), "asc"),
    "fit":      (lambda r: 0 if r["fit"] == "High" else 1, "asc"),      # asc = High first
    "contacts": (lambda r: len(r["contacts"]), "desc"),                  # desc = most contacts first
}


@app.post("/interested")
def interested():
    job_key = request.form["job_key"]
    pipeline.add_from_job(conn(), job_key)
    job = conn().execute("SELECT company, title FROM jobs WHERE job_key=?", (job_key,)).fetchone()
    flash(f"Moved to Pipeline: {job['company']}, {job['title']}")
    return back("radar_page")


# ---------- Pipeline: what you're pursuing ----------

@app.get("/pipeline")
def pipeline_page():
    config = db.load_config()
    items = pipeline.list_items(conn())
    all_contacts = [dict(r) for r in conn().execute("SELECT * FROM contacts")]
    url_by_name = {f"{c['first_name']} {c['last_name']}".strip().lower(): c["url"] for c in all_contacts}
    for it in items:
        it["people"] = contacts_for_name(all_contacts, it["company"], config["companies"])
        it["contact_url"] = url_by_name.get((it["contact"] or "").strip().lower())
    groups = pipeline.group_items(items)
    closed = [i for i in items if i["stage"] in pipeline.STAGES_CLOSED]
    return render_template("pipeline.html", groups=groups, closed=closed, stats=pipeline.stats(conn()),
                           stages=pipeline.STAGES, referrals=pipeline.REFERRALS, today=db.today())


@app.post("/pipeline/add")
def pipeline_add():
    f = request.form
    if not f.get("company", "").strip() or not f.get("title", "").strip():
        flash("Company and role are both needed.", "error")
        return redirect(url_for("pipeline_page"))
    pid = pipeline.add_manual(
        conn(), company=f["company"], title=f["title"], url=f.get("url", ""), location=f.get("location", ""),
        stage=f.get("stage", "Interested"), referral=f.get("referral", ""), contact=f.get("contact", ""),
        date_applied=f.get("date_applied") or None, notes=f.get("notes", ""),
    )
    flash(f"Added {f['company']}, {f['title']}")
    # Track this company on the Radar from now on (looks for a public jobs feed)
    for name, feed in refresh_feeds(conn()).items():
        flash(f"Added {name} to config.yaml: " + (f"found its {feed[0].title()} jobs feed. Its roles appear on the Radar after the next check."
                                                  if feed else "no public jobs feed, so it's listed under Check by hand."))
    return redirect(url_for("pipeline_page") + f"#item-{pid}")


@app.post("/pipeline/<int:item_id>/update")
def pipeline_update(item_id):
    f = request.form
    pipeline.update_item(
        conn(), item_id, stage=f["stage"], referral=f.get("referral", ""),
        contact=f.get("contact", "").strip(), date_applied=f.get("date_applied") or None,
        notes=f.get("notes", "").strip(),
    )
    flash("Saved.")
    return redirect(url_for("pipeline_page") + f"#item-{item_id}")


@app.post("/pipeline/<int:item_id>/remove")
def pipeline_remove(item_id):
    pipeline.remove_item(conn(), item_id)
    flash("Removed from Pipeline. If it came from the Radar, it's back there.")
    return redirect(url_for("pipeline_page"))


# ---------- Contacts ----------

@app.get("/contacts")
def contacts_page():
    config = db.load_config()
    q = request.args.get("q", "").strip().lower()
    seen, rows = set(), []
    for co, people in contacts_by_company(conn(), config["companies"]).items():
        for p in people:
            if p["url"] in seen:
                continue
            seen.add(p["url"])
            p["tracked"] = co
            rows.append(p)
    if q:
        rows = [p for p in rows if q in " ".join(str(p.get(k) or "") for k in
                ("first_name", "last_name", "company", "position", "how_i_know", "tracked")).lower()]
    rows.sort(key=lambda p: (p["tracked"], p["first_name"]))
    return render_template("contacts.html", rows=rows, q=q, strengths=db.STRENGTHS,
                           csv_exists=CSV_PATH.exists(), csv_path=CSV_PATH)


@app.post("/contacts/update")
def contacts_update():
    db.update_contact(conn(), request.form["url"], request.form.get("how_i_know", "").strip(),
                      request.form.get("strength", ""))
    flash("Contact saved.")
    return back("contacts_page")


@app.post("/contacts/import")
def contacts_import():
    if not CSV_PATH.exists():
        flash(f"Put your LinkedIn export at {CSV_PATH} first.", "error")
    else:
        flash(f"Imported {import_connections(conn())} connections. Your 'how I know' and strength notes were kept.")
    return redirect(url_for("contacts_page"))


# ---------- Companies ----------

@app.get("/companies")
def companies_page():
    config = db.load_config()
    results = {r["company"]: dict(r) for r in conn().execute("SELECT * FROM company_results")}
    rows = []
    for co in config["companies"]:
        r = results.get(co["name"])
        if co.get("paused"):
            state, text = "muted", "Paused (not fetched, roles hidden)"
        elif co["ats"] == "manual":
            state, text = "muted", "Check by hand"
        elif r is None:
            state, text = "muted", "Not run yet"
        elif r["ok"]:
            state, text = "ok", f"OK: {r['scanned']} postings, {r['matched']} matching"
        else:
            state, text = "error", f"Error: {r['error']}"
        rows.append({"co": co, "state": state, "text": text, "checked": r["run_at"] if r else ""})
    return render_template("companies.html", rows=rows, config_path=db.CONFIG_PATH)


# ---------- Run check now ----------

@app.post("/run")
def run_now():
    flash(format_summary(run_check()), "summary")
    return back("radar_page")


@app.context_processor
def demo_flag():
    return {"demo": db.DEMO}


def serve(open_browser=True):
    if open_browser:
        # Open the browser a moment after the server starts
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    label = " (DEMO data)" if db.DEMO else ""
    print(f"Job Radar{label} running at http://localhost:{PORT}  (Ctrl+C or close this window to stop it)")
    app.run(host="127.0.0.1", port=PORT, debug=False)


if __name__ == "__main__":
    serve()
