"""Command line: python -m techintel <stage> [options]

    discover  [--source NAME ...] [--minutes N] [--seeds-file FILE]
    qualify   [--limit N] [--recheck] [--domain D ...]
    profile   [--limit N] [--domain D ...]
    monitor   [--limit N] [--force] [--domain D ...]
    analyze   [--days N]
    report    [--days N]
    run       all stages in order (discover -> qualify -> profile -> monitor -> report)
    status    counts per stage, and the last run of each stage

Global: --config PATH (a different settings.json), -v for debug logging.
"""

import argparse
import asyncio
import importlib
import json
import logging
import os
import sys
from collections import Counter

os.environ.setdefault("CRAWL4AI_NO_CLOUD_NOTICE", "1")   # hide crawl4ai's promo banner

from .models import utc_now
from .settings import load_settings
from .stages import STAGE_ORDER
from .store import FileStore

logger = logging.getLogger("techintel")


async def run_stage(name: str, settings, store, **options) -> dict:
    module = importlib.import_module(f".stages.{name}", __package__)
    started = utc_now()
    logger.info("── %s ──", name)
    try:
        summary = await module.run(settings, store, **options)
        status = "ok"
    except Exception as e:
        logger.exception("stage %s failed", name)
        summary, status = {"error": str(e)}, "failed"
    store.append("runs", [{"stage": name, "status": status, "started_at": started,
                           "finished_at": utc_now(), "options": {k: v for k, v in options.items() if v},
                           "summary": summary}])
    logger.info("%s: %s", name, json.dumps(summary, ensure_ascii=False))
    return summary


def status(store) -> None:
    quals = Counter(q["status"] for q in store.latest("qualifications").values())
    print(f"candidates      {len(store.keys('candidates'))}")
    print(f"qualified       {quals.get('qualified', 0)}   watch {quals.get('watch', 0)}   rejected {quals.get('rejected', 0)}")
    print(f"profiled        {len(store.keys('profiles'))}")
    print(f"changes         {sum(1 for _ in store.read('changes'))}")
    last = {r["stage"]: r for r in store.read("runs")}
    print("\nlast runs:")
    for stage in STAGE_ORDER:
        if stage in last:
            r = last[stage]
            print(f"  {stage:<9} {r['finished_at']}  {r['status']:<6} {json.dumps(r['summary'], ensure_ascii=False)[:110]}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m techintel", description="Tech intelligence pipeline.")
    parser.add_argument("--config", help="Path to a settings.json (default: techintel/config/settings.json)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("discover", help="Find new domains")
    d.add_argument("--source", action="append", dest="sources",
                   help="seeds | hackernews | crtsh | certstream | outlinks (repeatable; default: enabled ones)")
    d.add_argument("--minutes", type=float, help="How long to listen (certstream)")
    d.add_argument("--seeds-file", help="Seeds file instead of the configured one")

    for name, help_text in (("qualify", "Cheap homepage check"), ("profile", "Deep crawl of qualified sites"),
                            ("monitor", "Re-crawl profiled sites and record changes")):
        s = sub.add_parser(name, help=help_text)
        s.add_argument("--limit", type=int, help="Process at most N sites")
        s.add_argument("--domain", action="append", dest="domains", help="Only this domain (repeatable)")
        if name == "qualify":
            s.add_argument("--recheck", action="store_true", help="Re-check already checked candidates")
        if name == "monitor":
            s.add_argument("--force", action="store_true", help="Ignore monitor.interval_days")

    for name in ("analyze", "report"):
        s = sub.add_parser(name, help=f"{name.title()} the collected data")
        s.add_argument("--days", type=int, help="Reporting period in days")

    r = sub.add_parser("run", help="Run all stages in order")
    r.add_argument("--source", action="append", dest="sources", help="Discovery sources (default: enabled ones)")
    r.add_argument("--minutes", type=float, help="certstream listening time, if certstream is used")
    r.add_argument("--limit", type=int, help="Max sites per stage")
    r.add_argument("--days", type=int, help="Report period in days")

    sub.add_parser("status", help="Show counts and last runs")
    return parser


async def main_async(args) -> None:
    settings = load_settings(args.config)
    store = FileStore(settings.data_dir)
    options = {k: v for k, v in vars(args).items() if k not in ("config", "verbose", "command")}

    if args.command == "status":
        status(store)
        return
    if args.command == "run":
        await run_stage("discover", settings, store, sources=options.get("sources"), minutes=options.get("minutes"))
        for stage in ("qualify", "profile", "monitor"):
            await run_stage(stage, settings, store, limit=options.get("limit"))
        summary = await run_stage("report", settings, store, days=options.get("days"))
        if "html" in summary:
            print(f"\nReport: {summary['html']}")
        return

    summary = await run_stage(args.command, settings, store, **options)
    if args.command == "report" and "html" in summary:
        print(f"\nReport: {summary['html']}")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("crawl4ai", "asyncio", "aiohttp", "urllib3", "httpx", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
