"""COMPANY facts for qualified sites, from what each company publishes about itself.

For each qualified site (newest first) it reads the homepage plus its about, team and contact pages
(plain HTTP, no browser, robots.txt respected) and saves one row in the `companies` table:
  social           the company's accounts: LinkedIn, Facebook, X, Instagram, YouTube, GitHub, ... (links)
  linkedin_people  personal LinkedIn pages the company links to from its own pages (links)
  people           leadership named on its own pages: CEO, founders, directors
  employees        schema.org numberOfEmployees, else a phrase like "a team of 25" as the site writes it
  founded, legal_name, address, emails, phones   from the page's schema.org data and contact links
  github           public numbers from GitHub's API for its GitHub account: repos, followers, since when

LinkedIn, Facebook and the other social sites are never fetched: their terms forbid automated
collection, and most of it needs a login. The links are kept, so each profile is one click away.
GitHub's API is public and made for programs. If it refuses (403/429, 60 lookups an hour without
a token), it isn't asked again this run, and those sites get their GitHub numbers on a later run.
Set GITHUB_TOKEN (a token with no permissions is enough) for 5,000 an hour.

A site is checked again after company.recheck_days; one that couldn't be read after retry_failed_days.
"""

import asyncio
import logging
import os
from urllib.parse import urlsplit

import aiohttp

from .. import company as extract
from ..domains import homepage_url
from ..fetch import HONEST_USER_AGENT, Fetcher
from ..models import utc_now
from ..pages import parse_page
from . import age_days, process_items
from .profile import _name, find_key_pages

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com/users/{login}"
GITHUB_NOT_ACCOUNTS = {"orgs", "sponsors", "features", "about", "pricing", "topics", "marketplace", "apps",
                       "login", "signup", "enterprise", "security", "site", "collections", "explore"}
DEFAULT_KEY_PAGES = {
    "about": ["about", "about-us", "company", "who-we-are", "our-story"],
    "team": ["team", "our-team", "people", "leadership", "founders", "management"],
    "contact": ["contact", "contact-us"],
}


class GitHubRefused(RuntimeError):
    pass


def github_login(url: str | None) -> str | None:
    """github.com/acme or github.com/orgs/acme -> "acme"; a repo link gives its owner."""
    if not url:
        return None
    parts = [p for p in urlsplit(url).path.split("/") if p]
    if parts[:1] == ["orgs"]:
        parts = parts[1:]
    return parts[0] if parts and parts[0].lower() not in GITHUB_NOT_ACCOUNTS else None


async def github_account(session: aiohttp.ClientSession, api: str, login: str) -> dict:
    """GitHub's public numbers for a user or organisation account."""
    async with session.get(api.format(login=login), timeout=aiohttp.ClientTimeout(total=20)) as r:
        if r.status in (403, 429) or (r.status == 200 and r.headers.get("X-RateLimit-Remaining") == "0"):
            reset = r.headers.get("X-RateLimit-Reset")
            raise GitHubRefused(f"GitHub's API said HTTP {r.status}" + (" (hourly limit reached)" if reset else ""))
        if r.status == 404:
            return {"login": login, "error": "no such GitHub account"}
        if r.status != 200:
            return {"login": login, "error": f"HTTP {r.status}"}
        data = await r.json(content_type=None)
    keep = ("login", "type", "name", "public_repos", "followers", "created_at", "location", "blog", "email",
            "twitter_username", "html_url")
    return {k: data.get(k) for k in keep if data.get(k) not in (None, "")}


def merge_pages(domain: str, pages: list) -> dict:
    """Combine what the homepage and the about/team/contact pages say. The homepage counts first.

    Names with a role are only taken from the other pages: on a homepage they're mostly testimonials
    ("Jane Doe, Founder of SomeOtherCo"). Founders in the page's schema.org data count from any page."""
    org: dict = {}
    links, people, emails, phones, texts = [], [], [], [], []
    for result in pages:
        if not result.success:
            continue
        org = org or extract.organization(result.html)
        links += result.external_links
        info = parse_page(result.html, result.markdown)
        texts.append(info.text)
        if result is not pages[0]:
            people += [p for p in extract.people(result.html) if p["name"] not in {q["name"] for q in people}]
        found = extract.contacts(result.html, info.text)
        emails += [e for e in found["emails"] if e not in emails]
        phones += [p for p in found["phones"] if p not in phones]
    social, linkedin_people = extract.social_links(links)
    more, _ = extract.social_links([{"href": u} for u in org.get("same_as", []) if isinstance(u, str)])
    for platform, url in more.items():
        social.setdefault(platform, url)
    if org.get("email") and org["email"].lower() not in emails:
        emails.insert(0, org["email"].lower())
    if org.get("telephone") and org["telephone"] not in phones:
        phones.insert(0, org["telephone"])
    for name in org.get("founders", []):
        if name not in {p["name"] for p in people}:
            people.append({"name": name, "role": "founder"})
    employees = org.get("employees")
    return {
        "name": org.get("name") or _name(parse_page(pages[0].html), domain),
        "legal_name": org.get("legal_name"),
        "founded": org.get("founding_date"),
        "employees": employees,
        "employees_source": "schema.org" if employees else None,
        "employees_hint": None if employees else extract.employee_hint("\n".join(texts)),
        "address": org.get("address"),
        "people": people[:15],
        "emails": emails[:10],
        "phones": phones[:5],
        "social": social,
        "linkedin_people": linkedin_people[:20],
    }


