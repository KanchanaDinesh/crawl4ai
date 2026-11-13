"""Motorcycles: the bike listings of ikman.lk and riyasewana.com are read like the car listings and saved with the
category "motorcycle", so cars and bikes can be told apart. The sample cards are trimmed from the real pages
(2026-11-13)."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from scrapers import common
from vehicles import scrape

IKMAN_BIKES = """<html><body><ul>
<li class="top-ad--3dRNK gtm-top-ad"><a class="card-link--3ssYv gtm-ad-item" href="/en/ad/honda-cb-2010-for-sale-kandy">
  <h2 class="heading--2eONR">Honda CB 2010</h2></a><div class="price--3SnqI">Rs 300,000</div></li>
<li class="normal--2QYVk gtm-normal-ad"><a class="card-link--3ssYv gtm-ad-item" href="/en/ad/bajaj-pulsar-135-2011-for-sale-galle-412"
  title="Bajaj Pulsar 135 2011 for sale"><div class="image--2hizm"><img alt="Bajaj Pulsar 135 2011" loading="lazy"
  src="data:image/png;base64,iVBORw0KGgo"></div><h2 class="heading--2eONR">Bajaj Pulsar 135 2011</h2>
  <div class="description--2-ez3">Galle, Motorbikes</div><div class="price--3SnqI">Rs 190,000</div>
  <div class="updated-time--1DbCk">1 minute</div></a></li>
<li class="normal--2QYVk gtm-normal-ad"><a class="card-link--3ssYv gtm-ad-item" href="/en/ad/yamaha-fzs-v3-2019-for-sale-colombo">
  <h2 class="heading--2eONR">Yamaha FZS V3 2019</h2><div class="description--2-ez3">Colombo, Motorbikes</div>
  <div class="price--3SnqI">Rs 755,000</div><div class="updated-time--1DbCk">just now</div></a></li>
</ul><script>window.initialData={"img":"https://i.ikman-st.com/bajaj-pulsar-135-2011-for-sale-galle-412/5dd2/142/107/cropped.jpg"}</script>
</body></html>"""

RIYASEWANA_BIKES = """<html><body><ul>
<li class="v-card promoted"><div class="v-card-img"><a href="https://riyasewana.com/buy/yamaha-ray-zr-sale-boralesgamuwa-12644528">
  <img src="//riyasewana.com/thumb/thumbyamaha-ray-zr-71200501491.jpg"></a><span class="top-ribbon">TOP AD</span></div>
  <div class="v-card-body"><div class="v-card-title"><a href="https://riyasewana.com/buy/yamaha-ray-zr-sale-boralesgamuwa-12644528">Yamaha Ray ZR Motorbike</a></div>
  <div class="v-card-price">Rs. 480,000</div><div class="v-card-year">2018</div>
  <div class="v-card-meta">Boralesgamuwa<span class="v-sep">·</span>46,180 km</div><div class="v-card-date">Nov 10</div></div></li>
<li class="v-card"><div class="v-card-img"><a href="https://riyasewana.com/buy/honda-hornet-sale-kurunegala-12660819">
  <img src="//riyasewana.com/thumb/thumbhonda-hornet-1322064213341.jpg"></a></div>
  <div class="v-card-body"><div class="v-card-title"><a href="https://riyasewana.com/buy/honda-hornet-sale-kurunegala-12660819">Honda Hornet Motorbike</a></div>
  <div class="v-card-price">Negotiable</div><div class="v-card-year">2014</div>
  <div class="v-card-meta">Kurunegala<span class="v-sep">·</span>22,500 km</div><div class="v-card-date">3m ago</div></div></li>
<li class="v-card"><div class="v-card-img"><a href="https://riyasewana.com/buy/yamaha-fz-s-sale-matara-12660825">
  <img src="//riyasewana.com/thumb/thumbyamaha-fz-s-1322092922201.jpg"></a></div>
  <div class="v-card-body"><div class="v-card-title"><a href="https://riyasewana.com/buy/yamaha-fz-s-sale-matara-12660825">Yamaha FZ-S Motorbike</a></div>
  <div class="v-card-price">Rs. 1,045,000</div><div class="v-card-year">2025</div>
  <div class="v-card-meta">Matara<span class="v-sep">·</span>6,430 km</div><div class="v-card-date">Just now</div></div></li>
