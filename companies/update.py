"""UPDATE: read each company's own website and keep what it says about itself.

For companies with a website (one check per domain, however many companies share it): the homepage
plus its about, team and contact pages, over plain HTTP (no browser), robots.txt respected, with an
honest user agent, one request per second per site. The facts come from techintel's extractors
(techintel/company.py via techintel.stages.company.merge_pages): social account links, people named
with a role, employees, emails and phones, founded, legal name, address (schema.org).

LinkedIn, Facebook and the other social sites are never fetched: only the links the company itself
publishes are kept.

Results: ok, not_allowed (robots.txt says no), unreachable. A site that refuses us (HTTP 401, 403
or 429) is not a result: it is only noted in `refusals`, and tried again after refused_wait_days.
Sites are read again after recheck_days (retry_failed_days if they couldn't be read).
"""

import logging
import time

from techintel.domains import host_of, root_domain
from techintel.fetch import Fetcher
from techintel.stages import process_items
from techintel.stages.company import merge_pages
from techintel.stages.profile import find_key_pages

from .store import now_iso

logger = logging.getLogger("companies")

REFUSED = (401, 403, 429)


def skipped(domain: str, url: str, skip: list[str]) -> bool:
    """Websites that are really a page on a platform (facebook.com/acme, acme.blogspot.com, ...)."""
    host = host_of(url)
    return any(domain == s or host == s or host.endswith("." + s) for s in skip)


async def read_site(fetcher: Fetcher, item: dict, key_pages: dict, max_pages: int) -> dict:
    url = item["website"]
    row = {"domain": item["domain"], "url": url, "checked_at": now_iso()}
    home = await fetcher.fetch(url)
    if not home.success or (home.status or 0) >= 400:
        error = home.error or f"HTTP {home.status}"
        if "robots" in error.lower():            # crawl4ai reports a robots.txt "no" as status 403 too
            return {**row, "status": "not_allowed", "error": "robots.txt disallows crawling"}
        if home.status in REFUSED:
            return {**row, "status": "refused", "error": f"HTTP {home.status}"}
        return {**row, "status": "unreachable", "error": error}
    final = home.final_url or url
    domain = root_domain(final) or item["domain"]
    found = find_key_pages(home.internal_links, domain, key_pages)
    others = [u for u in dict.fromkeys(found.values()) if u.rstrip("/") != final.rstrip("/")]
    pages = [home, *await fetcher.fetch_many(others[: max_pages - 1])]
    facts = merge_pages(item["domain"], [p for p in pages if p.success and (p.status or 200) < 400] or [home])
    return {**row, "status": "ok", "final_url": final, "pages": [p.url for p in pages if p.success], **facts}


def _describe(r: dict) -> str:
    if r["status"] != "ok":
        return f"{r['domain']:<30} {r['status']:<11} {(r.get('error') or '')[:60]}"
    bits = [",".join(sorted(r.get("social") or {})) or "no social links"]
    if r.get("people"):
        bits.append(f"{len(r['people'])} people")
    if r.get("employees") or r.get("employees_hint"):
        bits.append(f"employees: {r.get('employees') or r.get('employees_hint')}")
    return f"{r['domain']:<30} ok          " + "; ".join(bits)


async def update(settings: dict, store, limit: int | None = None, countries: list[str] | None = None,
                 domains: list[str] | None = None, max_minutes: float | None = None) -> dict:
    started_at, t0 = now_iso(), time.monotonic()
    config = settings.get("update", {})
    crawl = dict(settings.get("crawl", {}))
    crawl.setdefault("user_agent", settings.get("user_agent"))
    max_minutes = float(max_minutes or config.get("max_minutes", 10))
    concurrency = int(config.get("concurrency") or crawl.get("concurrency", 8))
    # enough sites for the time limit (a site takes a few seconds), never the whole table
    want = limit or max(50, int(max_minutes * 60 * concurrency / 3))
    todo = store.due_sites(config, settings.get("priority_countries", []), want,
                           [c.upper() for c in countries] if countries else None, domains)
    skip = [s.lower() for s in config.get("skip_domains", [])]
    todo = [t for t in todo if not skipped(t["domain"], t["website"], skip)]
    counts = {"ok": 0, "unreachable": 0, "not_allowed": 0, "refused": 0}
    if not todo:
        logger.info("update: no websites due (every company website was read recently)")
        summary = {"checked": 0, **counts, "seconds": round(time.monotonic() - t0, 1)}
        store.record_run("update", started_at, summary["seconds"], summary)
        return summary

    key_pages = config.get("key_pages") or {}
    max_pages = int(config.get("max_pages", 4))

    def save(row: dict) -> None:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        if row["status"] == "refused":            # not a result: only "don't ask again for a while"
            store.save_refusal(row["domain"], row.get("error") or "refused")
        else:
            store.save_site(row)

    async with Fetcher(crawl, "http") as fetcher:
        out = await process_items(todo, lambda item: read_site(fetcher, item, key_pages, max_pages),
                                  stage="update", on_result=save, describe=_describe,
                                  concurrency=concurrency, max_minutes=max_minutes)
    ok = [r for r in out["results"] if r["status"] == "ok"]
    summary = {"checked": len(out["results"]), **counts,
               "with_social": sum(1 for r in ok if r.get("social")),
               "with_linkedin": sum(1 for r in ok if "linkedin" in (r.get("social") or {})),
               "with_people": sum(1 for r in ok if r.get("people")),
               "with_employees": sum(1 for r in ok if r.get("employees") or r.get("employees_hint")),
               "left_for_next_run": out["skipped"], "seconds": round(time.monotonic() - t0, 1)}
    store.record_run("update", started_at, summary["seconds"], summary)
    return summary
