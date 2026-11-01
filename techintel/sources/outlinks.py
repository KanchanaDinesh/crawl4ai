"""Snowball discovery: unknown domains linked from sites we've already profiled.

A tech site linking to another tech site is a strong hint. The profile stage
records each site's external domains; this source turns new ones into candidates.
"""

from ..domains import homepage_url
from ..models import Candidate


def collect_from_profiles(profiles: dict[str, dict], known: set[str], domain_filter, limit: int) -> list[Candidate]:
    found: dict[str, Candidate] = {}
    for profile in profiles.values():
        for domain in profile.get("external_domains", []):
            if domain in known or domain in found or domain_filter.is_ignored(domain):
                continue
            found[domain] = Candidate(domain=domain, url=homepage_url(domain), source="outlinks",
                                      detail={"linked_from": profile["domain"]})
            if len(found) >= limit:
                return list(found.values())
    return list(found.values())


async def collect(ctx) -> list[Candidate]:
    limit = int(ctx.settings.source("outlinks").get("max_per_run", 200))
    return collect_from_profiles(ctx.store.latest("profiles"), ctx.store.keys("candidates"),
                                 ctx.domain_filter, limit)
