"""GLEIF LEI records (CC0): legal entities worldwide, by country. Names and countries, no websites.

    GET https://api.gleif.org/api/v1/lei-records?filter[entity.legalAddress.country]=LK
        &filter[entity.category]=GENERAL&filter[entity.status]=ACTIVE&page[size]=200&page[cursor]=*

Plain page numbers stop at 10,000 results, so we use GLEIF's cursor paging: each answer's
links.next is the next page, and that link is the cursor we save per country. GLEIF allows 60
requests a minute; we make one every delay_seconds (1.1s). Funds, branches and lapsed entities are
left out (category GENERAL, status ACTIVE). The US alone has ~300,000, so a country takes many runs:
the priority countries take turns with the rest of the world (see Rotation).

Cursor per country: {"next": url or null, "pages": n, "pass_started", "done_at"}.
"""

import logging
from urllib.parse import urlencode

from . import Batch, Rotation, Source, company, new_pass, now_iso, request

logger = logging.getLogger("companies")


def first_url(api: str, country: str, config: dict) -> str:
    params = {"filter[entity.legalAddress.country]": country, "page[size]": int(config.get("page_size", 200)),
              "page[cursor]": "*"}
    if config.get("category"):
        params["filter[entity.category]"] = config["category"]
    if config.get("status"):
        params["filter[entity.status]"] = config["status"]
    return f"{api.rstrip('/')}/lei-records?{urlencode(params)}"


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
                           created=(e.get("creationDate") or "")[:10] or None))
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
