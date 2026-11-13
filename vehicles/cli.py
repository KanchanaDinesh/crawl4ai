"""python -m vehicles <command>

    run      [--site ikman] [--category motorcycle] [--limit 20] [--max-seconds 120] [--save-html] [--print]
             scrape the latest ads from every enabled listing (cars and motorcycles) and save them
    show     [--site ikman] [--category car|motorcycle] [--new] [--search vezel] [--limit 20]
             latest saved ads, newest first (--new: only ads first seen in the last run)
    changes  [--category motorcycle] [--days 30] [--limit 20]   ads whose price changed between runs
    sites                                   list the configured listings

Sites and selectors: vehicles/config/sites.json
Output: output/vehicles_<site>.json (latest run), history in PostgreSQL (schema vehicles)
"""

import argparse
import asyncio
import json
import logging
import os
import sys

from .scrape import SITE_SECONDS, listing_label, load_sites, run

os.environ.setdefault("CRAWL4AI_NO_CLOUD_NOTICE", "1")
logger = logging.getLogger("vehicles")


def _pick_sites(names: list[str] | None, config: str | None, category: str | None = None) -> list[dict]:
    """Every enabled listing, or the named sites' listings (enabled or not); --category keeps one kind."""
    sites = load_sites(config)
    if names:
        wanted = {n.lower() for n in names}
        picked = [s for s in sites if s["name"].lower() in wanted]
        if not picked:
            sys.exit(f"No site named {', '.join(names)}. Sites: {', '.join(dict.fromkeys(s['name'] for s in sites))}")
    else:
        picked = [s for s in sites if s.get("enabled", True)]
    if category:
        kinds = sorted({s.get("category") or "" for s in picked} - {""})
        picked = [s for s in picked if s.get("category") == category]
        if not picked:
            sys.exit(f"No listing with category {category}. Categories: {', '.join(kinds)}")
    return picked


def cmd_run(args) -> None:
    sites = _pick_sites(args.site, args.config, args.category)
    try:
        reports = asyncio.run(run(sites, args.limit, args.save_html, quiet=not args.print,
                                  max_seconds=args.max_seconds))
    except RuntimeError as e:
        logger.error("%s", e)
        raise SystemExit(1) from None
    total = sum(r.get("count", 0) for r in reports)
    logger.info("Done: %d ads from %d site(s): %s", total, len(reports),
                ", ".join(f"{_label(r)} {r.get('count', 0)}" for r in reports))
    if total == 0:
        why = "; ".join(f"{_label(r)}: {_short(r.get('error')) or '0 ads matched (see output/html_dumps/)'}"
                        for r in reports)
        logger.error("No ads from any site. %s", why)
        raise SystemExit(1)


def _label(report: dict) -> str:
    return listing_label({"name": report.get("site"), "category": report.get("category")})


def _short(error: str | None) -> str:
    """The useful line of a long crawl4ai error (it adds code context and call logs)."""
    lines = [ln.strip() for ln in (error or "").splitlines() if ln.strip()]
    for wanted in (lambda ln: "net::" in ln or ln.startswith("Page.goto") or "gave up" in ln or "browser" in ln.lower(),
                   lambda ln: ln.startswith("Error:")):
        for line in lines:
            if wanted(line):
                return line.removeprefix("Error: ")[:200]
    return lines[0][:200] if lines else ""


def _db():
    """PostgreSQL, read-only login (works while a run is saving)."""
    from warehouse.postgres import DatabaseError, connect
    try:
        con = connect("reader", autocommit=True)
    except DatabaseError as e:
        sys.exit(str(e))
    con.execute("set timezone = 'UTC'")
    if not con.execute("select to_regclass('vehicles.ads') is not null").fetchone()[0]:
        sys.exit("No vehicle ads saved yet. Run:  uv run python -m vehicles run")
    return con


def _price(v) -> str:
    return "–" if v is None else f"Rs {v:,.0f}"


def cmd_show(args) -> None:
    from webapp.insights import queries
    con = _db()
    try:
        data = queries.vehicle_ads(con, site=args.site, category=args.category, q=args.search, new=args.new, days=args.days,
                                   limit=args.limit)
    finally:
        con.close()
    if not data["items"]:
        print("No ads match.")
        return
    for ad in data["items"]:
        new = "NEW " if ad["is_new"] else "    "
        was = f" (was {_price(ad['previous_price_lkr'])})" if ad.get("previous_price_lkr") else ""
        extra = " · ".join(x for x in (ad.get("year"), ad.get("mileage"), ad.get("location")) if x)
        print(f"{new}{ad['first_seen'][:16].replace('T', ' ')}  {ad['site']:<11} {_kind(ad):<11} "
              f"{_price(ad['price_lkr']):>15}{was}  {ad['title']}")
        print(f"{'':<46}{extra + '  ' if extra else ''}{ad['url']}")
    print(f"\n{len(data['items'])} of {data['total']} ads")


def _kind(ad: dict) -> str:
    return ad.get("category") or "unknown"


def cmd_changes(args) -> None:
    from webapp.insights import queries
    con = _db()
    try:
        items = queries.vehicle_price_changes(con, days=args.days, limit=args.limit, category=args.category)
    finally:
        con.close()
    if not items:
        print("No price changes yet.")
        return
    for c in items:
        print(f"{c['changed_at'][:16].replace('T', ' ')}  {c['site']:<11} {_kind(c):<11} {_price(c['previous_price_lkr']):>15} -> "
              f"{_price(c['price_lkr']):<15} {c['title']}\n{'':<41}{c['url']}")


def cmd_sites(args) -> None:
    for s in load_sites(args.config):
        print(f"{'on ' if s.get('enabled', True) else 'off'}  {s['name']:<12} {s.get('category') or '-':<11} "
              f"limit {s.get('limit', 10):<4} {s['url']}")


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    p = argparse.ArgumentParser(prog="python -m vehicles", description="Latest vehicle ads, cars and motorcycles (ikman.lk, riyasewana.com).",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--config", help="Sites file (default vehicles/config/sites.json)")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="scrape and save the latest ads")
    r.add_argument("--site", action="append", help="Only this site (repeatable). Default: every enabled listing.")
    r.add_argument("--category", help="Only listings of this kind: car or motorcycle.")
    r.add_argument("--limit", type=int, help="Ads per site (overrides the config's 'limit').")
    r.add_argument("--max-seconds", type=float, help=f"Give up on a site after this long (default {SITE_SECONDS}).")
    r.add_argument("--no-stealth", action="store_true", help=argparse.SUPPRESS)   # old flag; stealth is never used now
    r.add_argument("--save-html", action="store_true", help="Also save each page to output/html_dumps/.")
    r.add_argument("--print", action="store_true", help="Also print each site's JSON.")
    r.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)   # old flag; quiet is now the default

    s = sub.add_parser("show", help="latest saved ads")
    s.add_argument("--site")
    s.add_argument("--category", help="car or motorcycle (older ikman ads also three-wheeler, van, lorry, bus, ...)")
    s.add_argument("--new", action="store_true", help="Only ads first seen in the last run")
    s.add_argument("--search", help="Text in the title or location")
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--limit", type=int, default=20)

    c = sub.add_parser("changes", help="price changes between runs")
    c.add_argument("--category", help="car or motorcycle")
    c.add_argument("--days", type=int, default=30)
    c.add_argument("--limit", type=int, default=20)

    sub.add_parser("sites", help="list the configured sites")

    args = p.parse_args(argv)
    {"run": cmd_run, "show": cmd_show, "changes": cmd_changes, "sites": cmd_sites}[args.command](args)
