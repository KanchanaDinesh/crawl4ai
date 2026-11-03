"""Command line: python -m techintel <stage> [options]

    discover  [--source NAME ...] [--minutes N] [--seeds-file FILE] [--force]
    qualify   [--limit N] [--recheck] [--domain D ...]
    enrich    [--limit N] [--domain D ...]      registration dates (RDAP)
    profile   [--limit N] [--domain D ...]
    monitor   [--limit N] [--force] [--domain D ...]
    analyze   [--days N]
    report    [--days N]
    collect   new sites, fast and without a browser: discover -> qualify -> enrich
    update    the slow part, with a browser: profile new sites -> monitor known ones -> report
    run       collect + update (all stages in order)
    quick     frequent run (every 10-15 min): live queue + Hacker News + seeds -> qualify -> enrich
    watch     live listener: queue new domains from the certificate stream (runs until stopped)
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
import time
from collections import Counter

os.environ.setdefault("CRAWL4AI_NO_CLOUD_NOTICE", "1")   # hide crawl4ai's promo banner

from .models import utc_now
from .settings import load_settings
from .stages import STAGE_ORDER
from .stages.profile import BrowserDown
from .store import open_store

logger = logging.getLogger("techintel")


async def run_stage(name: str, settings, store, **options) -> dict:
    module = importlib.import_module(f".stages.{name}", __package__)
    started, t0 = utc_now(), time.monotonic()
    logger.info("── %s ──", name)

    def record(status: str, summary: dict) -> None:
        store.append("runs", [{"stage": name, "status": status, "started_at": started, "finished_at": utc_now(),
                               "seconds": round(time.monotonic() - t0, 1),
                               "options": {k: v for k, v in options.items() if v}, "summary": summary}])
    try:
        summary = await module.run(settings, store, **options)
        status = "ok"
    except (asyncio.CancelledError, KeyboardInterrupt):
        record("interrupted", {"note": "stopped with Ctrl+C; results finished so far are saved"})
        logger.warning("%s interrupted: results finished so far are saved; the rest is picked up next run", name)
        raise
    except BrowserDown as e:            # a known problem with a clear message: no traceback needed
        logger.error("%s stopped: %s", name, e)
        summary, status = {**e.summary, "error": str(e), "browser_down": True}, "failed"
    except Exception as e:
        logger.exception("stage %s failed", name)
        summary, status = {"error": str(e)}, "failed"
    record(status, summary)
    if status == "failed":
        FAILED_STAGES.append(name)
    logger.info("%s done in %s: %s", name, _dur(time.monotonic() - t0), json.dumps(summary, ensure_ascii=False))
    return summary


FAILED_STAGES: list[str] = []


def _dur(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}m{seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def status(store) -> None:
    quals = Counter(q["status"] for q in store.latest("qualifications").values())
    print(f"candidates      {len(store.keys('candidates'))}")
    print(f"qualified       {quals.get('qualified', 0)}   watch {quals.get('watch', 0)}   rejected {quals.get('rejected', 0)}")
    regs = store.latest("registrations")
    print(f"registrations   {sum(1 for r in regs.values() if r.get('registered_at'))} with date, "
          f"{sum(1 for r in regs.values() if r.get('error'))} without")
    print(f"profiled        {len(store.keys('profiles'))}")
    print(f"changes         {sum(1 for _ in store.read('changes'))}")
    last = {r["stage"]: r for r in store.read("runs")}
    print("\nlast runs:")
    for stage in STAGE_ORDER:
        if stage in last:
            r = last[stage]
            took = _dur(r["seconds"]) if r.get("seconds") is not None else "?"
            print(f"  {stage:<9} {r['finished_at']}  {r['status']:<11} {took:>7}  "
                  f"{json.dumps(r['summary'], ensure_ascii=False)[:100]}")


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
    d.add_argument("--force", action="store_true", help="Ask crt.sh even if it was checked in the last few hours")

    for name, help_text in (("qualify", "Cheap homepage check"),
                            ("enrich", "Look up domain registration dates (RDAP)"),
                            ("profile", "Deep crawl of qualified sites"),
                            ("monitor", "Re-crawl profiled sites and record changes")):
        s = sub.add_parser(name, help=help_text)
        s.add_argument("--limit", type=int, help="Process at most N sites")
        s.add_argument("--domain", action="append", dest="domains", help="Only this domain (repeatable)")
        s.add_argument("--max-minutes", type=float, dest="max_minutes",
                       help="Stop starting new sites after N minutes (the rest is done next run)")
        if name == "qualify":
            s.add_argument("--recheck", action="store_true", help="Re-check already checked candidates")
        if name == "monitor":
            s.add_argument("--force", action="store_true", help="Ignore monitor.interval_days")

    for name in ("analyze", "report"):
        s = sub.add_parser(name, help=f"{name.title()} the collected data")
        s.add_argument("--days", type=int, help="Reporting period in days")

    q = sub.add_parser("quick", help="Frequent run: new domains (live queue, Hacker News, seeds) -> qualify -> enrich")
    q.add_argument("--max-minutes", type=float, dest="max_minutes", help="Time limit for qualify (default: quick.qualify_max_minutes)")
    w = sub.add_parser("watch", help="Live listener: queue new domains from the certificate stream, without stopping")
    w.add_argument("--batch-minutes", type=float, dest="batch_minutes", help="Log/flush every N minutes (default 5)")
    w.add_argument("--batches", type=int, help="Stop after N batches (default: run forever)")

    for name, help_text in (("collect", "Find and store new sites: discover -> qualify -> enrich (no browser)"),
                            ("update", "Profile new sites and re-check known ones (browser), then the report"),
                            ("run", "collect + update: all stages in order")):
        r = sub.add_parser(name, help=help_text)
        if name != "update":
            r.add_argument("--source", action="append", dest="sources", help="Discovery sources (default: enabled ones)")
            r.add_argument("--minutes", type=float, help="certstream listening time, if certstream is used")
        r.add_argument("--limit", type=int, help="Max sites per stage")
        r.add_argument("--max-minutes", type=float, dest="max_minutes",
                       help="Time limit per stage (default: crawl.max_stage_minutes in settings)")
        if name != "collect":
            r.add_argument("--days", type=int, help="Report period in days")

    sub.add_parser("status", help="Show counts and last runs")
    return parser


async def _timed(timings: list, stage: str, settings, store, **options) -> dict:
    t = time.monotonic()
    summary = await run_stage(stage, settings, store, **options)
    timings.append((stage, time.monotonic() - t))
    return summary


async def collect(settings, store, options: dict, timings: list) -> None:
    """New sites, saved quickly: nothing here needs the browser (except qualify's rare fallback),
    so a browser problem or a long profiling run can't hold up finding new sites."""
    await _timed(timings, "discover", settings, store, sources=options.get("sources"), minutes=options.get("minutes"))
    for stage in ("qualify", "enrich"):
        await _timed(timings, stage, settings, store, limit=options.get("limit"), max_minutes=options.get("max_minutes"))


async def update(settings, store, options: dict, timings: list) -> None:
    """The slow part, with a browser: profile sites that are new, then re-check known ones (monitor)."""
    limits = {"limit": options.get("limit"), "max_minutes": options.get("max_minutes")}
    profiled = await _timed(timings, "profile", settings, store, **limits)
    if profiled.get("browser_down"):
        logger.warning("monitor skipped: the browser isn't working (see above). Nothing was lost; "
                       "the sites are checked on the next run.")
    else:
        await _timed(timings, "monitor", settings, store, **limits)
    summary = await _timed(timings, "report", settings, store, days=options.get("days"))
    if "html" in summary:
        print(f"Report: {summary['html']}")


async def main_async(args) -> None:
    settings = load_settings(args.config)
    store = open_store(settings.data_dir)
    options = {k: v for k, v in vars(args).items() if k not in ("config", "verbose", "command")}

    if args.command == "status":
        status(store)
        return
    if args.command == "quick":
        cfg = settings.section("quick")
        t = time.monotonic()
        await run_stage("discover", settings, store, sources=cfg.get("sources") or ["stream", "hackernews", "seeds"])
        await run_stage("qualify", settings, store, max_minutes=options.get("max_minutes") or cfg.get("qualify_max_minutes", 8))
        await run_stage("enrich", settings, store, max_minutes=cfg.get("enrich_max_minutes", 3))
        print(f"\nquick run done in {_dur(time.monotonic() - t)}")
        return
    if args.command in ("collect", "update", "run"):
        timings: list[tuple[str, float]] = []
        if args.command in ("collect", "run"):
            await collect(settings, store, options, timings)
        if args.command in ("update", "run"):
            await update(settings, store, options, timings)
        print("\nTime per stage: " + "  ".join(f"{s} {_dur(d)}" for s, d in timings)
              + f"  |  total {_dur(sum(d for _, d in timings))}")
        return

    summary = await run_stage(args.command, settings, store, **options)
    if args.command == "report" and "html" in summary:
        print(f"\nReport: {summary['html']}")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "watch":
        logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                            format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        logging.getLogger("aiohttp").setLevel(logging.WARNING)
        from .watch import watch
        settings = load_settings(args.config)
        try:
            asyncio.run(watch(settings, args.batch_minutes or settings.section("watch").get("batch_minutes", 5), args.batches))
        except KeyboardInterrupt:
            print("\nStopped listening. Queued domains are added by the next `python -m techintel quick`.")
        return
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("crawl4ai", "asyncio", "aiohttp", "urllib3", "httpx", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nStopped. Finished results are saved; run the same command again to continue.")
        sys.exit(130)
    if FAILED_STAGES:
        print(f"Failed stage(s): {', '.join(FAILED_STAGES)} (see the errors above)")
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
