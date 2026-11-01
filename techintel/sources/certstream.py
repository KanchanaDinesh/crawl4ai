"""Real-time Certificate Transparency stream (CertStream protocol over WebSocket).

Listens for N minutes and keeps root domains whose name looks interesting
(settings["domain_filter"]: match_tlds / match_keywords). Millions of
certificates are issued per day, mostly renewals; the discover stage drops
domains we've already seen, so only first sightings become candidates.

The public server (certstream.calidog.io) is not always available. For
reliable use, run your own: https://github.com/d-Rickyy-b/certstream-server-go
and set sources.certstream.url.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import aiohttp

from ..domains import homepage_url, root_domain
from ..models import Candidate

logger = logging.getLogger(__name__)


def parse_message(message: dict, domain_filter) -> list[Candidate]:
    if message.get("message_type") != "certificate_update":
        return []
    data = message.get("data", {})
    leaf = data.get("leaf_cert", {})
    seen = data.get("seen")
    signal_at = (datetime.fromtimestamp(seen, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                 if isinstance(seen, (int, float)) else None)
    issuer = (leaf.get("issuer") or {}).get("O")
    out: dict[str, Candidate] = {}
    for name in leaf.get("all_domains", []):
        domain = root_domain(name)
        if not domain or domain in out or domain_filter.is_ignored(domain):
            continue
        if not domain_filter.is_interesting_name(domain):
            continue
        out[domain] = Candidate(
            domain=domain, url=homepage_url(domain), source="certstream", signal_at=signal_at,
            detail={"cert_name": name, "issuer": issuer, "log": (data.get("source") or {}).get("name")},
        )
    return list(out.values())


async def collect(ctx) -> list[Candidate]:
    config = ctx.settings.source("certstream")
    minutes = float(ctx.options.get("minutes") or config.get("default_minutes", 5))
    url = config.get("url", "wss://certstream.calidog.io/")
    found: dict[str, Candidate] = {}
    seen_certs = 0

    async def listen():
        nonlocal seen_certs
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.ws_connect(url, heartbeat=30) as ws:
                logger.info("certstream: listening to %s for %.1f minutes", url, minutes)
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    seen_certs += 1
                    for cand in parse_message(json.loads(msg.data), ctx.domain_filter):
                        found.setdefault(cand.domain, cand)

    try:
        await asyncio.wait_for(listen(), timeout=minutes * 60)
    except asyncio.TimeoutError:
        pass
    except aiohttp.ClientError as e:
        logger.error("certstream connection failed: %s", e)
    logger.info("certstream: %d certificates seen, %d interesting root domains", seen_certs, len(found))
    return list(found.values())
