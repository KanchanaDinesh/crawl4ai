"""COLLECT: add companies (name, website, country, and whatever cheap fields the source gives).

Every enabled source runs at the same time (they are different servers, each with its own pace),
each continuing where its cursor says it stopped. After max_minutes no source starts anything new;
whatever was found is already saved (each batch is saved as it arrives, with its cursor).
"""

import asyncio
import json
import logging
import time

import aiohttp

from .countries import load_countries
from .sources import Context, Refused, SourceError, all_sources
from .store import now_iso

logger = logging.getLogger("companies")


async def collect(settings: dict, store, sources: list[str] | None = None, countries: list[str] | None = None,
                  max_minutes: float | None = None) -> dict:
    started_at, t0 = now_iso(), time.monotonic()
    max_minutes = float(max_minutes or settings.get("collect", {}).get("max_minutes", 5))
    available = all_sources()
    config = settings.get("sources", {})
    if sources:
        unknown = [s for s in sources if s not in available]
        if unknown:
            raise SystemExit(f"Unknown source {', '.join(unknown)}. Sources: {', '.join(available)}")
        names = sources                                  # named on the command line: run even if disabled
    else:
        names = [n for n in available if config.get(n, {}).get("enabled")]
    summary: dict = {"sources": {}, "added": 0, "updated": 0}
    if not names:
        logger.warning("No sources enabled in companies/config/settings.json")
        return summary

    headers = {"User-Agent": settings.get("user_agent") or "crawl4ai-companies/1.0"}
    async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
        ctx = Context(session=session, settings=settings, countries=load_countries(),
                      only_countries=[c.upper() for c in countries] if countries else None,
                      deadline=time.monotonic() + max_minutes * 60, download_dir=store.download_dir)

        async def run_source(name: str) -> None:
            src = available[name](config.get(name, {}), ctx)
            info = summary["sources"].setdefault(name, {"batches": 0, "added": 0, "updated": 0})
            try:
                async for batch in src.batches(store.cursors(name)):
                    added, updated = store.upsert_companies(batch.companies)
                    for key, value in batch.cursors.items():
                        store.set_cursor(name, key, value)
                    info["batches"] += 1
                    info["added"] += added
                    info["updated"] += updated
                    with_site = sum(1 for c in batch.companies if c.get("website"))
                    logger.info("%-15s %-28s %5d found, %5d new, %5d with a website", name, batch.label[:28],
                                len(batch.companies), added, with_site)
            except Refused as e:
                info["refused"] = str(e)
                logger.warning("%s: %s. Not asked again this run; it continues where it stopped next time.", name, e)
            except SourceError as e:
                info["error"] = str(e)
                logger.error("%s: %s", name, e)
            except Exception as e:  # noqa: BLE001 - one broken source mustn't stop the others
                info["error"] = f"{type(e).__name__}: {e}"
                logger.exception("%s failed: %s", name, e)

        logger.info("collect: %s, at most %g min", ", ".join(names), max_minutes)
        await asyncio.gather(*(run_source(n) for n in names))

    for info in summary["sources"].values():
        summary["added"] += info["added"]
        summary["updated"] += info["updated"]
    summary["total"] = store.count()
    summary["seconds"] = round(time.monotonic() - t0, 1)
    store.record_run("collect", started_at, summary["seconds"], summary)
    return summary


def describe(summary: dict) -> str:
    lines = []
    for name, info in summary.get("sources", {}).items():
        why = f"  REFUSED: {info['refused']}" if info.get("refused") else f"  ERROR: {info['error']}" if info.get("error") else ""
        lines.append(f"  {name:<16} {info['added']:>7,} new {info['updated']:>8,} updated  ({info['batches']} batches){why}")
    lines.append(f"  {'total':<16} {summary.get('added', 0):>7,} new, {summary.get('total', 0):,} companies in the "
                 f"database, {summary.get('seconds', 0):.0f}s")
    return "\n".join(lines)


def to_json(summary: dict) -> str:
    return json.dumps(summary, indent=2, default=str)
