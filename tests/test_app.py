"""Pipeline logic + every page and button of the Flask app, against a throwaway database."""
import pytest

from radar import db, pipeline


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    # db.connect's default argument was bound at import time, so patch connect too
    real_connect = db.connect
    monkeypatch.setattr(db, "connect", lambda p=path: real_connect(p))
    # Tests edit a copy of the example config, never your real config.yaml
    import shutil
    cfg_copy = tmp_path / "config.yaml"
    shutil.copy(db.EXAMPLE_CONFIG_PATH, cfg_copy)
    monkeypatch.setattr(db, "CONFIG_PATH", cfg_copy)
    # Never hit the internet from tests: pretend every company has no public feed
    from radar import tracking
    monkeypatch.setattr(tracking, "detect_feed", lambda name, urls=(): None)
    c = db.connect()
    for i, (co, title, fit) in enumerate([("Affirm", "Staff Product Manager, Credit", "High"),
                                          ("Gusto", "Principal Product Manager", "Medium")]):
        db.insert_job_if_new(c, {"job_key": f"{co}|{i}", "company": co, "title": title, "location": "Remote US",
                                 "url": f"https://example.com/{i}", "date_posted": "2026-11-01",
                                 "date_found": db.today(), "fit": fit, "fit_reason": "Level: staff"})
    c.commit()
    yield c
    c.close()


@pytest.fixture
def client(conn):
    import app as app_module
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def test_promote_is_idempotent_and_logs(conn):
    a = pipeline.add_from_job(conn, "Affirm|0")
    b = pipeline.add_from_job(conn, "Affirm|0")
    assert a == b
    assert pipeline.stats(conn)["by_stage"]["Interested"] == 1
    assert pipeline.stats(conn)["week"]["interested"] == 1


def test_update_stamps_applied_date_and_logs(conn):
    pid = pipeline.add_from_job(conn, "Affirm|0")
    pipeline.update_item(conn, pid, stage="Applied", referral="Referred", contact="Ana Lee",
                         date_applied=None, notes="sent")
    row = conn.execute("SELECT * FROM pipeline WHERE id=?", (pid,)).fetchone()
    assert row["date_applied"] == db.today()
    st = pipeline.stats(conn)
    assert st["week"]["applied"] == 1 and st["week"]["outreach"] == 1


def test_manual_add_backdated(conn):
    pipeline.add_manual(conn, "Anthropic", "Product Manager, Claude", stage="Applied", at="2026-10-28")
    row = conn.execute("SELECT * FROM pipeline WHERE company='Anthropic'").fetchone()
    assert row["date_applied"] == "2026-10-28" and row["job_key"] is None


def test_pages_render(client):
    for path in ["/", "/pipeline", "/contacts", "/companies"]:
        r = client.get(path)
        assert r.status_code == 200, path


def test_interested_button_moves_job_off_radar(client, conn):
    row_marker = b'name="job_key" value="Affirm|0"'
    assert row_marker in client.get("/").data
    r = client.post("/interested", data={"job_key": "Affirm|0", "next": "/"})
    assert r.status_code == 302
    page = client.get("/").data
    assert row_marker not in page          # the row is gone from the Radar
    assert b"Moved to Pipeline" in page    # and the confirmation shows
    assert b"Staff Product Manager, Credit" in client.get("/pipeline").data


def test_pipeline_add_update_remove(client, conn):
    client.post("/pipeline/add", data={"company": "Redfin", "title": "Director - Product, Rentals",
                                       "stage": "Applied", "referral": "Reached out"})
    pid = conn.execute("SELECT id FROM pipeline WHERE company='Redfin'").fetchone()["id"]
    client.post(f"/pipeline/{pid}/update", data={"stage": "Interviewing", "referral": "Referred",
                                                 "contact": "Sam Rivera", "date_applied": "2026-10-28",
                                                 "notes": "phone screen Fri"})
    row = conn.execute("SELECT * FROM pipeline WHERE id=?", (pid,)).fetchone()
    assert (row["stage"], row["notes"]) == ("Interviewing", "phone screen Fri")
    client.post(f"/pipeline/{pid}/remove")
    assert conn.execute("SELECT COUNT(*) FROM pipeline").fetchone()[0] == 0


def test_removed_radar_item_returns_to_radar(client, conn):
    client.post("/interested", data={"job_key": "Gusto|1"})
    pid = conn.execute("SELECT id FROM pipeline").fetchone()["id"]
    client.post(f"/pipeline/{pid}/remove")
    assert b'name="job_key" value="Gusto|1"' in client.get("/").data


