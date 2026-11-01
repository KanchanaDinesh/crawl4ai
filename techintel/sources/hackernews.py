"""Hacker News "Show HN" posts: where many new products are launched.

Uses the free public Algolia HN API (no key needed).
"""

import logging
import time

import aiohttp

from ..domains import root_domain
from ..models import Candidate

logger = logging.getLogger(__name__)

API = "https://hn.algolia.com/api/v1/search_by_date"


def parse_hits(hits: list[dict]) -> list[Candidate]:
    candidates = []
    for hit in hits:
        url = hit.get("url")
        domain = root_domain(url) if url else None
        if not domain:
            continue
        candidates.append(Candidate(
            domain=domain,
            url=url,
            source="hackernews",
            signal_at=hit.get("created_at"),
            detail={
                "title": hit.get("title"),
                "points": hit.get("points"),
                "comments": hit.get("num_comments"),
                "hn_url": f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            },
        ))
    return candidates


async def collect(ctx) -> list[Candidate]:
    config = ctx.settings.source("hackernews")
    since = int(time.time()) - int(config.get("lookback_hours", 48)) * 3600
    params = {
        "tags": "show_hn",
        "numericFilters": f"created_at_i>{since}",
        "hitsPerPage": str(min(int(config.get("max_results", 200)), 1000)),
    }
    async with aiohttp.ClientSession(trust_env=True) as session:
        async with session.get(API, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            data = await resp.json()
    return parse_hits(data.get("hits", []))
