"""Demo data and first-run config, against throwaway folders."""
from radar import db, demo


def test_demo_seed_renders(tmp_path, monkeypatch):
    path = tmp_path / "demo.db"
    real_connect = db.connect
    monkeypatch.setattr(db, "connect", lambda p=path: real_connect(p))
    monkeypatch.setattr(db, "CONFIG_PATH", db.EXAMPLE_CONFIG_PATH)
    from radar import tracking
    monkeypatch.setattr(tracking, "detect_feed", lambda name, urls=(): None)
    conn = db.connect()
    assert demo.seed(conn) is True
    assert demo.seed(conn) is False                       # only once
    assert conn.execute("SELECT COUNT(*) FROM pipeline").fetchone()[0] >= 5
    import app as app_module
    client = app_module.app.test_client()
    for page in ["/", "/pipeline", "/contacts", "/companies"]:
        assert client.get(page).status_code == 200, page
    conn.close()


def test_first_run_creates_config(tmp_path, monkeypatch):
    target = tmp_path / "config.yaml"
    monkeypatch.setattr(db, "CONFIG_PATH", target)
    assert db.ensure_config() is True
    assert db.load_config()["companies"]
    assert db.ensure_config() is False                    # never overwrites yours
