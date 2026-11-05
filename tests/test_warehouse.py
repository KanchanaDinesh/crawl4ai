"""Storage: scrapers history, techintel JSONL import, views, local paths (PostgreSQL, the test server)."""

import importlib
import json


def test_scrapers_outputs_build_history(tmp_path, monkeypatch, pg_env):
    from warehouse import postgres
    postgres.setup()
    monkeypatch.setenv("SCRAPERS_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.delenv("SCRAPERS_NO_DB", raising=False)
    import scrapers.common as common
    importlib.reload(common)
    common.save_json([{"title": "a"}, {"title": "b"}], "news.json")
    common.save_csv([{"a": 1}, {"a": 2}], "cse_trade_summary.csv")
    common.save_json([{"title": "Rice 5kg", "price": "Rs 1,200"}], "products_keells.json")
    assert (tmp_path / "out" / "news.json").exists()                            # files still written
    with postgres.connect("reader") as con:
        assert con.execute("select scraper, count(*) from scrapers.scraper_rows group by 1 order by 1").fetchall() == \
            [("cse_trade_summary", 2), ("news", 2), ("products_keells", 1)]
        assert con.execute("select count(*) from scrapers.v_news_headlines").fetchone()[0] == 2
        assert con.execute("select title, price from scrapers.v_products").fetchall() == [("Rice 5kg", "Rs 1,200")]


def test_vehicles_history_latest_ads_and_price_changes(pg_env):
    """Vehicle ads in PostgreSQL (schema vehicles): every run kept, the latest per ad, and price changes."""
    from warehouse import postgres
    postgres.setup()
    from vehicles.store import VehicleStore
    store = VehicleStore()
    for n, price in enumerate((5_000_000, 4_750_000, 4_600_000)):
        ads = [{"rank": 1, "title": "Toyota Axio", "url": "https://ikman.lk/1", "price_lkr": price, "price": "Rs x",
                "photo_note": "kept in extra"}]
        if n < 2:
            ads.append({"rank": 2, "title": "Honda Fit", "url": "https://ikman.lk/2", "price_lkr": "3000000"})
        store.save_report({"site": "ikman", "success": True, "seconds": 3.2, "ads": ads},
                          saved_at=f"2026-11-0{n + 1}T10:00:00Z")
    store.save_report({"site": "riyasewana", "success": False, "error": "gave up after 120s", "ads": []})
    store.close()
    with postgres.connect("reader") as con:
        assert con.execute("select count(*) from vehicles.ads").fetchone()[0] == 5
        latest = con.execute("select title, price_lkr, times_seen from vehicles.v_vehicle_ads_latest "
                             "order by title").fetchall()
        assert latest == [("Honda Fit", 3_000_000, 2), ("Toyota Axio", 4_600_000, 3)]
        assert con.execute("select previous_price_lkr, price_lkr from vehicles.v_vehicle_price_changes "
                           "order by saved_at").fetchall() == [(5_000_000, 4_750_000), (4_750_000, 4_600_000)]
        assert con.execute("select count(*), sum(ads) from vehicles.runs").fetchone() == (4, 5)
        assert con.execute("select extra->>'photo_note' from vehicles.ads limit 1").fetchone()[0] == "kept in extra"


def test_vehicle_save_never_breaks_a_scrape(monkeypatch):
    import vehicles.store as vs
    monkeypatch.setattr(vs, "VehicleStore", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    assert vs.save_report({"site": "ikman", "ads": [{"title": "x"}]}) == 0


def test_record_output_never_breaks_a_scrape():
    import warehouse.scrapers_db as sdb
    assert sdb.record_output("x", [{"a": 1}]) == 0       # no database reachable (tests' dead address): 0, no error


def test_techintel_imports_old_jsonl_and_views_work(settings, pg_env):
    from techintel.store import TechStore
    from warehouse import postgres
    postgres.setup()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    rows = [{"domain": "a.ai", "status": "rejected", "reason": "x", "checked_at": "2026-11-01T00:00:00Z"},
            {"domain": "a.ai", "status": "qualified", "reason": "y", "category": "ai_ml", "score": 12.5,
             "checked_at": "2026-11-02T00:00:00Z"}]
    (settings.data_dir / "qualifications.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    store = TechStore(settings.data_dir)
    assert store.latest("qualifications")["a.ai"]["status"] == "qualified"            # latest wins
    assert len(list(store.read("qualifications"))) == 2                               # history kept
    assert (settings.data_dir / "qualifications.jsonl.imported").exists()
    assert store.con.execute("select status, category, score from tech.v_qualifications").fetchall() == \
        [("qualified", "ai_ml", 12.5)]
    store.close()
    again = TechStore(settings.data_dir)                                               # reopening doesn't re-import
    assert len(list(again.read("qualifications"))) == 2
    again.close()


def test_local_env_keeps_tool_files_in_project(tmp_path, monkeypatch):
    import project_paths
    monkeypatch.setenv("PROJECT_LOCAL_DIR", str(tmp_path / ".local"))
    for name in project_paths.LOCAL_ENV:
        monkeypatch.delenv(name, raising=False)
    importlib.reload(project_paths)
    project_paths.apply_local_env()
    import os
    assert os.environ["CRAWL4_AI_BASE_DIRECTORY"] == str(tmp_path / ".local" / "crawl4ai")
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path / ".local" / "ms-playwright")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/custom")                         # explicit settings win
    project_paths.apply_local_env()
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "/custom"


def test_backup_writes_a_dump_and_keeps_the_newest(pg_env, tmp_path):
    """python -m warehouse backup: pg_dump of the whole database; older backups beyond --keep are removed."""
    import subprocess
    from datetime import datetime
    from warehouse import backup, postgres
    postgres.setup()
    postgres.migrate()
    with postgres.connect("writer") as con:
        con.execute("insert into news.news_items (url, title) values ('https://x/1', 'Kept in the backup')")
    made = []
    for day in (1, 2, 3):
        path, removed = backup.backup(tmp_path, keep=2, now=datetime(2026, 11, day, 3, 0, 0))
        made.append(path)
    assert [p.name for p in backup.backups(tmp_path)] == ["crawl4ai-20261103-030000.dump", "crawl4ai-20261102-030000.dump"]
    assert removed == [made[0]] and not list(tmp_path.glob(".*partial"))
    listing = subprocess.run([backup.find_pg_dump().replace("pg_dump", "pg_restore"), "--list", str(made[-1])],
                             capture_output=True, text=True, check=True).stdout
    assert "TABLE DATA news news_items" in listing and "VIEW tech v_companies" in listing


def test_a_failed_backup_leaves_nothing_behind(tmp_path):
    from warehouse import backup
    import pytest
    with pytest.raises(backup.BackupError, match="pg_dump said"):     # tests' dead database address
        backup.backup(tmp_path)
    assert list(tmp_path.iterdir()) == []
