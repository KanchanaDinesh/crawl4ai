"""Certificate Transparency logs via crt.sh.

Every public HTTPS certificate is logged publicly. Querying crt.sh for a
pattern (e.g. "%.lk") lists certificates for matching names; we keep those
logged in the last `lookback_days` and reduce each name to its root domain.

crt.sh is free but slow, and big queries can time out: keep patterns narrow.
For a real-time feed of *all* new certificates, use the certstream source.

A "%.lk" query takes about 2 minutes, but it looks back `lookback_days` (3), so it only runs
every `min_hours_between_runs` (6), and after a failure it waits `retry_after_minutes` (60).
That keeps it from slowing down every collect run, and spares crt.sh.
"""

import json
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


def _state_file(ctx):
    return ctx.settings.data_dir / "crtsh_state.json"


def _hours_since(value: str | None) -> float:
    when = _parse_time(value)
    return (datetime.now(timezone.utc) - when).total_seconds() / 3600 if when else float("inf")


def wait_reason(state: dict, config: dict) -> str | None:
    """Why crt.sh isn't asked this run, or None if it's due."""
    ok_ago = _hours_since(state.get("last_ok_at"))
    failed_ago = _hours_since(state.get("last_failed_at"))
    every = float(config.get("min_hours_between_runs", 6))
    retry = float(config.get("retry_after_minutes", 60)) / 60
    if failed_ago < retry and failed_ago < ok_ago:
        return f"it failed {failed_ago * 60:.0f} min ago; trying again after {retry * 60:.0f} min"
    if ok_ago < every:
        return f"last checked {ok_ago:.1f}h ago; it looks back {config.get('lookback_days', 3)} days, so every {every:g}h is enough"
    return None


async def collect(ctx) -> list[Candidate]:
    config = ctx.settings.source("crtsh")
    state_file = _state_file(ctx)
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    why = wait_reason(state, config) if not ctx.options.get("force") else None
    if why:
        logger.info("crt.sh: skipped this run (%s)", why)
        return []
    found, failed = await _query_all(ctx, config)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    state["last_failed_at" if failed else "last_ok_at"] = stamp
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state), encoding="utf-8")
    if failed:
        raise RuntimeError(failed)
    return found


async def _query_all(ctx, config: dict) -> tuple[list[Candidate], str | None]:
    """All configured queries. Returns (candidates, error if every query failed)."""
    since = datetime.now(timezone.utc) - timedelta(days=float(config.get("lookback_days", 3)))
    timeout = aiohttp.ClientTimeout(total=float(config.get("timeout_seconds", 120)))
    limit = int(config.get("max_results_per_query", 500))
    results: list[Candidate] = []
    queries = config.get("queries", [])
    errors: list[str] = []
    async with aiohttp.ClientSession(trust_env=True) as session:
        for query in queries:
            logger.info("crt.sh: querying %r (crt.sh is slow; this can take up to %ss)", query, int(timeout.total))
            params = {"q": query, "output": "json", "exclude": "expired"}
            try:
                async with session.get(API, params=params, timeout=timeout) as resp:
                    resp.raise_for_status()
                    entries = await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError) as e:
                why = str(e) or f"no answer within {timeout.total:.0f}s"
                logger.error("crt.sh query %r failed: %s", query, why)
                errors.append(f"{query}: {why}")
                continue
            found = parse_entries(entries, since, ctx.domain_filter, query)[:limit]
            logger.info("crt.sh %r: %d certificates, %d recent root domains", query, len(entries), len(found))
            results.extend(found)
    if queries and len(errors) == len(queries):
        return results, "all crt.sh queries failed: " + "; ".join(errors)
    return results, None
