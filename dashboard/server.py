"""Local web dashboard for world news, tech intel, markets, vehicles and companies, reading live from the DuckDB files.

    python -m dashboard                 http://127.0.0.1:8050
    python -m dashboard --port 9000 --open

Databases are opened read-only for each request and closed straight away, so the
dashboard never blocks a running collection. If a run is writing at that moment,
the last loaded data is shown with "busy": true.
"""

import argparse
import logging
import time
import webbrowser
from pathlib import Path

import duckdb
from aiohttp import web

import project_paths
from warehouse import DB_FILES

from . import queries

project_paths.apply_local_env()
STATIC = Path(__file__).resolve().parent / "static"
logger = logging.getLogger("dashboard")
_cache: dict[str, object] = {}


def _int(request, name, default, lo=1, hi=100000):
    try:
        return max(lo, min(hi, int(request.query.get(name, default))))
    except ValueError:
        return default


def _query(db: str, fn, cache_key: str):
    """Run fn(connection) on a database, read-only, with a short retry and a cached fallback."""
    path = DB_FILES[db]
    if not path.exists():
        return {"empty": True, "data": None}
    deadline = time.monotonic() + 1.5
    while True:
        try:
            con = duckdb.connect(str(path), read_only=True)
            break
        except duckdb.Error as e:
            if "lock" in str(e).lower() and time.monotonic() < deadline:
                time.sleep(0.3)
                continue
            if cache_key in _cache:
                return {"busy": True, "data": _cache[cache_key]}
            return {"busy": True, "data": None, "error": "A run is writing to the database; try again in a moment."}
    try:
        con.execute("set TimeZone = 'UTC'")
        data = fn(con)
    finally:
        con.close()
    _cache[cache_key] = data
    return {"data": data}


def _news_groups():
    try:
        from worldnews.settings import load_settings
        return load_settings().groups
    except Exception:  # noqa: BLE001
        return []


async def news_summary(request):
    days = _int(request, "days", 7)
    result = _query("news", lambda c: queries.news_summary(c, days), f"news_summary:{days}")
    result["groups"] = _news_groups()
    return web.json_response(result)


async def news_items(request):
    q = request.query
    args = dict(group=q.get("group") or None, source=q.get("source") or None, tech=q.get("tech") == "1",
                topic=q.get("topic") or None,
                new=q.get("new") == "1", q=(q.get("q") or "").strip() or None, days=_int(request, "days", 7),
                limit=_int(request, "limit", 100, hi=1000), offset=_int(request, "offset", 0, lo=0))
    return web.json_response(_query("news", lambda c: queries.news_items(c, **args), f"news_items:{sorted(args.items())}"))


async def news_analysis(request):
    days = _int(request, "days", 7)
    groups = [g for g in (request.query.get("group") or "").upper().split(",") if g] or None
    return web.json_response(_query("news", lambda c: queries.news_analysis(c, days, groups), f"news_analysis:{days}:{groups}"))


async def news_sources(request):
    return web.json_response(_query("news", queries.news_sources, "news_sources"))


async def news_runs(request):
    return web.json_response(_query("news", queries.news_runs, "news_runs"))


async def tech_summary(request):
    days = _int(request, "days", 30)
    return web.json_response(_query("tech", lambda c: queries.tech_summary(c, days), f"tech_summary:{days}"))


async def tech_domains(request):
    q = request.query
    args = dict(status=q.get("status") or None, category=q.get("category") or None, source=q.get("source") or None,
                q=(q.get("q") or "").strip() or None, days=_int(request, "days", 30),
                limit=_int(request, "limit", 100, hi=1000), offset=_int(request, "offset", 0, lo=0),
                sort=q.get("sort") if q.get("sort") in queries.TECH_SORT else "seen",
                company=q.get("company") if q.get("company") in queries.TECH_COMPANY else None)
    return web.json_response(_query("tech", lambda c: queries.tech_domains(c, **args), f"tech_domains:{sorted(args.items())}"))


async def tech_domain(request):
    domain = request.match_info["domain"]
    return web.json_response(_query("tech", lambda c: queries.tech_domain(c, domain), f"tech_domain:{domain}"))


async def tech_changes(request):
    days = _int(request, "days", 30)
    return web.json_response(_query("tech", lambda c: queries.tech_changes(c, days), f"tech_changes:{days}"))


async def tech_runs(request):
    return web.json_response(_query("tech", queries.tech_runs, "tech_runs"))


async def markets_summary(request):
    return web.json_response(_query("markets", queries.markets_summary, "markets_summary"))


async def markets_quotes(request):
    cls = request.query.get("class") or "index"
    q = (request.query.get("q") or "").strip() or None
    return web.json_response(_query("markets", lambda c: queries.markets_quotes(c, cls, q), f"markets_quotes:{cls}:{q}"))


async def markets_history(request):
    cls, sym = request.query.get("class") or "", request.query.get("symbol") or ""
    days = _int(request, "days", 90)
    return web.json_response(_query("markets", lambda c: queries.markets_history(c, cls, sym, days), f"mh:{cls}:{sym}:{days}"))


