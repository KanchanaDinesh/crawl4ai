"""python -m articles <command>

    run       [--max-minutes 20] [--country US]   companies if due, then feeds, then articles; stop any time, the next run continues
    companies                                     read the list of most valuable companies again
    feeds     [--max-minutes 15]                  look for the feeds of companies that haven't been checked lately
    collect   [--max-minutes 15] [--country US]   read the feeds that are due and save the articles about tech services
    show      [--tag ai] [--country US] [--company Google] [--limit 30]   the newest articles
    stats                                         how many, per country, tag and company
    retag                                         apply changed word lists to the saved articles; nothing is downloaded

Countries: US, GB, IN, AU, CA. Sri Lanka will be separate. Settings: articles/config/settings.json
"""

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import sys
import time

from . import companies_list as cl
from .grabbing import Web, find_feeds, read_feed
from .store import ArticleStore
from .tagging import compile_tags

CONFIG = os.path.join(os.path.dirname(__file__), "config", "settings.json")
logger = logging.getLogger("articles")
COMPANIES_EVERY_DAYS = 30


async def _companies(store: ArticleStore, settings: dict) -> dict:
    started, found, done, failed = time.monotonic(), [], [], {}
    async with Web(settings["fetch"]) as web:
        for index in settings["indexes"]:
            try:
                members = await cl.fetch_index(web, index)
                found += members
                done.append(index["name"])
                logger.info("%-11s %3d companies, %d with a website", index["name"], len(members), sum(1 for m in members if m["website"]))
            except (ValueError, KeyError) as e:              # the old list stays, and this says why
                failed[index["name"]] = str(e)[:200]
                logger.warning("%s: %s. Its list stays as it was.", index["name"], str(e)[:200])
    merged = cl.merge(found)
    store.save_companies(merged)
    retired = store.retire_others(done, [c["id"] for c in merged]) if done else 0
    store.save_companies(cl.extras(settings))
    counts = {"companies": len(merged), "extra": len(settings.get("extra_companies", [])), "left_the_list": retired, "failed": failed}
    store.save_run("companies", time.monotonic() - started, counts)
    return counts


def _known_feeds(settings: dict, company: dict) -> list[str]:
    feeds = []
    for key, urls in settings.get("known_feeds", {}).items():
        if re.search(rf"\b{re.escape(key)}\b", company["name"], re.I):         # whole words: "meta" must not match Metro
            feeds += urls
    for extra in settings.get("extra_companies", []):
        if extra["name"] == company["name"]:
            feeds += extra.get("feeds", [])
    return feeds


async def _feeds(store: ArticleStore, settings: dict, max_minutes: float) -> dict:
    started, deadline = time.monotonic(), time.monotonic() + max_minutes * 60
    discovery = settings["discovery"]
    due = store.companies_needing_feeds(discovery["again_after_days"])
    waiting = {c["id"] for c in due}
    due += [c for c in store.companies_missing_known_feeds(lambda c: _known_feeds(settings, c)) if c["id"] not in waiting]
    counts = {"checked": 0, "with_feeds": 0, "feeds": 0, "left": 0}
    queue: asyncio.Queue = asyncio.Queue()
    for c in due:
        queue.put_nowait(c)

    async def worker(web):
        while not queue.empty() and time.monotonic() < deadline:
            company = queue.get_nowait()
            feeds, note = await find_feeds(web, company, discovery, _known_feeds(settings, company))
            store.save_feeds(company["id"], feeds, note)
            counts["checked"] += 1
            counts["with_feeds"] += bool(feeds)
            counts["feeds"] += len(feeds)
            if counts["checked"] % 25 == 0:
                logger.info("Feeds: %d of %d companies checked, %d have feeds", counts["checked"], len(due), counts["with_feeds"])

    async with Web(settings["fetch"]) as web:
        await asyncio.gather(*(worker(web) for _ in range(int(settings["fetch"].get("concurrency", 6)))))
    counts["left"] = queue.qsize()
    store.save_run("feeds", time.monotonic() - started, counts)
    return counts


async def _collect(store: ArticleStore, settings: dict, max_minutes: float, country: str | None) -> dict:
    started, deadline = time.monotonic(), time.monotonic() + max_minutes * 60
    cfg = settings["collect"]
    free = shutil.disk_usage("/").free / 1e9
    counts = {"feeds": 0, "new": 0, "not_about_services": 0, "refused": 0, "failed": 0, "left": 0}
    if free < cfg["min_free_gb"]:
        logger.warning("Only %.1f GB free on the Mac's drive, where PostgreSQL keeps its data. Not saving articles: free some space first.", free)
        return {**counts, "note": "disk nearly full"}
    compiled = compile_tags(settings["tags"])
    due = store.feeds_due(cfg["feed_every_hours"], country)
    queue: asyncio.Queue = asyncio.Queue()
    for f in due:
        queue.put_nowait(f)

    async def worker(web):
        while not queue.empty() and time.monotonic() < deadline:
            feed = queue.get_nowait()
            result = await read_feed(web, feed, store.known_urls(feed["company_id"]), cfg, compiled, deadline)
            added = store.save_feed_result(feed, result, cfg["refused_wait_days"])
            counts["feeds"] += 1
            counts["new"] += added
            counts["not_about_services"] += result["skipped"]
            counts["refused"] += result["status"] == "refused"
            counts["failed"] += result["status"] == "failed"
            if counts["feeds"] % 25 == 0:
                logger.info("Articles: %d of %d feeds read, %d new articles", counts["feeds"], len(due), counts["new"])

    async with Web(settings["fetch"]) as web:
        await asyncio.gather(*(worker(web) for _ in range(int(settings["fetch"].get("concurrency", 6)))))
    counts["left"] = queue.qsize()
    store.save_run("collect", time.monotonic() - started, counts)
    return counts


