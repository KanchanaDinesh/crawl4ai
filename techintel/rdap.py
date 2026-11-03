"""Domain registration data via RDAP (the JSON successor to WHOIS).

Each registry runs its own RDAP server; IANA publishes which one serves which TLD
(https://data.iana.org/rdap/dns.json, cached for a week). We ask the registry directly:
rdap.org, a redirect service, refused us after a dozen lookups (HTTP 429), while the
registries answer quickly and each has its own limits. rdap.org is only used if IANA's
list can't be loaded. Many country TLDs (.io, .co, .sh, .lk, ...) have no public RDAP
service at all; those domains are recorded without a date, with no request made.
"""

import json
import time
from pathlib import Path

import aiohttp

IANA_BOOTSTRAP = "https://data.iana.org/rdap/dns.json"


def servers_from_bootstrap(data: dict) -> dict[str, str]:
    """{tld: RDAP base URL} from IANA's bootstrap file."""
    return {tld.lower(): urls[0] for tlds, urls in data.get("services", []) if urls for tld in tlds}


async def load_servers(session: aiohttp.ClientSession, cache: Path, url: str = IANA_BOOTSTRAP,
                       max_age_days: float = 7, timeout: float = 30) -> dict[str, str]:
    """IANA's RDAP server list, from the cache if it's less than a week old. A stale cache is
    still used if the download fails; with no cache at all the error is raised."""
    fresh = cache.exists() and time.time() - cache.stat().st_mtime < max_age_days * 86400
    if not fresh:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data), encoding="utf-8")
            return servers_from_bootstrap(data)
        except (aiohttp.ClientError, TimeoutError, ValueError):
            if not cache.exists():
                raise
    return servers_from_bootstrap(json.loads(cache.read_text(encoding="utf-8")))


def url_template_for(domain: str, servers: dict[str, str] | None, fallback: str) -> str | None:
    """The lookup URL template for a domain: its registry's server, the fallback when there's no
    IANA list, or None when its TLD has no RDAP service."""
    if servers is None:
        return fallback
    base = servers.get(domain.rsplit(".", 1)[-1].lower())
    return f"{base.rstrip('/')}/domain/{{domain}}" if base else None


def parse_rdap(data: dict) -> dict:
    """Pick the useful dates and the registrar out of an RDAP domain response."""
    events = {e.get("eventAction"): e.get("eventDate") for e in data.get("events", []) if isinstance(e, dict)}
    registrar = None
    for entity in data.get("entities", []):
        if "registrar" in (entity.get("roles") or []):
            vcard = entity.get("vcardArray") or []
            for item in (vcard[1] if len(vcard) > 1 else []):
                if item and item[0] == "fn":
                    registrar = item[3]
                    break
            break
    return {
        "registered_at": events.get("registration"),
        "expires_at": events.get("expiration"),
        "updated_at": events.get("last changed"),
        "registrar": registrar,
    }


class Refused(Exception):
    """The RDAP service refused us (401/403/429): back off, don't record it as the domain's answer."""

    def __init__(self, status: int, host: str):
        super().__init__(f"{host} refused the request (HTTP {status})")
        self.status = status


async def lookup(session: aiohttp.ClientSession, domain: str, url_template: str, timeout: float) -> dict:
    url = url_template.format(domain=domain)
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout),
                           headers={"Accept": "application/rdap+json, application/json"}) as resp:
        if resp.status == 404:
            raise LookupError("no RDAP record (TLD may not support RDAP)")
        if resp.status in (401, 403, 429):
            raise Refused(resp.status, resp.url.host or "the RDAP server")
        resp.raise_for_status()
        return parse_rdap(await resp.json(content_type=None))
