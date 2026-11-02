"""The vehicles scraper must never hang: a stuck page or a browser that won't start gives up in time."""

import asyncio
from types import SimpleNamespace

import pytest

from scrapers import common
from vehicles import scrape as vehicles

ADS_HTML = "<ul>" + "".join(
    f'<li class="gtm-normal-ad"><a class="gtm-ad-item" href="/ad/car-{i}"><h2 class="heading--x">Car {i}</h2></a></li>'
    for i in range(1, 4)) + "</ul>"


class FakeCrawler:
    started = 0
    start_delay = 0.0

    def __init__(self, config=None):
        pass

    async def __aenter__(self):
        FakeCrawler.started += 1
        await asyncio.sleep(FakeCrawler.start_delay)
        return self

    async def __aexit__(self, *exc):
        return None

    async def arun(self, url, config=None):
        if "slow" in url:
            await asyncio.sleep(3600)               # a page that never finishes
        return SimpleNamespace(success=True, html=ADS_HTML, error_message="")


def site(name):
    return {"name": name, "url": f"https://example.test/{name}", "output_file": f"v_{name}.json",
            "item_selector": "li.gtm-normal-ad",
            "fields": {"title": {"selector": "h2", "type": "text"},
                       "url": {"selector": "a", "type": "attribute", "attribute": "href"}}}


@pytest.fixture(autouse=True)
def fake_browser(monkeypatch, tmp_path):
    import warehouse
    monkeypatch.setattr(vehicles, "AsyncWebCrawler", FakeCrawler)
    monkeypatch.setattr(common, "OUTPUT_DIR", tmp_path)
    monkeypatch.setitem(warehouse.DB_FILES, "vehicles", tmp_path / "vehicles.duckdb")
    FakeCrawler.started, FakeCrawler.start_delay = 0, 0.0


def test_stuck_site_gives_up_and_next_site_still_runs():
    reports = asyncio.run(vehicles.run([site("slow"), site("fast")], 5, False, True, max_seconds=0.3))
    slow, fast = reports
    assert not slow["success"] and "gave up after" in slow["error"] and slow["seconds"] < 5
    assert fast["success"] and fast["count"] == 3
    assert FakeCrawler.started == 2                  # a fresh browser after the stuck page
    import duckdb
    con = duckdb.connect(str(common.OUTPUT_DIR / "vehicles.duckdb"), read_only=True)
    assert con.execute("select site, success, ads from runs order by site").fetchall() == [("fast", True, 3), ("slow", False, 0)]
    assert con.execute("select count(*) from v_vehicle_ads").fetchone()[0] == 3
    con.close()


def test_browser_that_never_starts_fails_fast(monkeypatch):
    monkeypatch.setattr(vehicles, "START_SECONDS", 0.2)
    FakeCrawler.start_delay = 3600
    with pytest.raises(RuntimeError, match="didn't start"):
        asyncio.run(vehicles.run([site("fast")], 5, False, True))


def test_site_config_can_set_its_own_limit():
    reports = asyncio.run(vehicles.run([{**site("slow"), "max_seconds": 0.2}], 5, False, True))
    assert "gave up" in reports[0]["error"] and reports[0]["seconds"] < 5


def test_cli_show_changes_and_sites(tmp_path, monkeypatch, capsys):
    """python -m vehicles show / changes / sites, on sample data."""
    import importlib
    monkeypatch.setenv("PROJECT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SCRAPERS_OUTPUT_DIR", str(tmp_path / "out"))
    import project_paths, warehouse
    importlib.reload(project_paths); importlib.reload(warehouse)
    from sample_data import build_vehicles
    build_vehicles(runs=3)
    from vehicles import cli
    cli.main(["sites"])
    assert "ikman" in capsys.readouterr().out
    cli.main(["show", "--new"])
    out = capsys.readouterr().out
    assert "Nissan Leaf 2014" in out and "NEW" in out and "2 of 2 ads" in out
    cli.main(["changes"])
    assert "->" in capsys.readouterr().out