</ul></body></html>"""


def listing(name: str, category: str) -> dict:
    return next(s for s in scrape.load_sites() if s["name"] == name and s.get("category") == category)


def test_each_site_has_a_car_and_a_motorcycle_listing():
    sites = scrape.load_sites()
    assert listing("ikman", "motorcycle")["url"] == "https://ikman.lk/en/ads/sri-lanka/motorbikes-scooters"
    assert listing("riyasewana", "motorcycle")["url"] == "https://riyasewana.com/search/motorcycles"
    assert {(s["name"], s["category"]) for s in sites} == {("ikman", "car"), ("ikman", "motorcycle"),
                                                           ("riyasewana", "car"), ("riyasewana", "motorcycle")}
    files = [s["output_file"] for s in sites]
    assert len(set(files)) == len(files)              # one JSON per listing: bikes don't overwrite cars
    assert all(s.get("enabled", True) for s in sites)


def test_ikman_bike_cards():
    ads = scrape.parse_ads(IKMAN_BIKES, listing("ikman", "motorcycle"), 10)
    assert [a["title"] for a in ads] == ["Bajaj Pulsar 135 2011", "Yamaha FZS V3 2019"]   # the top ad is left out
    pulsar = ads[0]
    assert (pulsar["category"], pulsar["price_lkr"], pulsar["location"]) == ("motorcycle", 190_000, "Galle, Motorbikes")
    assert pulsar["url"] == "https://ikman.lk/en/ad/bajaj-pulsar-135-2011-for-sale-galle-412"
    assert pulsar["image"] == "https://i.ikman-st.com/bajaj-pulsar-135-2011-for-sale-galle-412/5dd2/142/107/cropped.jpg"


def test_riyasewana_bike_cards():
    ads = scrape.parse_ads(RIYASEWANA_BIKES, listing("riyasewana", "motorcycle"), 2)
    # newest ad number first, so the older promoted ad falls off the end
    assert [a["title"] for a in ads] == ["Yamaha FZ-S Motorbike", "Honda Hornet Motorbike"]
    fz, hornet = ads
    assert (fz["category"], fz["price_lkr"], fz["year"]) == ("motorcycle", 1_045_000, "2025")
    assert "6,430 km" in fz["mileage"] and fz["image"] == "https://riyasewana.com/thumb/thumbyamaha-fz-s-1322092922201.jpg"
    assert hornet["price"] == "Negotiable" and hornet["price_lkr"] is None


def test_listing_label_names_bikes_in_logs_and_files():
    assert scrape.listing_label(listing("ikman", "car")) == "ikman"
    assert scrape.listing_label(listing("ikman", "motorcycle")) == "ikman motorcycle"


class BikePages:
    """A browser that answers each listing with its sample page."""

    def __init__(self, config=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def arun(self, url, config=None):
        html = RIYASEWANA_BIKES if "riyasewana" in url else IKMAN_BIKES
        return SimpleNamespace(success=True, html=html, error_message="")


def test_a_run_saves_bikes_with_their_category(pg_env, monkeypatch, tmp_path):
    from warehouse import postgres
    postgres.setup()
    monkeypatch.setattr(scrape, "AsyncWebCrawler", BikePages)
    monkeypatch.setattr(common, "OUTPUT_DIR", tmp_path)
    sites = [listing("ikman", "motorcycle"), listing("riyasewana", "motorcycle")]
    reports = asyncio.run(scrape.run(sites, 5, False, True))
    assert [(r["site"], r["category"], r["count"]) for r in reports] == [("ikman", "motorcycle", 2), ("riyasewana", "motorcycle", 3)]
    saved = json.loads((tmp_path / "vehicles_ikman_motorcycles.json").read_text())
    assert saved["category"] == "motorcycle" and saved["ads"][0]["category"] == "motorcycle"
    with postgres.connect("reader") as con:
        assert con.execute("select site, category, count(*), sum(price_lkr) from vehicles.v_vehicle_ads_latest "
                           "group by 1, 2 order by 1").fetchall() == [("ikman", "motorcycle", 2, 945_000),
                                                                      ("riyasewana", "motorcycle", 3, 1_525_000)]
        assert con.execute("select distinct category from vehicles.runs").fetchall() == [("motorcycle",)]
        assert con.execute("select count(*) from vehicles.ads where extra ? 'category'").fetchone()[0] == 0


def test_ads_saved_before_categories_get_one(pg_env):
    """Older rows had no category: riyasewana only read cars, and ikman's location ends with its own category."""
    from warehouse import postgres
    postgres.setup()
    with postgres.connect("writer", autocommit=True) as con:
        postgres.migrate(con, ("vehicles",))
        old = [("ikman", "Galle, Motorbikes"), ("ikman", "Colombo, Cars"), ("ikman", "Kandy, Three Wheelers"),
               ("ikman", "Colombo, Lorries & Trucks"), ("ikman", "Colombo, Auto Parts & Accessories"), ("riyasewana", None)]
        for i, (site, location) in enumerate(old):
            con.execute("insert into vehicles.ads (saved_at, site, title, url, location) values (now(), %s, %s, %s, %s)",
                        [site, f"Ad {i}", f"https://example.test/{i}", location])
        con.execute("insert into vehicles.runs (saved_at, site, source_url, success, ads) values "
                    "(now(), 'ikman', 'https://ikman.lk/en/ads/sri-lanka/vehicles', true, 5), "
                    "(now(), 'ikman', 'https://ikman.lk/en/ads/sri-lanka/cars', true, 5), (now(), 'riyasewana', null, true, 1)")
        con.execute("delete from public.schema_versions where schema = 'vehicles'")   # as on the Mac: the file changed
        assert postgres.migrate(con, ("vehicles",)) == ["vehicles"]
        got = con.execute("select location, category from vehicles.ads order by url").fetchall()
        assert [c for _, c in got] == ["motorcycle", "car", "three-wheeler", "lorry", "other", "car"]
        assert con.execute("select source_url, category from vehicles.runs order by source_url nulls last").fetchall() == [
            ("https://ikman.lk/en/ads/sri-lanka/cars", "car"), ("https://ikman.lk/en/ads/sri-lanka/vehicles", None),
            (None, "car")]
        con.execute("update vehicles.ads set category = 'car' where url = 'https://example.test/0'")   # never redone
        con.execute("delete from public.schema_versions where schema = 'vehicles'")
        postgres.migrate(con, ("vehicles",))
        assert con.execute("select category from vehicles.ads where url = 'https://example.test/0'").fetchone()[0] == "car"


