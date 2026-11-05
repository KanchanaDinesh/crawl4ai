"""The dashboard tabs of the web app (webapp/insights/) against sample data built through the real stores: the queries,
what the pages show, the rights, and how numbers are drawn. Data lives in pg_env's database; users and sessions in
pytest-django's test database, both on the throwaway PostgreSQL."""

import importlib
import json
import re

import pytest
from django.contrib.auth.models import Group, User
from django.urls import reverse
from django.utils import timezone

from sample_data import build_news, build_tech
from webapp.catalog.models import Dataset, DatasetRight
from webapp.insights import charts, queries
from webapp.insights.common import TABS, Reader, last_runs
from webapp.insights.templatetags import insights_ui as ui

pytestmark = pytest.mark.django_db


def make_user(name, *roles):
    u = User.objects.create_user(name, password="correct-horse-battery-9")
    u.groups.set(Group.objects.filter(name__in=roles))
    return u


def read(fn, *args, **kwargs):
    with Reader() as db:
        out = db.get(fn, *args, **kwargs)
    assert not db.error, db.error
    return out


def text_of(res) -> str:
    assert res.status_code == 200, res.content[:300]
    return res.content.decode()


def kpi_values(html: str) -> list[str]:
    return re.findall(r'<div class="kpi"><b>(.*?)</b>', html)


def figures(html: str) -> list[dict]:
    return [json.loads(s) for s in re.findall(r'<div class="plot"[^>]*><script type="application/json">(.*?)</script>', html, re.S)]


