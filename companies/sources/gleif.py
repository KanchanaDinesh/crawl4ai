"""GLEIF LEI records (CC0): legal entities worldwide, by country. Names and countries, no websites.

    GET https://api.gleif.org/api/v1/lei-records?filter[entity.legalAddress.country]=LK
        &filter[entity.category]=GENERAL&filter[entity.status]=ACTIVE&page[size]=200&page[cursor]=*

Plain page numbers stop at 10,000 results, so we use GLEIF's cursor paging: each answer's
links.next is the next page, and that link is the cursor we save per country. GLEIF allows 60
requests a minute; we make one every delay_seconds (1.1s). Funds, branches and lapsed entities are
left out (category GENERAL, status ACTIVE). The US alone has ~300,000, so a country takes many runs:
the priority countries take turns with the rest of the world (see Rotation).

Cursor per country: {"next": url or null, "pages": n, "pass_started", "done_at"}.

Names: legalName is the name as registered, often in the country's own script (国泰金融控股股份有限公司). A record
can also give otherNames (an English legal or trading name) and transliteratedOtherNames (the company's own Latin
spelling, "PREFERRED_ASCII_..."). Those go into extra as english_name / latin_name, and PostgreSQL shows them where
the legal name isn't in Latin letters (companies.english_name in warehouse/schema/companies.sql). GLEIF's
automatic transliterations ("AUTO_ASCII_...") are machine-made and often unreadable, so they're not used.
`fill_english_names` asks again for the records saved before this, a list of LEIs per request.
"""

import asyncio
import logging
import time
from urllib.parse import urlencode

from . import Batch, Refused, Rotation, Source, SourceError, company, new_pass, now_iso, request

logger = logging.getLogger("companies")


def first_url(api: str, country: str, config: dict) -> str:
    params = {"filter[entity.legalAddress.country]": country, "page[size]": int(config.get("page_size", 200)),
              "page[cursor]": "*"}
    if config.get("category"):
        params["filter[entity.category]"] = config["category"]
    if config.get("status"):
        params["filter[entity.status]"] = config["status"]
    return f"{api.rstrip('/')}/lei-records?{urlencode(params)}"


def _clean(name) -> str:
    return " ".join(str(name or "").split())


def other_names(entity: dict) -> dict:
    """{"english_name": ..., "latin_name": ...} from a record's other names; only those that differ from the legal name.

    English: an ALTERNATIVE_LANGUAGE_LEGAL_NAME in English first, then an English trading name; never a previous name.
    Latin: the PREFERRED_ASCII_TRANSLITERATED_LEGAL_NAME, which the company itself gave."""
    legal = _clean((entity.get("legalName") or {}).get("name")).casefold()
    order = {"ALTERNATIVE_LANGUAGE_LEGAL_NAME": 0, "TRADING_OR_OPERATING_NAME": 1}
    english = sorted((order[o.get("type")], _clean(o.get("name"))) for o in entity.get("otherNames") or []
                     if o.get("type") in order and str(o.get("language") or "").lower().startswith("en") and _clean(o.get("name")))
    latin = [_clean(t.get("name")) for t in entity.get("transliteratedOtherNames") or []
             if t.get("type") == "PREFERRED_ASCII_TRANSLITERATED_LEGAL_NAME" and _clean(t.get("name"))]
    out = {"english_name": english[0][1] if english else None, "latin_name": latin[0] if latin else None}
    return {k: v for k, v in out.items() if v and v.casefold() != legal}


def parse(data: dict, country: str) -> list[dict]:
    out = []
    for rec in data.get("data") or []:
        a = rec.get("attributes") or {}
        e = a.get("entity") or {}
        addr = e.get("legalAddress") or {}
        form = e.get("legalForm") or {}
        out.append(company("gleif", a.get("lei") or rec.get("id"), (e.get("legalName") or {}).get("name"),
                           addr.get("country") or country, None, None,
                           city=(addr.get("city") or "").title() or None, legal_form=form.get("other") or form.get("id"),
                           registered_as=e.get("registeredAs"), jurisdiction=e.get("jurisdiction"),
                           created=(e.get("creationDate") or "")[:10] or None, **other_names(e)))
    return [c for c in out if c["source_id"] and c["source_id"] != "None"]