@pytest.fixture
def bikes_saved(pg_env, tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("PROJECT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SCRAPERS_OUTPUT_DIR", str(tmp_path / "out"))
    import project_paths
    import warehouse
    importlib.reload(project_paths); importlib.reload(warehouse)
    from warehouse import postgres
    postgres.setup()
    from sample_data import build_vehicles
    build_vehicles(runs=3, bikes=True)


def test_cli_filters_by_category(bikes_saved, capsys, monkeypatch):
    from vehicles import cli
    cli.main(["show", "--category", "motorcycle", "--days", "3650"])
    out = capsys.readouterr().out
    assert "Bajaj Pulsar 150 2018" in out and "Yamaha FZ-S Motorbike" in out and "Toyota" not in out
    assert "3 of 3 ads" in out and "motorcycle" in out
    cli.main(["show", "--category", "car", "--days", "3650"])
    assert "7 of 7 ads" in capsys.readouterr().out
    cli.main(["changes", "--category", "motorcycle"])
    out = capsys.readouterr().out
    assert "Bajaj Pulsar 150 2018" in out and "Honda Vezel" not in out
    cli.main(["sites"])
    assert "motorcycle" in capsys.readouterr().out

    picked = []

    async def fake_run(sites, *a, **k):
        picked.extend(sites)
        return [{"site": s["name"], "category": s["category"], "count": 1, "success": True} for s in sites]
    monkeypatch.setattr(cli, "run", fake_run)
    cli.main(["run", "--category", "motorcycle"])
    assert [(s["name"], s["category"]) for s in picked] == [("ikman", "motorcycle"), ("riyasewana", "motorcycle")]
    picked.clear()
    cli.main(["run", "--site", "ikman"])              # a site means all its listings
    assert [s["category"] for s in picked] == ["car", "motorcycle"]
    with pytest.raises(SystemExit, match="No listing with category boat"):
        cli.main(["run", "--category", "boat"])
