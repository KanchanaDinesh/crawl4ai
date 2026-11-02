"""DuckDB storage: scrapers history, techintel JSONL import, views, local paths."""

import importlib
import json


def test_scrapers_outputs_build_history_and_price_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJECT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SCRAPERS_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.delenv("SCRAPERS_NO_DB", raising=False)
    import project_paths
    import warehouse
    import warehouse.scrapers_db as sdb
    import scrapers.common as common
    for mod in (project_paths, warehouse, sdb, common):
        importlib.reload(mod)

    for price in (5_000_000, 4_750_000):
        common.save_json({"site": "ikman", "ads": [
            {"rank": 1, "title": "Toyota Axio", "url": "https://ikman.lk/1", "price_lkr": price},
            {"rank": 2, "title": "Honda Fit", "url": "https://ikman.lk/2", "price_lkr": 3_000_000}]}, "vehicles_ikman.json")
    common.save_csv([{"a": 1}, {"a": 2}], "cse_trade_summary.csv")
    assert (tmp_path / "out" / "vehicles_ikman.json").exists()                  # files still written

    con = warehouse.connect(warehouse.DB_FILES["scrapers"])
    assert con.execute("select count(*) from scraper_rows").fetchone()[0] == 6
    latest = con.execute("select title, price_lkr, times_seen from v_vehicle_ads_latest order by title").fetchall()
    assert latest == [("Honda Fit", 3_000_000, 2), ("Toyota Axio", 4_750_000, 2)]
    assert con.execute("select title, previous_price_lkr, price_lkr from v_vehicle_price_changes").fetchall() == \
        [("Toyota Axio", 5_000_000, 4_750_000)]
    assert con.execute("select data->>'site' from scraper_rows where scraper='vehicles_ikman' limit 1").fetchone()[0] == "ikman"
    con.close()


def test_record_output_never_breaks_a_scrape(monkeypatch):
    import warehouse.scrapers_db as sdb
    monkeypatch.setattr(sdb, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    assert sdb.record_output("x", [{"a": 1}]) == 0


def test_techintel_imports_old_jsonl_and_views_work(settings):
    from techintel.store import DuckStore
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    rows = [{"domain": "a.ai", "status": "rejected", "reason": "x", "checked_at": "2026-11-01T00:00:00Z"},
            {"domain": "a.ai", "status": "qualified", "reason": "y", "category": "ai_ml", "score": 12.5,
             "checked_at": "2026-11-02T00:00:00Z"}]
    (settings.data_dir / "qualifications.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    store = DuckStore(settings.data_dir)
    assert store.latest("qualifications")["a.ai"]["status"] == "qualified"            # latest wins
    assert len(list(store.read("qualifications"))) == 2                               # history kept
    assert (settings.data_dir / "qualifications.jsonl.imported").exists()
    assert store.con.execute("select status, category, score from v_qualifications").fetchall() == [("qualified", "ai_ml", 12.5)]
    store.close()
    again = DuckStore(settings.data_dir)                                               # reopening doesn't re-import
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