async def markets_compare(request):
    """?items=index:^GSPC,commodity:GC=F,crypto:BTC&days=30"""
    items = []
    for part in (request.query.get("items") or "").split(","):
        cls, _, sym = part.partition(":")
        if cls and sym:
            items.append((cls, sym))
    days = _int(request, "days", 30, hi=3650)
    key = f"mcmp:{items}:{days}"
    return web.json_response(_query("markets", lambda c: queries.markets_compare(c, items, days), key))


async def markets_performance(request):
    return web.json_response(_query("markets", queries.markets_performance, "markets_performance"))


async def vehicles_summary(request):
    days = _int(request, "days", 7)
    return web.json_response(_query("vehicles", lambda c: queries.vehicles_summary(c, days), f"veh_summary:{days}"))


async def vehicle_ads(request):
    q = request.query
    args = dict(site=q.get("site") or None, q=(q.get("q") or "").strip() or None, new=q.get("new") == "1",
                days=_int(request, "days", 30), limit=_int(request, "limit", 100, hi=1000),
                offset=_int(request, "offset", 0, lo=0))
    return web.json_response(_query("vehicles", lambda c: queries.vehicle_ads(c, **args), f"veh_ads:{sorted(args.items())}"))


async def vehicle_changes(request):
    days = _int(request, "days", 30)
    return web.json_response(_query("vehicles", lambda c: queries.vehicle_price_changes(c, days), f"veh_changes:{days}"))


async def companies_summary(request):
    return web.json_response(_query("companies", queries.companies_summary, "companies_summary"))


async def companies_list(request):
    q = request.query
    args = dict(country=(q.get("country") or "").upper() or None, source=q.get("source") or None,
                status=q.get("status") if q.get("status") in queries.COMPANY_FILTERS else None,
                q=(q.get("q") or "").strip() or None,
                sort=q.get("sort") if q.get("sort") in queries.COMPANY_SORT else "best",
                limit=_int(request, "limit", 100, hi=1000), offset=_int(request, "offset", 0, lo=0))
    return web.json_response(_query("companies", lambda c: queries.companies_list(c, **args), f"co_list:{sorted(args.items())}"))


async def company_detail(request):
    cid = request.match_info["id"]
    return web.json_response(_query("companies", lambda c: queries.company_detail(c, cid), f"co:{cid}"))


async def automation(request):
    """Last lines of logs/history.log written by run_all.sh (manual and scheduled runs)."""
    path = project_paths.REPO_ROOT / "logs" / "history.log"
    lines = path.read_text(encoding="utf-8").splitlines()[-10:] if path.exists() else []
    runs = []
    for line in reversed(lines):
        when, _, rest = line[:19], line[19:21], line[21:]
        runs.append({"when": when, "ok": rest.startswith("ok"), "skipped": rest.startswith("skipped"), "text": rest.strip()})
    return web.json_response({"runs": runs})


async def index(request):
    """The page, with each script/stylesheet link carrying the file's change time, so the browser never
    mixes an old cached app.js with a new page after an update."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "style.css"):
        html = html.replace(f"/static/{name}", f"/static/{name}?v={int((STATIC / name).stat().st_mtime)}")
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})


def build_app() -> web.Application:
    app = web.Application()
    app.add_routes([
        web.get("/", index),
        web.get("/api/news/summary", news_summary),
        web.get("/api/news/items", news_items),
        web.get("/api/news/sources", news_sources),
        web.get("/api/news/analysis", news_analysis),
        web.get("/api/news/runs", news_runs),
        web.get("/api/tech/summary", tech_summary),
        web.get("/api/tech/domains", tech_domains),
        web.get("/api/tech/domain/{domain}", tech_domain),
        web.get("/api/tech/changes", tech_changes),
        web.get("/api/tech/runs", tech_runs),
        web.get("/api/automation", automation),
        web.get("/api/vehicles/summary", vehicles_summary),
        web.get("/api/vehicles/ads", vehicle_ads),
        web.get("/api/vehicles/changes", vehicle_changes),
        web.get("/api/markets/summary", markets_summary),
        web.get("/api/markets/quotes", markets_quotes),
        web.get("/api/markets/history", markets_history),
        web.get("/api/markets/compare", markets_compare),
        web.get("/api/markets/performance", markets_performance),
        web.get("/api/companies/summary", companies_summary),
        web.get("/api/companies/list", companies_list),
        web.get("/api/companies/company/{id}", company_detail),
        web.static("/static", STATIC),
    ])
    return app


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m dashboard", description="Dashboard for worldnews and techintel.")
    p.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = only this computer (default)")
    p.add_argument("--port", type=int, default=8050)
    p.add_argument("--open", action="store_true", help="Open the dashboard in the browser")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}/"
    print(f"Dashboard: {url}   (Ctrl+C to stop)")
    for name, path in DB_FILES.items():
        if name != "scrapers":
            print(f"  {name:<5} {path}{'' if path.exists() else '  (no data yet)'}")
    if args.open:
        webbrowser.open(url)
    web.run_app(build_app(), host=args.host, port=args.port, print=None, access_log=None)
