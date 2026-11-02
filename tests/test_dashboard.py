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