def _settings(args) -> dict:
    return json.load(open(args.config or CONFIG))


def cmd_companies(args) -> None:
    store = ArticleStore()
    print(asyncio.run(_companies(store, _settings(args))))
    store.close()


def cmd_feeds(args) -> None:
    store = ArticleStore()
    print(asyncio.run(_feeds(store, _settings(args), args.max_minutes)))
    store.close()


def cmd_collect(args) -> None:
    store = ArticleStore()
    print(asyncio.run(_collect(store, _settings(args), args.max_minutes, args.country)))
    store.close()


def cmd_run(args) -> None:
    settings, store, started = _settings(args), ArticleStore(), time.monotonic()
    due = store.con.execute("select coalesce(max(run_at) < now() - make_interval(days => %s), true) from articles.runs where step = 'companies'", [COMPANIES_EVERY_DAYS]).fetchone()[0]
    if due:
        print("Companies:", asyncio.run(_companies(store, settings)))
    left = lambda: max(1.0, args.max_minutes - (time.monotonic() - started) / 60)    # noqa: E731
    print("Feeds:", asyncio.run(_feeds(store, settings, min(left(), args.max_minutes / 2))))
    print("Articles:", asyncio.run(_collect(store, settings, left(), args.country)))
    store.close()


def cmd_retag(args) -> None:
    settings, store = _settings(args), ArticleStore()
    print(store.retag(compile_tags(settings["tags"]), settings["collect"]["body_hits_needed"]))
    store.close()


def _reader():
    from warehouse.postgres import DatabaseError, connect
    try:
        con = connect("reader", autocommit=True)
    except DatabaseError as e:
        sys.exit(str(e))
    if not con.execute("select to_regclass('articles.articles') is not null").fetchone()[0]:
        sys.exit("Nothing saved yet. Run:  uv run python -m articles run")
    return con


def cmd_show(args) -> None:
    con = _reader()
    where, params = ["true"], []
    if args.tag:
        where.append("%s = any(tags)"); params.append(args.tag)
    if args.country:
        where.append("%s = any(countries)"); params.append(args.country.upper())
    if args.company:
        where.append("company ilike %s"); params.append(f"%{args.company}%")
    rows = con.execute(f"""select to_char(published_at, 'YYYY-MM-DD'), company, title, tags, word_count, text_status, url from articles.v_articles where {' and '.join(where)}
        order by published_at desc nulls last limit {int(args.limit)}""", params).fetchall()
    con.close()
    if not rows:
        print("No articles match. Run:  uv run python -m articles run")
    for day, company, title, tags, words, status, url in rows:
        print(f"{day or 'no date':<10} {company[:22]:<22} {title[:70]:<70} {','.join(tags)}  {words}w {status}\n           {url}")


def cmd_stats(args) -> None:
    con = _reader()

    def table(title, sql):
        print(f"\n{title}")
        for row in con.execute(sql).fetchall():
            print("  " + "  ".join(str(v) for v in row))
    table("Companies, feeds, articles", """select (select count(*) from articles.companies where in_list) companies, (select count(*) from articles.companies where feeds_checked_at is not null) checked,
        (select count(*) from articles.feeds where last_ok is not null) working_feeds, (select count(*) from articles.articles) articles""")
    table("By country", "select c, count(*) from articles.v_articles, unnest(countries) c group by 1 order by 2 desc")
    table("By tag", "select t, count(*) from articles.v_articles, unnest(tags) t group by 1 order by 2 desc")
    table("Text", "select text_status, count(*), round(avg(word_count)) from articles.articles group by 1 order by 2 desc")
    table("Top companies", "select company, count(*) from articles.v_articles group by 1 order by 2 desc limit 20")
    con.close()


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m articles", description="Articles and blogs from the most valuable companies, tagged by service.")
    p.add_argument("--config", help="A different settings.json")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--max-minutes", type=float, default=20)
    r.add_argument("--country")
    sub.add_parser("companies")
    f = sub.add_parser("feeds")
    f.add_argument("--max-minutes", type=float, default=15)
    c = sub.add_parser("collect")
    c.add_argument("--max-minutes", type=float, default=15)
    c.add_argument("--country")
    s = sub.add_parser("show")
    s.add_argument("--tag")
    s.add_argument("--country")
    s.add_argument("--company")
    s.add_argument("--limit", type=int, default=30)
    sub.add_parser("stats")
    sub.add_parser("retag")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    try:
        {"run": cmd_run, "companies": cmd_companies, "feeds": cmd_feeds, "collect": cmd_collect, "show": cmd_show, "stats": cmd_stats, "retag": cmd_retag}[args.command](args)
    except KeyboardInterrupt:
        print("\nStopped. The next run continues from here.")
        sys.exit(130)
