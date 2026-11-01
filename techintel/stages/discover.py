"""Stage 1: DISCOVER new domains from the configured sources.

Only first sightings are kept: a domain already in candidates.jsonl is skipped,
so re-running is safe and certificate renewals don't create duplicates.
"""

import logging

from ..domains import DomainFilter
from ..sources import SourceContext, get_sources

logger = logging.getLogger(__name__)


async def run(settings, store, sources: list[str] | None = None, **options) -> dict:
    available = get_sources()
    if sources:
        unknown = set(sources) - set(available)
        if unknown:
            raise ValueError(f"Unknown source(s): {', '.join(sorted(unknown))}. Available: {', '.join(available)}")
        names = sources
    else:
        names = [n for n in available if settings.source(n).get("enabled", True)]

    domain_filter = DomainFilter(settings.section("domain_filter"))
    ctx = SourceContext(settings, store, domain_filter, options)
    known = store.keys("candidates")
    new_rows: list[dict] = []
    per_source: dict[str, dict] = {}
    errors: dict[str, str] = {}

    for name in names:
        try:
            found = await available[name](ctx)
        except Exception as e:
            logger.error("source %s failed: %s", name, e)
            errors[name] = str(e)
            continue
        kept = 0
        for cand in found:
            if cand.domain in known or domain_filter.is_ignored(cand.domain):
                continue
            known.add(cand.domain)
            new_rows.append(cand.to_dict())
            kept += 1
        per_source[name] = {"found": len(found), "new": kept}
        logger.info("discover %-10s found %4d, new %4d", name, len(found), kept)

    store.append("candidates", new_rows)
    return {"new_candidates": len(new_rows), "sources": per_source, "errors": errors}
