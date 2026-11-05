"""Wikidata (CC0): companies with an official website (P856) in a country (P17).

One SPARQL query per country and company type (company, public company, business, manufacturer,
bank, ...; settings "types"). A whole country in one query is too slow for the US and the UK (the
service stops queries after 60 seconds), but one type at a time takes 2-35 seconds.

The query service allows each client 60 seconds of query time per minute, so after each query we
wait as long as it took (at least min_wait_seconds). It asks programs for an honest User-Agent with
a contact (ours has the project's GitHub address). Its robots.txt keeps search-engine crawlers out
of /sparql; the service itself is offered to programs under the Wikimedia User-Agent policy.
A 429 means "slow down": the source stops for this run.

Cursor per country: {"next_type": i, "pass_started", "done_at", "failed": [types that timed out]}.
"""

import logging
import time

from . import Batch, Rotation, Source, SourceError, company, new_pass, now_iso, request

logger = logging.getLogger("companies")

QUERY = """SELECT ?item ?website ?name ?industry WHERE {
  hint:Query hint:optimizer "None" .
  ?item wdt:P31 wd:%(type)s .
  ?item wdt:P17 wd:%(country)s .
  ?item wdt:P856 ?website .
  OPTIONAL { ?item rdfs:label ?name FILTER(LANG(?name) = "en") }
  OPTIONAL { ?item wdt:P452/rdfs:label ?industry FILTER(LANG(?industry) = "en") }
}"""


def parse(data: dict, country: str, type_name: str) -> list[dict]:
    """Rows -> one company per item (an item with two websites or industries comes back twice)."""
    items: dict[str, dict] = {}
    for b in data.get("results", {}).get("bindings", []):
        qid = b.get("item", {}).get("value", "").rsplit("/", 1)[-1]
        name = b.get("name", {}).get("value")
        if not qid.startswith("Q") or not name:
            continue                                   # no English name: nothing to show
        item = items.setdefault(qid, {"name": name, "websites": [], "industries": []})
        site = b.get("website", {}).get("value")
        if site and site not in item["websites"]:
            item["websites"].append(site)
        ind = b.get("industry", {}).get("value")
        if ind and ind not in item["industries"]:
            item["industries"].append(ind)
    out = []
    for qid, it in items.items():
        sites = sorted(it["websites"], key=lambda u: (not u.startswith("https://"), len(u)))
        out.append(company("wikidata", qid, it["name"], country, sites[0] if sites else None,
                           ", ".join(sorted(it["industries"])[:3]) or None, type=type_name,
                           wikidata=f"https://www.wikidata.org/wiki/{qid}"))
    return out


class Wikidata(Source):
    name = "wikidata"
    unit_seconds = 20

    async def batches(self, cursors: dict[str, dict]):
        types = self.config.get("types") or []
        qids = {c["code"]: c.get("wikidata") for c in self.ctx.countries}
        order = [c for c in self.ctx.country_order() if qids.get(c) and self.due(cursors.get(c))]
        if not types or not order:
            return
        state = {c: (cursors[c] if cursors.get(c) and not cursors[c].get("done_at") else
                     {**new_pass(), "next_type": 0, "failed": []}) for c in order}
        rotation = Rotation(order, self.ctx.priority, (cursors.get("_rotation") or {}).get("other_pos", 0))
        for code in rotation:
            if not self.has_time():
                return
            cur = state[code]
            t = types[min(cur["next_type"], len(types) - 1)]
            started = time.monotonic()
            try:
                data = await request(self.ctx.session, "Wikidata", self.config["endpoint"],
                                     params={"query": QUERY % {"type": t["id"], "country": qids[code]}},
                                     headers={"Accept": "application/sparql-results+json"},
                                     timeout=float(self.config.get("timeout_seconds", 70)))
                found = parse(data, code, t["name"])
            except SourceError as e:              # too slow or a server error: try this type again next pass
                logger.warning("wikidata: %s %s: %s (skipped until the next pass)", code, t["name"], e)
                cur["failed"] = cur.get("failed", []) + [t["id"]]
                found = []
            took = time.monotonic() - started
            cur["next_type"] += 1
            if cur["next_type"] >= len(types):
                cur["done_at"] = now_iso()
                rotation.drop(code)
            yield Batch(found, {code: dict(cur), "_rotation": {"other_pos": rotation.other_pos}},
                        f"{code} {t['name']}")
            # 60 seconds of query time per minute: wait as long as the query took
            if not await self.pause(max(float(self.config.get("min_wait_seconds", 5)), took)):
                return