class Gleif(Source):
    name = "gleif"
    unit_seconds = 5

    async def batches(self, cursors: dict[str, dict]):
        api = self.config.get("api", "https://api.gleif.org/api/v1").rstrip("/")
        order = [c for c in self.ctx.country_order() if self.due(cursors.get(c))]
        state = {}
        for c in order:
            cur = cursors.get(c)
            if not cur or cur.get("done_at") or not str(cur.get("next") or "").startswith(api):
                cur = {**new_pass(), "next": first_url(api, c, self.config), "pages": 0}
            state[c] = cur
        rotation = Rotation(order, self.ctx.priority, (cursors.get("_rotation") or {}).get("other_pos", 0))
        for code in rotation:
            if not self.has_time():
                return
            cur = state[code]
            data = await request(self.ctx.session, "GLEIF", cur["next"], timeout=60)
            found = parse(data, code)
            nxt = (data.get("links") or {}).get("next")
            cur["pages"] += 1
            if nxt and found and str(nxt).startswith(api):
                cur["next"] = nxt
            else:
                cur.update(next=None, done_at=now_iso())
                rotation.drop(code)
            yield Batch(found, {code: dict(cur), "_rotation": {"other_pos": rotation.other_pos}},
                        f"{code} page {cur['pages']}")
            if not await self.pause(float(self.config.get("delay_seconds", 1.1))):
                return


def needs_english_name(store, limit: int | None = None) -> list[str]:
    """LEIs of GLEIF companies whose name isn't in Latin letters and that have no English name saved yet."""
    sql = """select source_id from companies.companies
             where source = 'gleif' and name_script in ('mixed', 'other') and english_name is null
               and not (coalesce(extra, '{}'::jsonb) ? 'english_name' or coalesce(extra, '{}'::jsonb) ? 'latin_name')
             order by source_id"""
    return [r[0] for r in store.con.execute(sql + (f" limit {int(limit)}" if limit else "")).fetchall()]


async def fill_english_names(settings: dict, store, max_minutes: float = 5, limit: int | None = None) -> dict:
    """Ask GLEIF again, page_size LEIs per request, for the companies saved before their other names were kept,
    and save them the normal way (rule A for corrections, last_seen updated). One request every delay_seconds;
    a refusal (401/403/429) stops the run, and a later run continues with what's left. Some records have no English
    or Latin name at all: they're asked again on each run of this command (a few requests), never by `collect`."""
    import aiohttp
    config = settings.get("sources", {}).get("gleif", {})
    api = config.get("api", "https://api.gleif.org/api/v1").rstrip("/")
    size = max(1, min(int(config.get("page_size", 200)), 200))
    leis = needs_english_name(store, limit)
    summary = {"asked": 0, "found": 0, "requests": 0, "left": len(leis), "refused": None, "error": None}
    deadline = time.monotonic() + max_minutes * 60
    headers = {"User-Agent": settings.get("user_agent") or "crawl4ai-companies/1.0"}
    async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
        for i in range(0, len(leis), size):
            if time.monotonic() > deadline:
                break
            chunk = leis[i:i + size]
            params = {"filter[lei]": ",".join(chunk), "page[size]": size}
            try:
                data = await request(session, "GLEIF", f"{api}/lei-records?{urlencode(params)}", timeout=60)
            except Refused as e:
                summary["refused"] = str(e)
                break
            except SourceError as e:
                summary["error"] = str(e)
                break
            found = parse(data, "")
            store.upsert_companies(found)
            summary["requests"] += 1
            summary["asked"] += len(chunk)
            summary["found"] += sum(1 for c in found if c["extra"].get("english_name") or c["extra"].get("latin_name"))
            summary["left"] -= len(chunk)
            logger.info("gleif names: %d of %d asked, %d with an English or Latin name", summary["asked"], len(leis), summary["found"])
            if summary["left"] > 0:
                await asyncio.sleep(float(config.get("delay_seconds", 1.1)))
    return summary
