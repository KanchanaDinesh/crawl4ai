"""Stage 2b: ENRICH qualified (and watched) domains with their registration date via RDAP.

Runs between qualify and profile. Only looks up domains with no registration
row yet (or whose last lookup failed more than retry_failed_after_days ago),
so registries aren't hammered. Results go to registrations.jsonl.

If the RDAP service refuses us (401/403/429), the stage stops for this run instead of
asking again and again, and those domains stay due (a refusal isn't the domain's answer).
"""

import asyncio
import logging

import aiohttp

from .. import rdap
from ..domains import _is_local
from ..models import utc_now
from . import age_days, process_items

logger = logging.getLogger(__name__)
DEFAULT_USER_AGENT = "crawl4ai-techintel/1.0 (personal research; https://github.com/KanchanaDinesh/crawl4ai)"


async def run(settings, store, limit: int | None = None, domains: list[str] | None = None,
              max_minutes: float | None = None, **_) -> dict:
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
        refused = (last or {}).get("error", "").startswith(("401", "403", "429"))   # saved by older versions
        if domains or last is None or refused or (last.get("error") and age_days(last.get("checked_at")) >= retry_days):
            todo.append(domain)
    todo = todo[: limit or None]
    if not todo:
        return {"looked_up": 0}

    url_template = config.get("rdap_url", "https://rdap.org/domain/{domain}")
    timeout = float(config.get("timeout_seconds", 20))
    found, refused = 0, []
    # say who we are: an honest user agent, never a browser's
    headers = {"User-Agent": config.get("user_agent") or DEFAULT_USER_AGENT}

    async with aiohttp.ClientSession(trust_env=True, headers=headers) as session:
        async def one(domain: str) -> dict:
            row = {"domain": domain, "checked_at": utc_now()}
            if refused:
                return {**row, "refused": refused[0]}
            try:
                row.update(await rdap.lookup(session, domain, url_template, timeout))
            except rdap.Refused as e:
                if not refused:
                    logger.warning("enrich: %s. Stopping lookups for this run; those domains are tried next run.", e)
                refused.append(str(e))
                return {**row, "refused": str(e)}
            except (aiohttp.ClientError, asyncio.TimeoutError, LookupError, ValueError) as e:
                row["error"] = str(e)[:200] or type(e).__name__
            await asyncio.sleep(float(config.get("delay_seconds", 0.5)))
            return row

        def save(row: dict) -> None:
            nonlocal found
            if row.get("refused"):
                return
            found += bool(row.get("registered_at"))
            store.append("registrations", [row])

        out = await process_items(
            todo, one, stage="enrich", on_result=save,
            describe=lambda r: f"{r['domain']:<28} " + ("not saved, tried next run" if r.get("refused")
                                                        else r.get("registered_at") or r.get("error", "")[:70]),
            concurrency=int(config.get("concurrency", 3)),
            max_minutes=max_minutes or settings.section("crawl").get("max_stage_minutes"),
            stop=lambda: refused[0] if refused else None)
    n_refused = sum(1 for r in out["results"] if r.get("refused"))
    looked_up = len(out["results"]) - n_refused
    summary = {"looked_up": looked_up, "with_date": found, "failed": looked_up - found,
               "left_for_next_run": out["skipped"] + n_refused, "seconds": out["seconds"]}
    if refused:
        summary["refused"] = refused[0]
    return summary
