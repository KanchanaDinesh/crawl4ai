"""Stage 2: QUALIFY candidates with a cheap homepage check.

Fetches each homepage in HTTP mode (no browser), falling back to the browser
for JavaScript-only sites, then the rule classifier decides:
  qualified  real content, enough tech keywords -> goes on to profiling
  watch      placeholder / "coming soon" / unreachable -> re-checked after watch_recheck_days
  rejected   parked, for sale, not tech -> re-checked after recheck_after_days
"""

import asyncio
import logging
from contextlib import AsyncExitStack

from ..classify import RuleClassifier
from ..domains import DomainFilter, homepage_url, host_of, root_domain
from ..fetch import Fetcher, FetchResult
from ..models import Qualification
from ..pages import parse_page
from . import age_days, process_items

logger = logging.getLogger(__name__)


def due_candidates(store, config: dict, recheck: bool = False, domains: list[str] | None = None) -> list[dict]:
    qualifications = store.latest("qualifications")
    due = []
    for domain, cand in store.latest("candidates").items():
        if domains and domain not in domains:
            continue
        last = qualifications.get(domain)
        if last is None or recheck or domains:
            due.append(cand)
        elif last["status"] == "watch" and age_days(last["checked_at"]) >= config.get("watch_recheck_days", 7):
            due.append(cand)
        elif last["status"] == "rejected" and age_days(last["checked_at"]) >= config.get("recheck_after_days", 30):
            due.append(cand)
    return due


def evaluate(cand: dict, result: FetchResult, classifier: RuleClassifier, domain_filter: DomainFilter) -> Qualification:
    base = dict(domain=cand["domain"], url=result.url, fetch_mode=result.mode,
                final_url=result.final_url, http_status=result.status)
    if not result.success:
        error = result.error or "unknown error"
        if "robots" in error.lower():  # friendly_error keeps the word "robots"
            return Qualification(status="rejected", reason="robots.txt disallows crawling", **base)
        # crawl4ai can flag small, simple pages as possible bot-blocks while still returning the HTML.
        # If the page has real content (or is clearly parked / a placeholder), judge it on its content.
        if result.html:
            page = parse_page(result.html, result.markdown)
            verdict = classifier.assess(page, host_of(result.final_url or result.url))
            if page.word_count >= classifier.min_words or verdict.reason.startswith(
                    ("parked", "placeholder", "redirects to parking")):
                return Qualification(
                    status=verdict.status, reason=verdict.reason, title=page.title[:300],
                    description=page.description[:500], word_count=page.word_count, category=verdict.category,
                    score=verdict.score, scores=verdict.scores, matched=verdict.matched, **base)
        return Qualification(status="watch", reason=f"unreachable: {error[:160]}", **base)
    if result.status and result.status >= 400:
        return Qualification(status="watch", reason=f"HTTP {result.status}", **base)

    final_host = host_of(result.final_url or result.url)
    final_root = root_domain(final_host)
    if final_root and final_root != cand["domain"] and domain_filter.is_ignored(final_root):
        return Qualification(status="rejected", reason=f"redirects to platform {final_root}", **base)

    page = parse_page(result.html, result.markdown)
    verdict = classifier.assess(page, final_host)
    reason = verdict.reason
    if final_root and final_root != cand["domain"]:
        reason += f" (redirects to {final_root})"
    return Qualification(
        status=verdict.status, reason=reason, title=page.title[:300], description=page.description[:500],
        word_count=page.word_count, category=verdict.category, score=verdict.score,
        scores=verdict.scores, matched=verdict.matched, **base,
    )


def needs_browser(result: FetchResult, min_words: int) -> bool:
    """Retry in a real browser only when the page loaded but is nearly empty (likely a JavaScript site).

    Never after "forbidden / too many requests / unavailable" (401/403/429/503): the site said no,
    and the project backs off instead of getting past a bot check. Such sites stay on "watch".
    Nor for DNS failures, refused connections, timeouts or robots.txt: a browser won't fix those,
    and new domains from certificate logs very often aren't live yet.
    """
    if result.success and (result.status or 200) < 400:
        return parse_page(result.html, result.markdown).word_count < min_words
    return False


async def run(settings, store, limit: int | None = None, recheck: bool = False,
              domains: list[str] | None = None, max_minutes: float | None = None, **_) -> dict:
    config = settings.section("qualify")
    todo = due_candidates(store, config, recheck, domains)[: limit or None]
    if not todo:
        return {"checked": 0}

    classifier = RuleClassifier(settings.section("categories"), config)
    domain_filter = DomainFilter(settings.section("domain_filter"))
    crawl = settings.section("crawl")
    min_words = config.get("min_words", 80)
    counts = {"qualified": 0, "watch": 0, "rejected": 0}
    browser_retries = 0

    async with AsyncExitStack() as stack:
        primary = await stack.enter_async_context(Fetcher(
            crawl, config.get("fetch_mode", "http"), concurrency=config.get("concurrency"),
            timeout_ms=config.get("timeout_ms")))
        browser: Fetcher | None = None
        browser_lock = asyncio.Lock()

        async def check(cand: dict) -> Qualification:
            nonlocal browser, browser_retries
            url = cand.get("url") or homepage_url(cand["domain"])
            result = await primary.fetch(url)
            if primary.mode == "http" and config.get("browser_fallback", True) and needs_browser(result, min_words):
                async with browser_lock:
                    if browser is None:
                        browser = await stack.enter_async_context(Fetcher(crawl, "browser"))
                if not browser.dead:
                    browser_retries += 1
                    retried = await browser.fetch(url)
                    if not retried.browser_failed:    # a crashed browser says nothing: keep the HTTP answer
                        result = retried
            return evaluate(cand, result, classifier, domain_filter)

        def save(q: Qualification) -> None:
            counts[q.status] += 1
            store.append("qualifications", [q.to_dict()])

        out = await process_items(
            todo, check, stage="qualify", on_result=save,
            describe=lambda q: f"{q.domain:<28} {q.status:<9} {q.reason[:70]}",
            concurrency=int(config.get("concurrency") or crawl.get("concurrency", 4)),
            max_minutes=max_minutes or crawl.get("max_stage_minutes"))

    return {"checked": len(out["results"]), **counts, "browser_retries": browser_retries,
            "left_for_next_run": out["skipped"], "seconds": out["seconds"]}