async def check_site(fetcher: Fetcher, q: dict, key_pages: dict, max_pages: int) -> dict:
    domain = q["domain"]
    url = q.get("final_url") or q.get("url") or homepage_url(domain)
    row = {"domain": domain, "url": url, "checked_at": utc_now()}
    home = await fetcher.fetch(url)
    if not home.success or (home.status or 0) >= 400:
        error = home.error or f"HTTP {home.status}"
        status = "not_allowed" if "robots" in error.lower() else "unreachable"
        return {**row, "status": status, "error": error}
    found = find_key_pages(home.internal_links, domain, key_pages)
    others = [u for u in dict.fromkeys(found.values()) if u.rstrip("/") != (home.final_url or url).rstrip("/")]
    pages = [home, *await fetcher.fetch_many(others[: max_pages - 1])]
    return {**row, "status": "ok", "final_url": home.final_url, "pages": [p.url for p in pages if p.success],
            **merge_pages(domain, pages)}


def _describe(r: dict) -> str:
    if r["status"] != "ok":
        return f"{r['domain']:<28} {r['status']:<11} {(r.get('error') or '')[:60]}"
    bits = [",".join(sorted(r["social"])) or "no social links"]
    if r["people"]:
        bits.append(f"{len(r['people'])} people")
    if r.get("employees") or r.get("employees_hint"):
        bits.append(f"employees: {r.get('employees') or r.get('employees_hint')}")
    if (r.get("github") or {}).get("public_repos") is not None:
        bits.append(f"github {r['github']['public_repos']} repos")
    return f"{r['domain']:<28} ok          " + "; ".join(bits)


def _due(domain: str, last: dict | None, config: dict) -> bool:
    if last is None:
        return True
    days = config.get("recheck_days", 30) if last.get("status") in ("ok", "not_allowed") \
        else config.get("retry_failed_days", 3)
    return age_days(last.get("checked_at")) >= float(days)


async def run(settings, store, limit: int | None = None, domains: list[str] | None = None,
              max_minutes: float | None = None, **_) -> dict:
    config = settings.section("company")
    crawl = settings.section("crawl")
    companies = store.latest("companies")
    qualified = sorted((q for q in store.latest("qualifications").values() if q["status"] == "qualified"),
                       key=lambda q: q.get("checked_at") or "", reverse=True)     # newest first
    if domains:
        todo = [q for q in qualified if q["domain"] in domains]
    else:
        todo = [q for q in qualified if _due(q["domain"], companies.get(q["domain"]), config)]
    # Sites read before, whose GitHub numbers are still missing because GitHub refused us then
    github_backlog = [r for d, r in companies.items() if r.get("status") == "ok" and r["social"].get("github")
                      and r.get("github") is None and d not in {q["domain"] for q in todo}]
    todo = todo[: limit or None]
    if not todo and not github_backlog:
        return {"checked": 0}

    api = config.get("github_api", GITHUB_API)
    headers = {"User-Agent": config.get("user_agent") or HONEST_USER_AGENT, "Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    refused: list[str] = []
    counts = {"ok": 0, "unreachable": 0, "not_allowed": 0, "github": 0}

    async def add_github(session, row: dict) -> None:
        login = github_login(row["social"].get("github"))
        if not login or refused or not config.get("github_enabled", True):
            return
        try:
            row["github"] = await github_account(session, api, login)
            counts["github"] += "error" not in row["github"]
        except GitHubRefused as e:
            refused.append(str(e))
            logger.warning("company: %s. Not asking GitHub again this run; the GitHub numbers are added on a "
                           "later run (set GITHUB_TOKEN for a higher limit).", e)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            row["github"] = {"login": login, "error": str(e)[:120] or type(e).__name__}

    key_pages = config.get("key_pages") or DEFAULT_KEY_PAGES
    max_pages = int(config.get("max_pages", 4))
    async with aiohttp.ClientSession(trust_env=True, headers=headers) as session:
        for row in github_backlog:
            if refused:
                break
            await add_github(session, row)
            if row.get("github") is not None:
                store.append("companies", [row])

        async with Fetcher(crawl, config.get("fetch_mode", "http")) as fetcher:
            async def one(q: dict) -> dict:
                row = await check_site(fetcher, q, key_pages, max_pages)
                if row["status"] == "ok":
                    await add_github(session, row)
                return row

            def save(row: dict) -> None:
                counts[row["status"]] += 1
                store.append("companies", [row])

            out = await process_items(todo, one, stage="company", on_result=save, describe=_describe,
                                      concurrency=int(config.get("concurrency") or crawl.get("concurrency", 4)),
                                      max_minutes=max_minutes or crawl.get("max_stage_minutes"))
    results = [r for r in out["results"] if r["status"] == "ok"]
    summary = {"checked": len(out["results"]), **{k: v for k, v in counts.items() if k != "github"},
               "with_social": sum(1 for r in results if r["social"]),
               "with_linkedin": sum(1 for r in results if "linkedin" in r["social"]),
               "with_people": sum(1 for r in results if r["people"]),
               "with_employees": sum(1 for r in results if r.get("employees") or r.get("employees_hint")),
               "github_numbers": counts["github"], "left_for_next_run": out["skipped"], "seconds": out["seconds"]}
    if refused:
        summary["github_refused"] = refused[0]
    return summary
