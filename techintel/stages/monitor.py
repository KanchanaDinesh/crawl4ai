"""Stage 4: MONITOR profiled sites for changes.

Re-profiles each site whose last snapshot is older than monitor.interval_days,
compares it with the previous snapshot, and records each difference in
changes.jsonl: new/removed pages, tech added/removed, signals switching on/off
(e.g. started hiring, added pricing), content edits, and the site going down or up.
"""

import asyncio
import logging

from ..domains import DomainFilter
from ..fetch import Fetcher
from ..models import Change
from ..techstack import TechDetector
from . import age_days
from .profile import build_profile

logger = logging.getLogger(__name__)


def diff_profiles(old: dict, new: dict) -> list[Change]:
    domain = new["domain"]
    changes: list[Change] = []

    def add(change_type, field, before=None, after=None):
        changes.append(Change(domain, change_type, field, before, after))

    if old.get("status") == "ok" and new.get("status") != "ok":
        add("site_down", "status", "ok", new.get("error"))
        return changes
    if old.get("status") != "ok" and new.get("status") == "ok":
        add("site_up", "status", old.get("error"), "ok")
    if new.get("status") != "ok":
        return changes

    for field in ("name", "description"):
        if old.get(field) and new.get(field) and old[field] != new[field]:
            add(f"{field}_changed", field, old[field], new[field])

    old_pages, new_pages = old.get("key_pages", {}), new.get("key_pages", {})
    for page_type in sorted(set(new_pages) - set(old_pages)):
        add("page_added", page_type, None, new_pages[page_type])
    for page_type in sorted(set(old_pages) - set(new_pages)):
        add("page_removed", page_type, old_pages[page_type], None)

    old_tech, new_tech = set(old.get("tech_stack", [])), set(new.get("tech_stack", []))
    for tech in sorted(new_tech - old_tech):
        add("tech_added", "tech_stack", None, tech)
    for tech in sorted(old_tech - new_tech):
        add("tech_removed", "tech_stack", tech, None)

    old_signals, new_signals = old.get("signals", {}), new.get("signals", {})
    for name, value in new_signals.items():
        if name in old_signals and old_signals[name] != value:
            add("signal_on" if value else "signal_off", name, old_signals[name], value)

    old_jobs = (old.get("hiring") or {}).get("job_links", 0)
    new_jobs = (new.get("hiring") or {}).get("job_links", 0)
    if old_jobs != new_jobs:
        add("job_links_changed", "hiring.job_links", old_jobs, new_jobs)

    old_hashes = {p["url"]: p.get("content_hash") for p in old.get("pages", []) if p.get("content_hash")}
    for page in new.get("pages", []):
        before = old_hashes.get(page["url"])
        if before and page.get("content_hash") and before != page["content_hash"]:
            add("content_changed", page["type"], before, page["content_hash"])
    return changes


async def run(settings, store, limit: int | None = None, force: bool = False,
              domains: list[str] | None = None, **_) -> dict:
    interval = float(settings.section("monitor").get("interval_days", 7))
    latest = store.latest("profiles")
    todo = [p for d, p in latest.items()
            if (domains and d in domains) or (not domains and (force or age_days(p["profiled_at"]) >= interval))]
    todo = todo[: limit or None]
    if not todo:
        return {"monitored": 0, "changes": 0}

    detector = TechDetector(settings.load_json("fingerprints.json"))
    domain_filter = DomainFilter(settings.section("domain_filter"))
    mode = settings.section("profile").get("fetch_mode", "browser")
    profiles, changes = [], []
    async with Fetcher(settings.section("crawl"), mode) as fetcher:
        async def one(old: dict) -> tuple[dict, list[Change]]:
            extra = {"category": old.get("category"), "tech_score": old.get("tech_score")}
            new = await build_profile(fetcher, old["domain"], old["url"], settings, store, detector,
                                      domain_filter, "monitor", extra)
            return new, diff_profiles(old, new)
        for new, diffs in await asyncio.gather(*(one(p) for p in todo)):
            profiles.append(new)
            changes.extend(c.to_dict() for c in diffs)
            logger.info("monitor %-28s %-11s %d change(s)", new["domain"], new["status"], len(diffs))
    store.append("profiles", profiles)
    store.append("changes", changes)
    return {"monitored": len(profiles), "changes": len(changes)}
