"""Certificate Transparency logs via crt.sh.

Every public HTTPS certificate is logged publicly. Querying crt.sh for a
pattern (e.g. "%.lk") lists certificates for matching names; we keep those
logged in the last `lookback_days` and reduce each name to its root domain.

crt.sh is free but slow, and big queries can time out: keep patterns narrow.
For a real-time feed of *all* new certificates, use the certstream source.
"""

import logging
from datetime import datetime, timedelta, timezone

import aiohttp

from ..domains import homepage_url, root_domain
from ..models import Candidate

logger = logging.getLogger(__name__)

API = "https://crt.sh/"


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "")).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_entries(entries: list[dict], since: datetime, domain_filter, query: str) -> list[Candidate]:
    found: dict[str, Candidate] = {}
    for entry in sorted(entries, key=lambda e: e.get("entry_timestamp") or "", reverse=True):
        logged = _parse_time(entry.get("entry_timestamp"))
        if logged and logged < since:
            continue
        names = {entry.get("common_name") or ""} | set((entry.get("name_value") or "").split("\n"))
        for name in names:
            domain = root_domain(name) if name else None
            if not domain or domain in found or domain_filter.is_ignored(domain):
                continue
            found[domain] = Candidate(
                domain=domain,
                url=homepage_url(domain),
                source="crtsh",
                signal_at=entry.get("entry_timestamp"),
                detail={"query": query, "cert_name": name.strip(), "issuer": entry.get("issuer_name"),
                        "crtsh_id": entry.get("id")},
            )
    return list(found.values())


async def collect(ctx) -> list[Candidate]:
    config = ctx.settings.source("crtsh")
    since = datetime.now(timezone.utc) - timedelta(days=float(config.get("lookback_days", 3)))
    timeout = aiohttp.ClientTimeout(total=float(config.get("timeout_seconds", 120)))
    limit = int(config.get("max_results_per_query", 500))
    results: list[Candidate] = []
    queries = config.get("queries", [])
    errors: list[str] = []
    async with aiohttp.ClientSession(trust_env=True) as session:
        for query in queries:
            params = {"q": query, "output": "json", "exclude": "expired"}
            try:
                async with session.get(API, params=params, timeout=timeout) as resp:
                    resp.raise_for_status()
                    entries = await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError) as e:
                logger.error("crt.sh query %r failed: %s", query, e)
                errors.append(f"{query}: {e}")
                continue
            found = parse_entries(entries, since, ctx.domain_filter, query)[:limit]
            logger.info("crt.sh %r: %d certificates, %d recent root domains", query, len(entries), len(found))
            results.extend(found)
    if queries and len(errors) == len(queries):
        raise RuntimeError("all crt.sh queries failed: " + "; ".join(errors))
    return results
