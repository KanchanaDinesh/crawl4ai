"""python -m worldnews <command>

    run     [--limit N] [--group US ...] [--source NAME ...] [--no-article-meta] [--no-browser]
            collect the latest N headlines per source, save them, and write the report
    check   [--group ...] [--source ...]   quick test of each source (3 headlines, no report)
    report                                 rebuild the report from the last run
    analyze [--days N] [--group LK ...]    our own analysis of the saved headlines: biggest stories, topics,
                                           names, countries, rising words, tone, Sri Lanka (offline)
    list    [--group ...]                  show the configured sources
    add     HOMEPAGE --name NAME --group CODE [--feed URL ...]   add a news site to sources.json

Global: --config DIR (a folder with settings.json + sources.json), -v for debug logging.
"""

import argparse
import asyncio
import logging
import os
import sys

from .collect import run_collection
from .report import write_report
from .settings import load_settings
from .store import NewsStore

os.environ.setdefault("CRAWL4AI_NO_CLOUD_NOTICE", "1")
logger = logging.getLogger("worldnews")


def select_sources(settings, groups=None, names=None) -> list[dict]:
    sources = [s for s in settings.load_sources() if s.get("enabled", True)]
    if groups:
        wanted = {g.upper() for g in groups}
        sources = [s for s in sources if (s.get("group") or "").upper() in wanted]
    if names:
        wanted = {n.lower() for n in names}
        sources = [s for s in sources if s["name"].lower() in wanted]
    return sources


def cmd_list(settings, args) -> None:
    names = settings.group_names
    for s in select_sources(settings, args.group):
        how = "feed" if s.get("feeds") else ("selectors" if s.get("selectors") else "auto")
        print(f"{s.get('group', ''):<7} {names.get(s.get('group'), ''):<14} {s['name']:<28} {how:<9} {s['homepage']}")


def cmd_add(settings, args) -> None:
    codes = [g["code"] for g in settings.groups]
    group = args.group.upper()
    if group not in codes:
        sys.exit(f"Unknown group {group!r}. Use one of: {', '.join(codes)} (or add it to settings.json 'groups').")
    sources = settings.load_sources()
    if any(s["name"].lower() == args.name.lower() or s["homepage"].rstrip("/") == args.homepage.rstrip("/")
           for s in sources):
        sys.exit(f"{args.name} / {args.homepage} is already in the list.")
    entry = {"name": args.name, "group": group, "homepage": args.homepage, "feeds": args.feed or []}
    sources.append(entry)
    settings.save_sources(sources)
    print(f"Added {args.name} ({group}). Test it with:  python -m worldnews check --source \"{args.name}\"")


async def cmd_run(settings, args, check: bool = False) -> None:
    sources = select_sources(settings, args.group, args.source)
    if not sources:
        sys.exit("No sources matched.")
    # check: reads the saved history (to mark new headlines) but saves nothing; its snapshot goes to _check/runs/
    store = NewsStore(settings.data_dir if not check else settings.data_dir / "_check", dry_run=check)
    limit = 3 if check else (args.limit or int(settings.raw.get("limit_per_source", 10)))
    try:
        snapshot = await run_collection(settings, store, sources, limit,
                                        article_meta=not (check or args.no_article_meta),
                                        use_browser=not getattr(args, "no_browser", False))
    finally:
        store.close()
    t = snapshot["totals"]
    if check:
        print(f"\n{'SOURCE':<28} {'GROUP':<7} {'STATUS':<7} {'VIA':<20} N  FIRST HEADLINE / ERROR")
        for s in snapshot["sources"]:
            first = s["items"][0]["title"][:60] if s["items"] else (s.get("error") or "")[:90]
            print(f"{s['name']:<28} {s.get('group', ''):<7} {s['status']:<7} {(s['via'] or '-'):<20} "
                  f"{len(s['items'])}  {first}")
        print(f"\n{t['ok']}/{t['sources']} sources OK")
        return
    paths = write_report(snapshot, settings)
    print(f"\n{t['headlines']} headlines ({t['new']} new) from {t['ok']}/{t['sources']} sources.")
    if t["ok"] == 0:
        print("No source returned headlines: check your internet connection.")
        sys.exit(1)
    print(f"Report: {paths['html']}\n        (also {settings.output_dir / 'latest.html'})")


def cmd_report(settings, _args) -> None:
    store = NewsStore(settings.data_dir)
    snapshot = store.latest_snapshot()
    store.close()
    if not snapshot:
        sys.exit("No runs yet. Run:  python -m worldnews run")
    print(f"Report: {write_report(snapshot, settings)['html']}")


def cmd_analyze(settings, args) -> None:
    from .analysis_report import run, summary_lines
    result, paths = run(settings, args.days, args.group)
    print("\n".join(summary_lines(result)))
    print(f"\nReport: {paths['html']}\n        (also {paths['latest']})")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m worldnews", description="Daily world + tech news collector.")
    p.add_argument("--config", help="Folder with settings.json and sources.json (default: worldnews/config)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    for name, help_text in (("run", "Collect headlines and write the report"), ("check", "Quick test of each source")):
        s = sub.add_parser(name, help=help_text)
        s.add_argument("--group", action="append", help="Only this group: US, IN, LK, AU, CA, CN, GLOBAL, TECH (repeatable)")
        s.add_argument("--source", action="append", help="Only this source name (repeatable)")
        s.add_argument("--no-browser", action="store_true", help="Never fall back to a real browser")
        if name == "run":
            s.add_argument("--limit", type=int, help="Headlines per source (default: limit_per_source in settings)")
            s.add_argument("--no-article-meta", action="store_true",
                           help="Don't open article pages for dates (faster; homepage sources lose dates)")
    sub.add_parser("report", help="Rebuild the report from the last run")
    an = sub.add_parser("analyze", help="Analyse the saved headlines: stories, topics, names, countries, tone")
    an.add_argument("--days", type=float, help="Period to analyse (default: analysis.default_days, 1)")
    an.add_argument("--group", action="append", help="Only these groups, e.g. LK (repeatable)")
    ls = sub.add_parser("list", help="Show configured sources")
    ls.add_argument("--group", action="append")
    add = sub.add_parser("add", help="Add a news site")
    add.add_argument("homepage", help="Homepage URL, e.g. https://www.example.com/")
    add.add_argument("--name", required=True)
    add.add_argument("--group", required=True, help="US, IN, LK, AU, CA, CN, GLOBAL, TECH, or a new one from settings")
    add.add_argument("--feed", action="append", help="RSS/Atom feed URL, if you know it (optional; found automatically)")
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("crawl4ai", "asyncio", "aiohttp", "urllib3", "httpx", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    settings = load_settings(args.config)
    if args.command == "list":
        cmd_list(settings, args)
    elif args.command == "add":
        cmd_add(settings, args)
    elif args.command == "report":
        cmd_report(settings, args)
    elif args.command == "analyze":
        cmd_analyze(settings, args)
    else:
        try:
            asyncio.run(cmd_run(settings, args, check=args.command == "check"))
        except KeyboardInterrupt:
            print("\nStopped.")
            sys.exit(130)
