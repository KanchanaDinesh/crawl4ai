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


def test_browser_that_cannot_start_gives_one_clear_line(monkeypatch):
    async def broken(self):
        raise Exception("BrowserType.launch: Executable doesn't exist at /x/chrome\n╔══ Playwright banner ══╗")
    monkeypatch.setattr(FakeCrawler, "__aenter__", broken)
    with pytest.raises(RuntimeError) as e:
        asyncio.run(vehicles.run([site("fast")], 5, False, True))
    assert str(e.value) == ("the browser could not start: BrowserType.launch: Executable doesn't exist at /x/chrome "
                            "(run: uv run python doctor.py)")


def test_no_ads_says_why_per_site(monkeypatch, caplog):
    from vehicles import cli
    async def fake_run(*a, **k):
        return [{"site": "ikman", "count": 0, "error": "Unexpected error in _crawl_web\nError: Failed on navigating ACS-GOTO:\n"
                 "Page.goto: net::ERR_NAME_NOT_RESOLVED at https://ikman.lk\nCall log: ..."},
                {"site": "riyasewana", "count": 0, "success": True}]
    monkeypatch.setattr(cli, "run", fake_run)
    with pytest.raises(SystemExit):
        cli.main(["run"])
    assert ("No ads from any site. ikman: Page.goto: net::ERR_NAME_NOT_RESOLVED at https://ikman.lk; "
            "riyasewana: 0 ads matched (see output/html_dumps/)") in caplog.text


def test_ikman_photos_come_from_the_page_by_the_ads_slug():
    """ikman's <img> is a placeholder until scrolled, but the photo URLs are in the page with each ad's slug."""
    site = next(s for s in vehicles.load_sites() if s["name"] == "ikman")
    ad = ("<li class='normal--x gtm-normal-ad'><a class='gtm-ad-item' href='/en/ad/bajaj-re-2000-for-sale-kalutara-129'>"
          "<h2 class='heading--x'>Bajaj RE 2000</h2></a><div class='price--x'>Rs 635,000</div>"
          "<img src='data:image/png;base64,AAAA' loading='lazy'></li>")
    page = (f"<html><body><ul>{ad}</ul><script>window.state={{\"img\":\"https://i.ikman-st.com/"
            "bajaj-re-2000-for-sale-kalutara-129/17b1/142/107/cropped.jpg\"}}</script></body></html>")
    ads = vehicles.parse_ads(page, site, 5)
    assert ads[0]["image"] == "https://i.ikman-st.com/bajaj-re-2000-for-sale-kalutara-129/17b1/142/107/cropped.jpg"


def test_the_vehicles_browser_is_plain():
    """No stealth settings or "magic" mode: they're made to hide automation from bot detection."""
    import json
    from pathlib import Path
    text = (Path(vehicles.__file__).parent / "config" / "sites.json").read_text()
    assert all(not s.get("crawl", {}).get("magic") for s in json.loads(text))
    assert "enable_stealth" not in Path(vehicles.__file__).read_text()
