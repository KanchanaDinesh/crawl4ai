"""Domain registration data via RDAP (the JSON successor to WHOIS).

https://rdap.org/domain/<name> redirects to the right registry. Generic TLDs
(.com, .net, .org, .app, .dev, ...) are covered; many country TLDs (including
.lk) have no public RDAP service, so for those the lookup returns an error and
the registration date stays empty.
"""

import aiohttp


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
