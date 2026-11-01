"""Stage 3: PROFILE qualified sites: what they are, what they use, what they're doing.

Crawls the homepage plus a few key pages (about, product, pricing, docs, blog,
careers, contact) found among its links, then records:
  name, description, category, headings, keywords
  tech stack (config/fingerprints.json), social links
  signals: has pricing / docs / blog / careers, hiring (job board links), open source (GitHub)
  external domains (fed back into discovery via the outlinks source)
Page text is saved under data/techintel/raw/pages/ for re-processing later.
"""

import asyncio
import logging
import re
from urllib.parse import urlsplit

from ..domains import DomainFilter, homepage_url, host_of, root_domain
from ..fetch import Fetcher, FetchResult
from ..models import utc_now
from ..pages import parse_page, strip_shared_lines, top_keywords
from ..techstack import TechDetector

logger = logging.getLogger(__name__)

SOCIAL_HOSTS = {
    "x.com": "x", "twitter.com": "x", "linkedin.com": "linkedin", "github.com": "github",
    "youtube.com": "youtube", "facebook.com": "facebook", "instagram.com": "instagram",
    "discord.gg": "discord", "discord.com": "discord", "medium.com": "medium", "t.me": "telegram",
}


def find_key_pages(links: list[dict], domain: str, key_pages: dict[str, list[str]]) -> dict[str, str]:
    """Pick one URL per page type, matched on path segments, subdomain, or link text."""
    found: dict[str, tuple[int, str]] = {}
    for link in links:
        href = link.get("href") or ""
        if not href.startswith("http") or root_domain(href) != domain:
            continue
        parts = urlsplit(href)
        segments = [s for s in parts.path.lower().split("/") if s]
        subdomain = host_of(href).split(".")[0]
        text = (link.get("text") or "").strip().lower()
        for page_type, words in key_pages.items():
            if any(seg.split(".")[0] in words for seg in segments[:2]) or subdomain in words or text in words:
                depth = len(segments)
                if page_type not in found or depth < found[page_type][0]:
                    found[page_type] = (depth, href.split("#")[0])
    return {k: v for k, (_, v) in found.items()}


def _name(page, domain: str) -> str:
    if page.site_name:
        return page.site_name
    title = re.split(r"\s[|\-–—:·]\s", page.title)[0].strip() if page.title else ""
    return title or domain


async def build_profile(fetcher: Fetcher, domain: str, url: str, settings, store,
                        detector: TechDetector, domain_filter: DomainFilter, kind: str = "initial",
                        extra: dict | None = None) -> dict:
    config = settings.section("profile")
    base = {"domain": domain, "url": url, "profiled_at": utc_now(), "kind": kind, **(extra or {})}

    home = await fetcher.fetch(url)
    if not home.success or (home.status or 0) >= 400:
        return {**base, "status": "unreachable", "error": home.error or f"HTTP {home.status}"}

    key_pages = find_key_pages(home.internal_links, domain, config.get("key_pages", {}))
    max_pages = int(config.get("max_pages", 6))
    page_urls = [u for u in dict.fromkeys(key_pages.values()) if u.rstrip("/") != (home.final_url or url).rstrip("/")]
    others: list[FetchResult] = await fetcher.fetch_many(page_urls[: max_pages - 1])

    url_type = {v: k for k, v in key_pages.items()}
    pages, texts, tech, external = [], [], {}, []
    home_info = None
    for result in [home, *others]:
        info = parse_page(result.html, result.markdown) if result.success else None
        page_type = "home" if result is home else url_type.get(result.url, "other")
        if result is home:
            home_info = info
        entry = {"type": page_type, "url": result.url, "status": result.status, "ok": result.success}
        if info:
            texts.append(info.text)
            entry.update(title=info.title[:200], word_count=info.word_count, content_hash=info.content_hash,
                         raw_file=store.write_raw_page(domain, result.url, info.text))
            for t in detector.detect(result.html, result.headers if result is home else {}, info.generator):
                tech[t["name"]] = t["group"]
            external.extend(result.external_links)
        pages.append(entry)

    # Links to other sites: social profiles, job boards, and everything else.
    social: dict[str, str] = {}
    ats_hosts = tuple(config.get("ats_hosts", []))
    ats, job_links, external_domains = None, 0, []
    for link in external:
        href = link.get("href") or ""
        host = host_of(href)
        root = root_domain(href)
        if not root:
            continue
        if root in SOCIAL_HOSTS:
            social.setdefault(SOCIAL_HOSTS[root], href)
        elif host.endswith(ats_hosts) or root in ats_hosts:
            ats = ats or root
            job_links += 1
        elif not domain_filter.is_ignored(root) and root != domain and root not in external_domains:
            external_domains.append(root)
    careers_url = key_pages.get("careers", "")
    job_links += sum(1 for link in home.internal_links + [l for r in others for l in r.internal_links]
                     if careers_url and (link.get("href") or "").startswith(careers_url.rstrip("/") + "/"))

    signals = {
        "has_pricing": "pricing" in key_pages,
        "has_docs": "docs" in key_pages,
        "has_blog": "blog" in key_pages,
        "has_careers": "careers" in key_pages,
        "hiring": job_links > 0,
        "open_source": "github" in social,
    }
    return {
        **base,
        "status": "ok",
        "final_url": home.final_url,
        "name": _name(home_info, domain),
        "description": home_info.description or (home_info.headings[0] if home_info.headings else ""),
        "headings": home_info.headings[:15],
        "key_pages": key_pages,
        "pages": pages,
        "tech_stack": sorted(tech),
        "tech_groups": tech,
        "social": social,
        "signals": signals,
        "hiring": {"job_board": ats, "job_links": job_links},
        "keywords": top_keywords(strip_shared_lines(texts) if len(texts) > 1 else texts,
                                 int(config.get("top_keywords", 20))),
        "word_count": sum(p.get("word_count", 0) for p in pages),
        "external_domains": external_domains[:100],
    }


async def run(settings, store, limit: int | None = None, domains: list[str] | None = None, **_) -> dict:
    profiled = store.keys("profiles")
    qualifications = store.latest("qualifications")
    todo = [q for d, q in qualifications.items()
            if q["status"] == "qualified" and (d in (domains or []) or (not domains and d not in profiled))]
    todo = todo[: limit or None]
    if not todo:
        return {"profiled": 0}

    detector = TechDetector(settings.load_json("fingerprints.json"))
    domain_filter = DomainFilter(settings.section("domain_filter"))
    mode = settings.section("profile").get("fetch_mode", "browser")
    rows = []
    async with Fetcher(settings.section("crawl"), mode) as fetcher:
        async def one(q: dict) -> dict:
            url = q.get("final_url") or q.get("url") or homepage_url(q["domain"])
            extra = {"category": q.get("category"), "tech_score": q.get("score")}
            return await build_profile(fetcher, q["domain"], url, settings, store, detector, domain_filter,
                                       "initial", extra)
        for p in await asyncio.gather(*(one(q) for q in todo)):
            rows.append(p)
            logger.info("profile %-28s %-11s %s", p["domain"], p["status"],
                        ", ".join(p.get("tech_stack", [])[:6]) or p.get("error", ""))
    store.append("profiles", rows)
    ok = sum(1 for p in rows if p["status"] == "ok")
    return {"profiled": len(rows), "ok": ok, "unreachable": len(rows) - ok}
