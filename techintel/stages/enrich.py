"""Stage 2b: ENRICH qualified (and watched) domains with their registration date via RDAP.

Runs between qualify and profile. Only looks up domains with no registration
row yet (or whose last lookup failed more than retry_failed_after_days ago),
so registries aren't hammered. Results go to registrations.jsonl.
"""

import asyncio
import logging

import aiohttp

from .. import rdap
from ..domains import _is_local
from ..models import utc_now
from . import age_days

logger = logging.getLogger(__name__)


async def run(settings, store, limit: int | None = None, domains: list[str] | None = None, **_) -> dict:
    config = settings.section("enrich")
    if not config.get("rdap_enabled", True):
        return {"looked_up": 0, "skipped": "rdap disabled"}

    registrations = store.latest("registrations")
    retry_days = float(config.get("retry_failed_after_days", 30))
    todo = []
    for domain, q in store.latest("qualifications").items():
        if domains and domain not in domains:
            continue
        if not domains and q["status"] not in ("qualified", "watch"):
            continue
        if _is_local(domain):
            continue
        last = registrations.get(domain)
        if domains or last is None or (last.get("error") and age_days(last.get("checked_at")) >= retry_days):
            todo.append(domain)
    todo = todo[: limit or None]
    if not todo:
        return {"looked_up": 0}

    semaphore = asyncio.Semaphore(int(config.get("concurrency", 3)))
    url_template = config.get("rdap_url", "https://rdap.org/domain/{domain}")
    timeout = float(config.get("timeout_seconds", 20))

    async with aiohttp.ClientSession(trust_env=True) as session:
        async def one(domain: str) -> dict:
            async with semaphore:
                row = {"domain": domain, "checked_at": utc_now()}
                try:
                    row.update(await rdap.lookup(session, domain, url_template, timeout))
                except (aiohttp.ClientError, asyncio.TimeoutError, LookupError, ValueError) as e:
                    row["error"] = str(e)[:200] or type(e).__name__
                await asyncio.sleep(float(config.get("delay_seconds", 0.5)))
                return row
        rows = await asyncio.gather(*(one(d) for d in todo))

    store.append("registrations", rows)
    found = sum(1 for r in rows if r.get("registered_at"))
    for r in rows:
        logger.info("enrich %-28s %s", r["domain"], r.get("registered_at") or r.get("error"))
    return {"looked_up": len(rows), "with_date": found, "failed": len(rows) - found}
