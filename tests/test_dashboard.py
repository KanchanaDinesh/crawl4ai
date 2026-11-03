"""Dashboard API against sample databases built through the real stores."""

import asyncio
import importlib

import duckdb
import pytest
from aiohttp.test_utils import TestClient, TestServer

from sample_data import build_news, build_tech


@pytest.fixture
def dash(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJECT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("WORLDNEWS_DATA_DIR", raising=False)
    monkeypatch.delenv("TECHINTEL_DATA_DIR", raising=False)
    monkeypatch.delenv("MARKETS_DATA_DIR", raising=False)
    import project_paths
    import warehouse
    import dashboard.server as server
    for mod in (project_paths, warehouse, server):
        importlib.reload(mod)
    from worldnews.settings import load_settings
    build_news(warehouse.DB_FILES["news"].parent, load_settings().raw["tech_keywords"])
    build_tech(warehouse.DB_FILES["tech"].parent)
    server._cache.clear()
    return server, warehouse.DB_FILES


def call(server, *paths):
    async def go():
        async with TestClient(TestServer(server.build_app())) as client:
            out = []
            for path in paths:
                resp = await client.get(path)
                assert resp.status == 200, path
                out.append(await resp.json() if path.startswith("/api") else await resp.text())
            return out
    return asyncio.run(go())


def test_news_endpoints(dash):
    server, _ = dash
    summary, items, lk, tech, new, search, sources, runs, page = call(
        server, "/api/news/summary?days=7", "/api/news/items?limit=5", "/api/news/items?group=LK",
        "/api/news/items?tech=1", "/api/news/items?new=1", "/api/news/items?q=chip", "/api/news/sources",
        "/api/news/runs", "/")
    s = summary["data"]
    assert s["totals"]["headlines"] == 31 and s["last_run"]["sources_ok"] == 12
    assert [g["code"] for g in summary["groups"]][:3] == ["US", "IN", "LK"]
    times = [i["published_at"] for i in items["data"]["items"]]
    assert times == sorted(times, reverse=True) and items["data"]["total"] == 31
    assert lk["data"]["total"] == 6 and all(i["group_code"] == "LK" for i in lk["data"]["items"])
    assert tech["data"]["total"] == 11 and new["data"]["total"] == 12 and search["data"]["total"] == 2
    reuters = next(r for r in sources["data"] if r["source"] == "Reuters")
    assert reuters["status"] == "failed" and reuters["failed_streak"] == 2
    assert len(runs["data"]) == 2 and "Intelligence Dashboard" in page
    assert {"topics_by_day", "by_hour"} <= set(s) and all(0 <= r["hour"] < 24 for r in s["by_hour"])
    assert "/static/app.js?v=" in page and "/static/style.css?v=" in page      # no stale cached script after updates


def test_tech_endpoints(dash):
    server, _ = dash
    summary, domains, qualified, search, detail, changes, runs = call(
        server, "/api/tech/summary?days=30", "/api/tech/domains?days=30", "/api/tech/domains?status=qualified",
        "/api/tech/domains?q=bakery", "/api/tech/domain/agentforge.dev", "/api/tech/changes", "/api/tech/runs")
    c = summary["data"]["counts"]
    assert (c["candidates"], c["qualified"], c["watch"], c["rejected"], c["profiled"]) == (9, 5, 1, 2, 5)
    seen = [d["seen_at"] for d in domains["data"]["items"]]
    assert seen == sorted(seen, reverse=True)                                  # latest first
    assert {d["status"] for d in domains["data"]["items"]} == {"qualified", "watch", "rejected", "not checked"}
    assert qualified["data"]["total"] == 5 and search["data"]["items"][0]["domain"] == "kandy-bakery.lk"
    d = detail["data"]
    assert d["profile"]["tech_stack"] == ["Next.js", "Vercel", "PostHog"] and d["registration"]["registered_at"]
    assert d["changes"][0]["after"] == "Stripe"                               # no JSON quotes
    assert changes["data"][0]["domain"] == "agentforge.dev" and len(runs["data"]) == 6
    s = summary["data"]
    assert sum(r["n"] for r in s["domain_age"]) == c["qualified"] and "new_domains_30d" in c
    assert all("qualified" in r for r in s["by_day"])
    by_reg, by_score = call(server, "/api/tech/domains?sort=registered", "/api/tech/domains?sort=score")
    regs = [d["registered_at"] for d in by_reg["data"]["items"] if d["registered_at"]]
    assert regs == sorted(regs, reverse=True)                                  # newest domain first
    scores = [d["score"] for d in by_score["data"]["items"] if d["score"] is not None]
    assert scores == sorted(scores, reverse=True)


def test_tech_company_facts(dash):
    server, db_files = dash
    summary, linkedin, social, detail = call(server, "/api/tech/summary", "/api/tech/domains?company=linkedin",
                                             "/api/tech/domains?company=any", "/api/tech/domain/agentforge.dev")
    c = summary["data"]["counts"]
    assert (c["companies"], c["with_linkedin"], c["with_people"]) == (2, 1, 1)
    assert {r["platform"] for r in summary["data"]["social_platforms"]} == {"linkedin", "x", "github", "facebook"}
    row, = linkedin["data"]["items"]
    assert row["domain"] == "agentforge.dev" and row["people"] == 1 and row["employees"] == "11-50"
    assert social["data"]["total"] == 2
    assert detail["data"]["company"]["github"]["public_repos"] == 12

    con = duckdb.connect(str(db_files["tech"]))         # a database from before `techintel company`
    con.execute("drop view v_companies")
    con.close()
    server._cache.clear()
    summary, domains, detail = call(server, "/api/tech/summary", "/api/tech/domains?company=linkedin",
                                    "/api/tech/domain/agentforge.dev")
    assert "companies" not in summary["data"]["counts"] and summary["data"]["social_platforms"] == []
    assert domains["data"]["total"] == 0 and detail["data"]["company"] is None


def test_busy_database_serves_last_data(dash):
    server, db_files = dash
    first, = call(server, "/api/news/runs")
    writer = duckdb.connect(str(db_files["news"]))                              # a run is "writing"
    try:
        busy, = call(server, "/api/news/runs")
        assert busy["busy"] is True and busy["data"] == first["data"]
    finally:
        writer.close()
    again, = call(server, "/api/news/runs")
    assert "busy" not in again


def test_empty_databases(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJECT_DATA_DIR", str(tmp_path / "nothing"))
    import project_paths
    import warehouse
    import dashboard.server as server
    for mod in (project_paths, warehouse, server):
        importlib.reload(mod)
    server._cache.clear()
    news, tech = call(server, "/api/news/summary", "/api/tech/summary")
    assert news["empty"] and news["data"] is None and tech["empty"]


def test_markets_endpoints(dash):
    server, db_files = dash
    from sample_data import build_markets
    build_markets(db_files["markets"].parent)
    summary, crypto_rows, lk, metals_lkr, search, hist, page_js = call(
        server, "/api/markets/summary", "/api/markets/quotes?class=crypto", "/api/markets/quotes?class=stock_lk",
        "/api/markets/quotes?class=commodity_lkr", "/api/markets/quotes?class=stock_us&q=apple",
        "/api/markets/history?class=index&symbol=ASPI&days=30", "/static/app.js")
    s = summary["data"]
    cards = {c["symbol"]: c for c in s["cards"]}
    assert {"^GSPC", "ASPI", "S&P SL20", "GC=F", "BTC", "LKR=X", "GOLD24K_LKR_PAWN"} <= set(cards)
    assert len(cards["ASPI"]["spark"]) == 6                                  # one point per saved run
    assert [r["rank"] for r in crypto_rows["data"]] == [1, 2, 3, 4, 5, 6]
    assert len(lk["data"]) == 4 and all(r["currency"] == "LKR" for r in lk["data"])
    assert {r["symbol"] for r in metals_lkr["data"]} == {"GOLD_LKR_G", "GOLD24K_LKR_PAWN", "GOLD22K_LKR_PAWN", "SILVER_LKR_G"}
    assert [r["symbol"] for r in search["data"]] == ["AAPL"]
    assert len(hist["data"]["points"]) == 6 and hist["data"]["quote"]["symbol"] == "ASPI"
    assert [x["source"] for x in s["sources"] if not x["ok"]] == ["yahoo BRK-B"]
    assert "loadMarkets" in page_js
    # History: Yahoo's daily closes before the first saved run, then every run's price
    long = call(server, "/api/markets/history?class=index&symbol=%5EGSPC&days=100")[0]["data"]["points"]
    assert len(long) > 80 and long[0]["t"] < long[-1]["t"] and long[0]["t"].endswith("T12:00:00Z")
    perf, cmp = call(server, "/api/markets/performance",
                     "/api/markets/compare?items=index:%5EGSPC,index:ASPI,crypto:NOPE&days=30")
    rows = {r["symbol"]: r for r in perf["data"]}
    assert rows["^GSPC"]["1m"] is not None and rows["^GSPC"]["3m"] is not None   # from the daily closes
    assert rows["ASPI"]["1m"] is None and rows["ASPI"]["1d"] is not None          # CSE: only today's runs
    assert {"BTC", "GC=F", "LKR=X"} <= set(rows) and "AAPL" not in rows
    assert [s["symbol"] for s in cmp["data"]] == ["^GSPC", "ASPI"]                # unknown ones are skipped


def test_news_topic_and_multi_group_filters(dash):
    server, _ = dash
    tech, both = call(server, "/api/news/items?topic=tech", "/api/news/items?group=US,LK")
    assert tech["data"]["total"] == 11
    assert both["data"]["total"] == 13 and {i["group_code"] for i in both["data"]["items"]} == {"US", "LK"}


def test_automation_history(dash, monkeypatch, tmp_path):
    server, _ = dash
    import project_paths
    monkeypatch.setattr(project_paths, "REPO_ROOT", tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "history.log").write_text(
        "2026-11-02 09:00:01  ok  total 4m02s | markets ok 20s | news ok 1m10s | tech ok 2m32s  log: logs/run-1.log\n"
        "2026-11-02 10:00:01  1 step(s) failed  total 3m | markets FAILED(exit 1) 5s  log: logs/run-2.log\n"
        "2026-11-02 10:00:30  skipped: another run is still going (pid 42)\n")
    data, = call(server, "/api/automation")
    assert [(r["when"], r["ok"], r["skipped"]) for r in data["runs"]] == [
        ("2026-11-02 10:00:30", False, True), ("2026-11-02 10:00:01", False, False), ("2026-11-02 09:00:01", True, False)]


def test_vehicles_endpoints(dash, tmp_path, monkeypatch):
    server, _ = dash
    monkeypatch.setenv("SCRAPERS_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.delenv("SCRAPERS_NO_DB", raising=False)
    from sample_data import build_vehicles
    build_vehicles(runs=3)
    import importlib, warehouse, dashboard.server as srv
    importlib.reload(warehouse); importlib.reload(srv); srv._cache.clear()
    summary, ads, new, riya, search, changes = call(
        srv, "/api/vehicles/summary", "/api/vehicles/ads", "/api/vehicles/ads?new=1",
        "/api/vehicles/ads?site=riyasewana", "/api/vehicles/ads?q=vezel", "/api/vehicles/changes")
    c = summary["data"]["counts"]
    assert (c["ads"], c["new_last_run"], c["price_drops"]) == (7, 2, 1)       # 4 ikman + 3 riyasewana; 1 new each in last run
    titles = [a["title"] for a in ads["data"]["items"]]
    assert set(titles[:2]) == {"Nissan Leaf 2014", "Mitsubishi Montero 2010"}   # newest first (sub-second order too)
    assert {a["title"] for a in new["data"]["items"]} == {"Nissan Leaf 2014", "Mitsubishi Montero 2010"}
    assert riya["data"]["total"] == 3 and all(a["site"] == "riyasewana" for a in riya["data"]["items"])
    vezel = search["data"]["items"][0]
    assert (vezel["price_lkr"], vezel["previous_price_lkr"], vezel["times_seen"]) == (9_100_000, 9_350_000, 3)
    assert changes["data"][0]["title"] == "Honda Vezel 2016"
    s = summary["data"]
    assert sum(r["n"] for r in s["price_ranges"]) == 7 and s["makes"][0]["n"] >= 1
    assert all(r["make"][0].isupper() for r in s["makes"])