@pytest.fixture
def dash(tmp_path, monkeypatch, pg_env, client):
    from warehouse import postgres
    postgres.setup()                                   # the tools' schemas live in the test PostgreSQL
    monkeypatch.setenv("PROJECT_DATA_DIR", str(tmp_path / "data"))
    for name in ("WORLDNEWS_DATA_DIR", "TECHINTEL_DATA_DIR", "MARKETS_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    import project_paths
    import warehouse
    for mod in (project_paths, warehouse):
        importlib.reload(mod)
    from worldnews.settings import load_settings
    build_news(warehouse.DATA_DIRS["news"], load_settings().raw["tech_keywords"])
    build_tech(warehouse.DATA_DIRS["tech"])
    client.force_login(make_user("ada", "Administrator"))
    return client, warehouse.DATA_DIRS


# ── world news ───────────────────────────────────────────────────────────────────────────────────────────────
def test_news_queries(dash):
    s = read(queries.news_summary, 7)
    assert s["totals"]["headlines"] == 31 and s["last_run"]["sources_ok"] == 12
    items = read(queries.news_items, limit=5)
    times = [i["published_at"] for i in items["items"]]
    assert times == sorted(times, reverse=True) and items["total"] == 31
    lk = read(queries.news_items, group="LK")
    assert lk["total"] == 6 and all(i["group_code"] == "LK" for i in lk["items"])
    assert read(queries.news_items, tech=True)["total"] == 11 and read(queries.news_items, new=True)["total"] == 12
    assert read(queries.news_items, q="chip")["total"] == 2
    reuters = next(r for r in read(queries.news_sources) if r["source"] == "Reuters")
    assert reuters["status"] == "failed" and reuters["failed_streak"] == 2
    assert len(read(queries.news_runs)) == 2
    assert {"topics_by_day", "by_hour"} <= set(s) and all(0 <= r["hour"] < 24 for r in s["by_hour"])
    assert read(queries.news_items, topic="tech")["total"] == 11
    both = read(queries.news_items, group="US,LK")
    assert both["total"] == 13 and {i["group_code"] for i in both["items"]} == {"US", "LK"}


def test_news_tab(dash):
    client, _ = dash
    s = read(queries.news_summary, 7)
    html = text_of(client.get(reverse("insights_news")))
    values = kpi_values(html)
    assert values[0] == f"{s['totals']['in_period']:,}" and values[3] == "12/" + str(s["last_run"]["sources"]) and values[5] == "31"
    assert "31 · newest first" in html and "Sri Lanka" in html and "Reuters" in html
    assert "/static/js/insights.js?v=" in html and "/static/css/insights.css?v=" in html and "plotly-basic.min.js" in html
    days_chart = figures(html)[0]
    assert [t["name"] for t in days_chart["data"]] == ["tech", "other"]
    x = days_chart["data"][0]["x"]
    assert len(x) == 14 and x[-1] == timezone.localdate().isoformat()
    by_day = {r["day"]: r for r in s["by_day"]}
    assert days_chart["data"][0]["y"] == [(by_day.get(d) or {}).get("tech", 0) for d in x]


def test_news_filters_come_back_as_rows_and_the_address_follows(dash):
    client, _ = dash
    res = client.get(reverse("insights_news_items"), {"group": "LK", "days": 7, "new": ""})
    html = text_of(res)
    assert html.count('class="news-item"') == 6 and "<html" not in html
    assert json.loads(res["HX-Trigger"]) == {"listCount": "6 · newest first"}
    assert res["HX-Push-Url"] == reverse("insights_news") + "?days=7&group=LK"
    page = text_of(client.get(reverse("insights_news"), {"group": "LK", "q": "zzz-nothing"}))   # a reloaded filter
    assert 'value="LK" checked' in page and 'value="zzz-nothing"' in page and "No headlines match these filters." in page


def test_news_list_pages(dash):
    client, _ = dash
    from webapp.insights import news
    old, news.PAGE = news.PAGE, 10
    try:
        first = client.get(reverse("insights_news_items"))
        html = text_of(first)
        assert html.count('class="news-item"') == 10 and "offset=10" in html
        assert json.loads(first["HX-Trigger"])["listCount"] == "31 · showing 10 · newest first"
        second = client.get(reverse("insights_news_items"), {"offset": 10})
        assert json.loads(second["HX-Trigger"])["listCount"] == "31 · showing 20 · newest first"
        assert "HX-Push-Url" not in second                  # scrolling isn't a new address
    finally:
        news.PAGE = old


def test_news_analysis(dash):
    client, _ = dash
    a = read(queries.news_analysis, 7)
    assert a["headlines"] > 0 and a["topics"] and a["tone"]["label"] and all("label" in g for g in a["tone"]["by_group"])
    assert {"stories", "people", "countries", "rising", "topics_by_group", "sri_lanka"} <= set(a)
    lk = read(queries.news_analysis, 7, ["LK"])
    assert 0 < lk["headlines"] < a["headlines"] and {g["group"] for g in lk["tone"]["by_group"]} == {"LK"}
    html = text_of(client.get(reverse("insights_news_analysis"), {"days": 7}))
    assert f"{a['headlines']:,} headlines" in html and "Names and places" in html and "Tone" in html
    assert "No headlines in this period yet." in text_of(client.get(reverse("insights_news_analysis"), {"group": "XX"}))


def test_a_run_in_the_middle_of_saving_never_stops_the_tab(dash):
    """PostgreSQL: the tab keeps answering with what was saved before."""
    from warehouse import postgres
    client, _ = dash
    writer = postgres.connect("writer")
    writer.execute("insert into news.news_items (url, title, first_seen_at) values ('https://x/new', 'New', now())")
    writer.execute("update news.news_runs set headlines = headlines + 1")
    try:
        assert kpi_values(text_of(client.get(reverse("insights_news"))))[5] == "31"
        writer.commit()
        assert kpi_values(text_of(client.get(reverse("insights_news"))))[5] == "32"
    finally:
        writer.close()


# ── tech intel ───────────────────────────────────────────────────────────────────────────────────────────────
def test_tech_queries(dash):
    s = read(queries.tech_summary, 30)
    c = s["counts"]
    assert (c["candidates"], c["qualified"], c["watch"], c["rejected"], c["profiled"]) == (9, 5, 1, 2, 5)
    domains = read(queries.tech_domains, days=30)["items"]
    seen = [d["seen_at"] for d in domains]
    assert seen == sorted(seen, reverse=True)                                  # latest first
    assert {d["status"] for d in domains} == {"qualified", "watch", "rejected", "not checked"}
    assert read(queries.tech_domains, status="qualified")["total"] == 5
    assert read(queries.tech_domains, q="bakery")["items"][0]["domain"] == "kandy-bakery.lk"
    d = read(queries.tech_domain, "agentforge.dev")
    assert d["profile"]["tech_stack"] == ["Next.js", "Vercel", "PostHog"] and d["registration"]["registered_at"]
    assert d["changes"][0]["after"] == "Stripe"                                # no JSON quotes
    assert read(queries.tech_changes)[0]["domain"] == "agentforge.dev" and len(read(queries.tech_runs)) == 6
    assert sum(r["n"] for r in s["domain_age"]) == c["qualified"] and "new_domains_30d" in c
    assert all("qualified" in r for r in s["by_day"])
    regs = [x["registered_at"] for x in read(queries.tech_domains, sort="registered")["items"] if x["registered_at"]]
    assert regs == sorted(regs, reverse=True)                                  # newest domain first
    scores = [x["score"] for x in read(queries.tech_domains, sort="score")["items"] if x["score"] is not None]
    assert scores == sorted(scores, reverse=True)


def test_tech_company_facts(dash):
    c = read(queries.tech_summary, 30)
    assert (c["counts"]["companies"], c["counts"]["with_linkedin"], c["counts"]["with_people"]) == (2, 1, 1)
    assert {r["platform"] for r in c["social_platforms"]} == {"linkedin", "x", "github", "facebook"}
    row, = read(queries.tech_domains, company="linkedin")["items"]
    assert row["domain"] == "agentforge.dev" and row["people"] == 1 and row["employees"] == "11-50"
    assert read(queries.tech_domains, company="any")["total"] == 2
    assert read(queries.tech_domain, "agentforge.dev")["company"]["github"]["public_repos"] == 12


def test_tech_tab_rows_and_side_panel(dash):
    client, _ = dash
    html = text_of(client.get(reverse("insights_tech")))
    values = kpi_values(html)
    assert values[1:5] == ["5", "1", "2", "5"] and values[8:] == ["1", "1"]
    assert "agentforge.dev" in html and 'class="soc"' in html and "Status reason" not in html
    res = client.get(reverse("insights_tech_rows"), {"status": "qualified", "sort": "score", "company": "nonsense"})
    rows = text_of(res)
    assert rows.count('<tr class="click"') == 5 and "<table" not in rows
    assert json.loads(res["HX-Trigger"])["listCount"].startswith("5 · ")
    assert res["HX-Push-Url"].endswith("?days=30&status=qualified&sort=score")      # an unknown filter is dropped
    panel = text_of(client.get(reverse("insights_tech_site", args=["agentforge.dev"])))
    for expected in ("Status reason", "Next.js", "PostHog", "started using Stripe", "Check history", "12 repos"):
        assert expected in panel


# ── markets ──────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def market(dash):
    from sample_data import build_markets
    client, dirs = dash
    build_markets(dirs["markets"].parent)
    return client


def test_markets_queries(market):
    s = read(queries.markets_summary)
    cards = {c["symbol"]: c for c in s["cards"]}
    assert {"^GSPC", "ASPI", "S&P SL20", "GC=F", "BTC", "LKR=X", "GOLD24K_LKR_PAWN"} <= set(cards)
    assert len(cards["ASPI"]["spark"]) == 6                                    # one point per saved run
    assert [r["rank"] for r in read(queries.markets_quotes, "crypto")] == [1, 2, 3, 4, 5, 6]
    lk = read(queries.markets_quotes, "stock_lk")
    assert len(lk) == 4 and all(r["currency"] == "LKR" for r in lk)
    assert {r["symbol"] for r in read(queries.markets_quotes, "commodity_lkr")} == {"GOLD_LKR_G", "GOLD24K_LKR_PAWN", "GOLD22K_LKR_PAWN", "SILVER_LKR_G"}
    assert [r["symbol"] for r in read(queries.markets_quotes, "stock_us", "apple")] == ["AAPL"]
    hist = read(queries.markets_history, "index", "ASPI", 30)
    assert len(hist["points"]) == 6 and hist["quote"]["symbol"] == "ASPI"
    assert [x["source"] for x in s["sources"] if not x["ok"]] == ["yahoo BRK-B"]
    # History: Yahoo's daily closes before the first saved run, then every run's price
    long = read(queries.markets_history, "index", "^GSPC", 100)["points"]
    assert len(long) > 80 and long[0]["t"] < long[-1]["t"] and long[0]["t"].endswith("T12:00:00Z")
    rows = {r["symbol"]: r for r in read(queries.markets_performance)}
    assert rows["^GSPC"]["1m"] is not None and rows["^GSPC"]["3m"] is not None # from the daily closes
    assert rows["ASPI"]["1m"] is None and rows["ASPI"]["1d"] is not None       # CSE: only today's runs
    assert {"BTC", "GC=F", "LKR=X"} <= set(rows) and "AAPL" not in rows
    compared = read(queries.markets_compare, [("index", "^GSPC"), ("index", "ASPI"), ("crypto", "NOPE")], 30)
    assert [x["symbol"] for x in compared] == ["^GSPC", "ASPI"]                # unknown ones are skipped


def test_markets_tab_prices_sort_and_map(market):
    client = market
    html = text_of(client.get(reverse("insights_markets")))
    assert html.count('class="mk-card"') == len(read(queries.markets_summary)["cards"])
    assert "Top gainers" in html and "Performance" in html and 'value="index" checked' in html
    metals = client.get(reverse("insights_prices"), {"cls": "metals"})
    rows = text_of(metals)
    expected = len(read(queries.markets_quotes, "commodity")) + len(read(queries.markets_quotes, "commodity_lkr"))
    assert rows.count('<tr class="click"') == expected and 'id="mk-title" hx-swap-oob="true">Gold, silver &amp; oil' in rows
    assert json.loads(metals["HX-Trigger"])["listCount"].startswith(f"{expected} · ")
    by_price = text_of(client.get(reverse("insights_prices"), {"cls": "crypto", "sort": "price", "dir": "-1"}))
    assert "▼" in by_price and by_price.index("BTC") < by_price.index("ETH")
    tiles = text_of(client.get(reverse("insights_prices"), {"cls": "crypto", "mode": "map"}))
    assert 'class="tile"' in tiles and "<table" not in tiles


def test_compare_picks_are_kept_and_a_line_keeps_its_colour(market):
    client = market
    start = text_of(client.get(reverse("insights_markets")))
    assert [t["name"].split(" (")[0] for t in figures(start)[0]["data"]] == ["S&P 500", "Gold", "BTC", "ASPI"]
    url = reverse("insights_compare")
    client.get(url, {"toggle": "commodity:GC=F"})             # remove Gold
    after = text_of(client.get(url, {"days": 90}))
    lines = {t["name"].split(" (")[0]: t["line"]["color"] for t in figures(after)[0]["data"]}
    assert lines == {"S&P 500": "token:series-1", "BTC": "token:series-3", "ASPI": "token:series-4"}   # nobody repainted
    added = text_of(client.get(url, {"toggle": "fx:LKR=X"}))
    assert {t["name"].split(" (")[0]: t["line"]["color"] for t in figures(added)[0]["data"]}["USD/LKR"] == "token:series-2"   # the free colour
    client.get(url, {"toggle": "nonsense:NOPE"})
    assert "nonsense:NOPE" not in client.session["compare_items"]
    preset = text_of(client.get(url, {"preset": 4}))           # crypto: only what's collected stays in the list
    assert {t["name"].split(" (")[0] for t in figures(preset)[0]["data"]} <= {"BTC", "ETH", "SOL", "XRP", "BNB"}
    assert text_of(client.get(url, {"preset": 99}))           # a wrong number changes nothing


def test_symbol_side_panel(market):
    client = market
    panel = text_of(client.get(reverse("insights_symbol"), {"class": "index", "symbol": "^GSPC", "days": 100}))
    fig = figures(panel)[0]
    assert len(fig["data"][0]["y"]) > 80 and "Previous close" in panel and "points · high" in panel
    # "S&P SL20" has a space and an ampersand: the links must carry it safely
    assert "symbol=S%26P+SL20" in text_of(client.get(reverse("insights_markets")))
    empty = text_of(client.get(reverse("insights_symbol"), {"class": "crypto", "symbol": "NOPE"}))
    assert "Not enough history for this period yet" in empty


# ── vehicles ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_vehicles_tab(dash, tmp_path, monkeypatch):
    client, _ = dash
    monkeypatch.setenv("SCRAPERS_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.delenv("SCRAPERS_NO_DB", raising=False)
    from sample_data import build_vehicles
    build_vehicles(runs=3)
    s = read(queries.vehicles_summary)
    c = s["counts"]
    assert (c["ads"], c["new_last_run"], c["price_drops"]) == (7, 2, 1)        # 4 ikman + 3 riyasewana; 1 new each in last run
    assert sum(r["n"] for r in s["price_ranges"]) == 7 and s["makes"][0]["n"] >= 1
    assert all(r["make"][0].isupper() for r in s["makes"])
    titles = [a["title"] for a in read(queries.vehicle_ads)["items"]]
    assert set(titles[:2]) == {"Nissan Leaf 2014", "Mitsubishi Montero 2010"}  # newest first (sub-second order too)
    vezel = read(queries.vehicle_ads, q="vezel")["items"][0]
    assert (vezel["price_lkr"], vezel["previous_price_lkr"], vezel["times_seen"]) == (9_100_000, 9_350_000, 3)
    assert read(queries.vehicle_price_changes)[0]["title"] == "Honda Vezel 2016"
    html = text_of(client.get(reverse("insights_vehicles")))
    assert kpi_values(html)[:4] == ["7", "2", kpi_values(html)[2], "1"] and "7 · newest first" in html
    assert "was Rs 9,350,000" in html and "Honda Vezel 2016" in html
    new = text_of(client.get(reverse("insights_vehicle_ads"), {"new": 1}))
    assert new.count('class="ad"') == 2 and "Nissan Leaf 2014" in new and "Honda Vezel" not in new
    riya = client.get(reverse("insights_vehicle_ads"), {"site": "riyasewana"})
    assert text_of(riya).count('class="ad"') == 3 and json.loads(riya["HX-Trigger"])["listCount"] == "3 · newest first"


# ── nothing yet, or something wrong ──────────────────────────────────────────────────────────────────────────
def test_tabs_say_what_to_run_when_no_tool_has_run_yet(pg_env, client):
    from warehouse import postgres
    postgres.setup()                                   # the database exists, but no tool has made its tables yet
    client.force_login(make_user("ada", "Administrator"))
    for name, command in (("insights_news", "python -m worldnews run"), ("insights_tech", "python -m techintel run"),
                          ("insights_markets", "python -m markets run"), ("insights_vehicles", "./run_all.sh vehicles"),
                          ("insights_companies", "python -m companies collect")):
        assert command in text_of(client.get(reverse(name)))


def test_tabs_say_what_to_do_when_postgres_is_down(client, monkeypatch):
    monkeypatch.setenv("CRAWL4AI_READER_URL", "postgresql://nobody@127.0.0.1:1/crawl4ai")
    client.force_login(make_user("ada", "Administrator"))
    html = text_of(client.get(reverse("insights_companies")))
    assert "Can&#x27;t reach PostgreSQL" in html and "brew services start" in html
    with Reader() as db:
        assert db.get(queries.companies_summary) is None and "Can't reach PostgreSQL" in db.error


def test_last_collection_run(client, monkeypatch, tmp_path):
    import project_paths
    monkeypatch.setattr(project_paths, "REPO_ROOT", tmp_path)
    assert last_runs() == []
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "history.log").write_text(
        "2026-11-02 09:00:01  ok  total 4m02s | markets ok 20s | news ok 1m10s | tech ok 2m32s  log: logs/run-1.log\n"
        "2026-11-02 10:00:01  1 step(s) failed  total 3m | markets FAILED(exit 1) 5s  log: logs/run-2.log\n"
        "2026-11-02 10:00:30  skipped: another run is still going (pid 42)\n")
    assert [(r["when"], r["ok"], r["skipped"]) for r in last_runs()] == [
        ("2026-11-02 10:00:30", False, True), ("2026-11-02 10:00:01", False, False), ("2026-11-02 09:00:01", True, False)]


# ── rights ───────────────────────────────────────────────────────────────────────────────────────────────────
def dataset(tab_code: str) -> Dataset:
    tab = next(t for t in TABS if t.code == tab_code)
    return Dataset.objects.create(schema=tab.schema, name=tab.table, label=tab.label)


def test_pages_need_a_sign_in_and_the_menu(client):
    for name in ("dashboard", "insights_news", "insights_news_items", "insights_markets", "insights_symbol"):
        res = client.get(reverse(name))
        assert res.status_code == 302 and res.url.startswith(reverse("login"))
    nobody = make_user("nobody")                        # no role at all
    client.force_login(nobody)
    assert client.get(reverse("dashboard")).status_code == 403
    assert client.get(reverse("insights_news")).status_code == 403


def test_a_tab_follows_the_rights_of_its_dataset(pg_env, client):
    """Taking a dataset away in the catalog closes the tab too, or the dashboard would be a way around the rights."""
    from warehouse import postgres
    postgres.setup()
    viewer = Group.objects.get(name="Viewer")
    client.force_login(make_user("vic", "Viewer"))
    assert "Your roles don't include the data" in text_of(client.get(reverse("dashboard")))
    assert client.get(reverse("insights_companies")).status_code == 403
    assert client.get(reverse("insights_company_rows")).status_code == 403
    assert client.get(reverse("insights_company", args=["x"])).status_code == 403
    ds = dataset("companies")
    DatasetRight.objects.create(dataset=ds, group=viewer, can_view=True)
    client.force_login(User.objects.get(username="vic"))     # rights are read once per request's user
    res = client.get(reverse("dashboard"))
    assert res.status_code == 302 and res.url == reverse("insights_companies")
    html = text_of(client.get(reverse("insights_companies")))
    assert ">Companies</a>" in html and ">World News</a>" not in html        # other tabs stay hidden
    assert client.get(reverse("insights_news")).status_code == 403
    assert client.get(reverse("insights_news_analysis")).status_code == 403
    ds.enabled = False
    ds.save()
    assert client.get(reverse("insights_companies")).status_code == 403      # switched off: administrators only


def test_markets_news_panels_need_the_news_tab(dash):
    from sample_data import build_markets
    client, dirs = dash
    build_markets(dirs["markets"].parent)
    assert "Market news" in text_of(client.get(reverse("insights_markets")))  # the administrator sees them
    DatasetRight.objects.create(dataset=dataset("markets"), group=Group.objects.get(name="Viewer"), can_view=True)
    client.force_login(make_user("vic", "Viewer"))
    assert "Market news" not in text_of(client.get(reverse("insights_markets")))


# ── how things are drawn ─────────────────────────────────────────────────────────────────────────────────────
def test_numbers_look_like_on_the_old_dashboard():
    assert ui.format_price(1234.5, "index") == "1,234.50" and ui.format_price(1.08321, "fx") == "1.0832"
    assert ui.format_price(0.000123456, "crypto") == "0.0001235" and ui.format_price(None) == "–"
    assert ui.big(1_530_000_000_000) == "1.53T" and ui.big(40_180_000_000) == "40.18B" and ui.big(950) == "950"
    assert ui.lkr(4300000) == "Rs 4,300,000" and ui.lkr(None) == "–" and ui.num(None) == "0"
    assert 'class="up">+1.50%' in ui.pct(1.5) and 'class="down">-2.00%' in ui.pct(-2) and "–" in ui.pct(None)
    assert ui.when("2026-11-02T08:15:00Z") == "2026-11-02 13:45"              # Sri Lanka time
    assert ui.day("2026-11-02T20:15:00Z") == "2026-11-03" and ui.when("not a date") == "" and ui.when(None) == ""
    now = timezone.now()
    assert ui.ago((now - timezone.timedelta(minutes=5)).isoformat()) == "5m ago"
    assert ui.ago((now - timezone.timedelta(hours=5)).isoformat()) == "5h ago"
    assert ui.ago((now - timezone.timedelta(days=5)).isoformat()) == "5d ago"
    assert "NEW" in ui.domain_age((now - timezone.timedelta(days=3)).isoformat()) and "3 days" in ui.domain_age((now - timezone.timedelta(days=3)).isoformat())
    assert ui.domain_age((now - timezone.timedelta(days=800)).isoformat()) == "2 years" and "–" in ui.domain_age(None)


def test_links_from_collected_pages_are_only_web_addresses():
    """Collected text can say anything, and 10 people read it: a page's "website" must never run a script."""
    assert ui.safe_url("https://a.lk/x") == "https://a.lk/x" and ui.safe_url("javascript:alert(1)") == "" and ui.safe_url(None) == ""
    assert "<a" not in ui.outlink("javascript:alert(1)", "click") and ui.outlink("javascript:alert(1)", "click") == "click"
    assert ui.outlink("https://a.lk/x", "<b>x</b>") == '<a href="https://a.lk/x" target="_blank" rel="noopener noreferrer">&lt;b&gt;x&lt;/b&gt;</a>'
    assert 'javascript' not in ui.social_links({"linkedin": "javascript:alert(1)", "x": "https://x.com/a"}) and "x.com/a" in ui.social_links('{"x": "https://x.com/a"}')
    assert ui.link_or_text("javascript:alert(1)") == "javascript:alert(1)" and "<a" in ui.link_or_text("https://a.lk")
    assert ui.chart({"data": [], "layout": {"title": "</script><script>alert(1)</script>"}}).count("<script") == 1


def test_chart_figures():
    today = timezone.localdate().isoformat()
    fig = charts.day_columns([{"day": today, "n": 10, "tech": 3}, {"day": "2000-01-01", "n": 99, "tech": 9}], "n", "tech", "tech", "other")
    assert [t["name"] for t in fig["data"]] == ["tech", "other"] and fig["layout"]["barmode"] == "stack"
    assert (fig["data"][0]["y"][-1], fig["data"][1]["y"][-1]) == (3, 7) and sum(fig["data"][1]["y"]) == 7      # older days aren't shown
    single = charts.day_columns([], "n", rest_label="new ads")
    assert len(single["data"]) == 1 and single["layout"]["showlegend"] is False
    assert charts.lines([{"name": "a", "slot": 0, "x": ["2026-11-01"], "y": [1]}], empty="Too little.") == {"empty": "Too little."}
    two = charts.lines([{"name": "a", "slot": 1, "x": ["2026-11-01", "2026-11-02"], "y": [1, 2]}, {"name": "b", "slot": 0, "x": [], "y": []}], percent=True)
    assert len(two["data"]) == 1 and two["data"][0]["line"]["color"] == "token:series-2" and two["layout"]["yaxis"]["ticksuffix"] == "%"
    assert charts.local_time("2026-11-02T08:15:00Z") == "2026-11-02 13:45:00"
    assert 'role="img"' in ui.chart(two, "A line") and ui.chart(None) == "" and "Too little." in ui.chart({"empty": "Too little."})


def test_news_hours_are_shown_in_local_time():
    from webapp.insights.news import _hours
    hours = _hours([{"hour": 23, "n": 4}, {"hour": 0, "n": 2}])      # +05:30: 23:00 UTC is 04:30, 00:00 UTC is 05:30
    assert hours[4] == 4 and hours[5] == 2 and sum(hours) == 6
