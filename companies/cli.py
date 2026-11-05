"""python -m companies <command>

    collect  [--max-minutes 5] [--source wikidata] [--country LK]
             add companies (name, website, country) from open data; continues where the last run stopped
    update   [--max-minutes 10] [--limit 200] [--country LK] [--domain keells.com]
             read company websites (homepage, about, team, contact): social links, people, employees, contacts
    show     [--country LK] [--search bank] [--source cse] [--limit 20]
             companies in the database
    stats    counts by country, source and website status
    sources  the sources, whether they're on, and where each one stopped

Settings: companies/config/settings.json. Database: PostgreSQL, schema companies (database crawl4ai).
"""

import argparse
import asyncio
import json
import logging
import os
import sys

from .settings import load_settings

os.environ.setdefault("CRAWL4AI_NO_CLOUD_NOTICE", "1")   # hide crawl4ai's promo banner
logger = logging.getLogger("companies")


def _countries(values: list[str] | None) -> list[str] | None:
    """--country LK --country US, or --country LK,US; "UK" means GB (the ISO code)."""
    if not values:
        return None
    codes = [c.strip().upper() for v in values for c in v.split(",") if c.strip()]
    return ["GB" if c == "UK" else c for c in codes]


def cmd_collect(args, settings) -> None:
    from .collect import collect, describe
    from .store import CompanyStore
    store = CompanyStore()
    try:
        summary = asyncio.run(collect(settings, store, args.source, _countries(args.country), args.max_minutes))
    finally:
        store.close()
    print("\nCollected:\n" + describe(summary))
    if summary.get("sources") and all(i.get("error") for i in summary["sources"].values()):
        raise SystemExit(1)


def cmd_update(args, settings) -> None:
    from .store import CompanyStore
    from .update import update
    store = CompanyStore()
    try:
        s = asyncio.run(update(settings, store, args.limit, _countries(args.country), args.domain, args.max_minutes))
    finally:
        store.close()
    print(f"\nWebsites read: {s['checked']} (ok {s.get('ok', 0)}, unreachable {s.get('unreachable', 0)}, "
          f"robots.txt says no {s.get('not_allowed', 0)}, refused {s.get('refused', 0)}) in {s['seconds']:.0f}s")
    if s.get("ok"):
        print(f"  with social links {s['with_social']}, LinkedIn {s['with_linkedin']}, people {s['with_people']}, "
              f"employees {s['with_employees']}")
    if s.get("left_for_next_run"):
        print(f"  {s['left_for_next_run']} left for the next run (time limit)")


def _db():
    """PostgreSQL, read-only login (works while collect/update are running)."""
    from warehouse.postgres import DatabaseError, connect
    try:
        con = connect("reader", autocommit=True)
    except DatabaseError as e:
        sys.exit(str(e))
    con.execute("set timezone = 'UTC'")
    exists = con.execute("select to_regclass('companies.companies') is not null").fetchone()[0]
    if not exists:
        sys.exit("No companies saved yet. Run:  uv run python -m companies collect")
    return con


def cmd_show(args, settings) -> None:
    from dashboard import queries
    con = _db()
    try:
        country = (_countries([args.country] if args.country else None) or [None])[0]
        data = queries.companies_list(con, country=country, source=args.source, q=args.search, limit=args.limit)
    finally:
        con.close()
    for c in data["items"]:
        social = ",".join(sorted(json.loads(c["social"]))) if c.get("social") else ""
        print(f"{c['country'] or '--':<3} {c['source']:<16} {c['name'][:48]:<48} {c['website'] or '':<40} "
              f"{c['site_status']:<11} {social}")
    print(f"\n{len(data['items'])} of {data['total']:,} companies")


def cmd_stats(args, settings) -> None:
    from dashboard import queries
    con = _db()
    try:
        s = queries.companies_summary(con)
    finally:
        con.close()
    c = s["counts"]
    print(f"{c['companies']:,} companies, {c['with_website']:,} with a website, {c['sites_ok']:,} websites read, "
          f"{c['countries']} countries")
    print("\nBy country (top 15):")
    for r in s["by_country"][:15]:
        print(f"  {r['country'] or '--':<4} {r['name'] or '':<26} {r['n']:>9,}  ({r['with_website']:,} with a website)")
    print("\nBy source:")
    for r in s["by_source"]:
        print(f"  {r['source']:<16} {r['n']:>9,}  ({r['with_website']:,} with a website)")
    print("\nWebsites:")
    for r in s["by_status"]:
        print(f"  {r['status']:<12} {r['n']:>9,}")


def cmd_sources(args, settings) -> None:
    from .sources import all_sources
    cursors = {}
    con = _db()
    try:
        for source, key, value in con.execute(
                "select source, key, value from companies.cursors order by source, key").fetchall():
            cursors.setdefault(source, {})[key] = json.loads(value) if isinstance(value, str) else value
    finally:
        con.close()
    for name in all_sources():
        cfg = settings.get("sources", {}).get(name, {})
        state = cursors.get(name, {})
        keys = {k: v for k, v in state.items() if not k.startswith("_")}
        done = sum(1 for v in keys.values() if v.get("done_at"))
        print(f"{'on ' if cfg.get('enabled') else 'off'}  {name:<16} {cfg.get('about', '')}")
        if keys:
            print(f"     {len(keys)} cursor(s), {done} finished this pass; unfinished: "
                  + (", ".join(k for k, v in keys.items() if not v.get("done_at"))[:120] or "none"))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m companies", description="Company directory from open data.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--config", help="Another settings.json")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("collect", help="add companies from the sources (resumable)")
    c.add_argument("--max-minutes", type=float, help="Start nothing new after this long (default: settings, 5)")
    c.add_argument("--source", action="append", help="Only this source (repeatable); also runs a disabled one")
    c.add_argument("--country", action="append", help="Only these countries (ISO codes, e.g. LK or US,GB)")

    u = sub.add_parser("update", help="read company websites for social links, people, employees")
    u.add_argument("--max-minutes", type=float, help="Start nothing new after this long (default: settings, 10)")
    u.add_argument("--limit", type=int, help="At most this many websites")
    u.add_argument("--country", action="append")
    u.add_argument("--domain", action="append", help="Read this website now, even if it isn't due")

    s = sub.add_parser("show", help="companies in the database")
    s.add_argument("--country")
    s.add_argument("--source")
    s.add_argument("--search")
    s.add_argument("--limit", type=int, default=20)

    sub.add_parser("stats", help="counts by country, source and website status")
    sub.add_parser("sources", help="sources and where each one stopped")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("crawl4ai", "asyncio", "aiohttp", "urllib3", "httpx", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    settings = load_settings(args.config)
    try:
        {"collect": cmd_collect, "update": cmd_update, "show": cmd_show, "stats": cmd_stats,
         "sources": cmd_sources}[args.command](args, settings)
    except KeyboardInterrupt:
        print("\nStopped. What was found is saved; the next run continues where this one stopped.")
        sys.exit(130)