def test_pipeline_add_requires_company_and_title(client, conn):
    client.post("/pipeline/add", data={"company": "", "title": "x"})
    assert conn.execute("SELECT COUNT(*) FROM pipeline").fetchone()[0] == 0


def test_radar_sort(client):
    import re
    def companies(qs):
        return re.findall(r'value="(\w+)\|\d"', client.get("/" + qs).data.decode())
    assert companies("?sort=company&dir=asc") == ["Affirm", "Gusto"]
    assert companies("?sort=company&dir=desc") == ["Gusto", "Affirm"]
    assert companies("?sort=fit") == ["Affirm", "Gusto"]      # first click: High first
    assert client.get("/?sort=nonsense").status_code == 200   # unknown sort is ignored


def test_interested_split_by_outreach(conn):
    a = pipeline.add_manual(conn, "Toast", "Product Lead", referral="Reached out")
    b = pipeline.add_manual(conn, "Chime", "Staff PM")
    groups = {g["id"]: [i["id"] for i in g["items"]] for g in pipeline.group_items(pipeline.list_items(conn))}
    assert groups["need-outreach"] == [b] and groups["waiting"] == [a]
    assert "referred" not in groups                      # hidden while empty
    st = pipeline.stats(conn)
    assert (st["need_outreach"], st["waiting"]) == (1, 1)


def test_pipeline_companies_added_to_config(client, conn, monkeypatch):
    original = db.CONFIG_PATH.read_text(encoding="utf-8")
    client.post("/pipeline/add", data={"company": "Acme Widgets (via recruiter)", "title": "Director PM"})
    text = db.CONFIG_PATH.read_text(encoding="utf-8")
    assert text.startswith(original.rstrip())   # nothing above was rewritten (comments kept)
    entry = next(c for c in db.load_config()["companies"] if c["name"] == "Acme Widgets")
    assert entry["ats"] == "manual" and entry["auto"] is True

    # Running again doesn't duplicate it; a company config.yaml already covers isn't added
    from radar.tracking import refresh_feeds
    pipeline.add_manual(conn, "Affirm", "Staff PM")
    refresh_feeds(conn)
    names = [c["name"] for c in db.load_config()["companies"]]
    assert names.count("Acme Widgets") == 1 and names.count("Affirm") == 1

    # 30 days later a feed turns up: the same line is updated in place
    from radar import tracking
    conn.execute("UPDATE company_feeds SET checked_on='2000-01-01'")
    monkeypatch.setattr(tracking, "detect_feed", lambda name, urls=(): ("greenhouse", "acmewidgets"))
    refresh_feeds(conn)
    entry = next(c for c in db.load_config()["companies"] if c["name"] == "Acme Widgets")
    assert (entry["ats"], entry["board"]) == ("greenhouse", "acmewidgets")
    assert [c["name"] for c in db.load_config()["companies"]].count("Acme Widgets") == 1


def test_run_check_loads_config_itself(conn, monkeypatch):
    """The daily run is called with no arguments; it must load config (incl. Pipeline companies)."""
    from radar import run
    monkeypatch.setattr(run, "fetch_company", lambda co, prefs: [])
    pipeline.add_manual(conn, "Valve", "Business Development")
    summary = run.run_check()
    assert "Valve" not in summary["companies"]           # manual (no feed) companies aren't fetched
    assert summary["checked"] > 0


def test_dark_theme_loaded(client):
    assert b"themes/graphite.css" in client.get("/").data


def test_check_never_touches_your_edits_or_duplicates(conn, monkeypatch):
    """Golden rule: re-running the check adds no duplicates and leaves the Pipeline alone."""
    from radar import run
    pid = pipeline.add_from_job(conn, "Affirm|0")
    pipeline.update_item(conn, pid, "Applied", "Referred", "Ana Lee", "2026-10-01", "my notes")
    before = dict(conn.execute("SELECT * FROM pipeline WHERE id=?", (pid,)).fetchone())
    posting = {"id": "0", "title": "Staff Product Manager, Credit", "location": "Remote US",
               "url": "https://example.com/0", "posted": db.today()}
    monkeypatch.setattr(run, "fetch_company", lambda co, prefs: [posting] if co["name"] == "Affirm" else [])
    jobs_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    run.run_check()
    run.run_check()
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == jobs_before
    assert dict(conn.execute("SELECT * FROM pipeline WHERE id=?", (pid,)).fetchone()) == before
