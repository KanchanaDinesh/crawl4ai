"""The most valuable companies of each country: the members of its main stock index, which are chosen by size.

Exact value rankings were checked and not used: Wikidata's market values are good only for the US (India has 2 companies, Canada 11,
many years old or wrong), and companiesmarketcap.com's terms need permission for this kind of reuse. So:

  Wikipedia (CC BY-SA, MediaWiki API)    the index's table: company, ticker, sector
  Wikidata (CC0)                         official website, and a market value where there is one, for information only

Both ask programs for a descriptive user agent with a contact, no parallel requests, and to back off when told: one request at a time
with a pause, and a 429 stops the step. Companies Wikipedia can't give, or that aren't listed at all (OpenAI, Mailchimp), are in
settings.json "extra_companies", marked "added by hand".
"""

import json
import logging
import re
from urllib.parse import unquote, urlencode, urlsplit

from bs4 import BeautifulSoup

logger = logging.getLogger("articles")
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
BATCH = 40
NAME_HEADS = ("company", "name", "constituent")
TICKER_HEADS = ("symbol", "ticker", "epic")
SECTOR_HEADS = ("sector", "gics", "industry")


def _head(th) -> str:
    return re.sub(r"\[.*?\]", "", th.get_text(" ", strip=True)).strip().lower()


def _find(heads: list[str], names: tuple) -> int | None:
    """The first choice of name wins wherever it is: "Sector" over a "GICS" column of code numbers."""
    return next((i for n in names for i, h in enumerate(heads) if n in h), None)


def parse_index_table(html: str) -> list[dict]:
    """The members: the first big table with both a company column and a ticker column. Other tables on those pages list yearly
    levels, futures contracts or past changes."""
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table", class_="wikitable"):
        rows = table.find_all("tr")
        if len(rows) < 30:
            continue
        heads = [_head(c) for c in rows[0].find_all(["th", "td"])]
        name, ticker = _find(heads, NAME_HEADS), _find(heads, TICKER_HEADS)
        if name is None or ticker is None:
            continue
        sector = _find(heads, SECTOR_HEADS)
        out = []
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) <= max(name, ticker):
                continue
            link = cells[name].find("a", href=True)
            label = cells[name].get_text(" ", strip=True)
            href = link["href"] if link else ""
            title = unquote(href.split("/wiki/", 1)[1]).replace("_", " ").split("#")[0] if "/wiki/" in href and "redlink" not in href else None
            if label:
                out.append({"name": re.sub(r"\s*\(Class [A-Z]\)|\[.*?\]", "", label).strip(), "title": title, "ticker": cells[ticker].get_text(" ", strip=True),
                            "sector": cells[sector].get_text(" ", strip=True) if sector is not None and len(cells) > sector else None})
        if out:
            return out
    return []


def parse_page_ids(data: dict) -> dict[str, str]:
    """{Wikipedia title as asked: Wikidata id}, following redirects and normalised titles."""
    query = data.get("query", {})
    rename = {r["from"]: r["to"] for r in query.get("normalized", [])}
    redirect = {r["from"]: r["to"] for r in query.get("redirects", [])}
    ids = {p["title"]: p.get("pageprops", {}).get("wikibase_item") for p in query.get("pages", []) if not p.get("missing")}
    out = {}
    for asked in set(rename) | set(ids) | set(redirect):
        title = rename.get(asked, asked)
        title = redirect.get(title, title)
        if ids.get(title):
            out[asked] = ids[title]
    return out


def _time(claim: dict) -> str:
    qualifiers = claim.get("qualifiers", {}).get("P585") or []
    try:
        return qualifiers[0]["datavalue"]["value"]["time"].lstrip("+")[:10].replace("-00", "-01")    # a date known only to the year is written 2023-00-00
    except (KeyError, IndexError, TypeError):
        return ""


def pick_website(claims: list[dict], name: str = "") -> str | None:
    """The one marked preferred, then one whose address has the company's name in it, then https, then the shortest. Apple has several,
    and the shortest of those is mac.com."""
    first_word = re.sub(r"[^a-z0-9]", "", (name.split() or [""])[0].lower())
    sites = []
    for c in claims:
        try:
            url = c["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
        named = bool(first_word) and first_word in urlsplit(url).netloc.lower()
        sites.append((c.get("rank") != "preferred", not named, not url.startswith("https://"), len(url), url))
    return min(sites)[4] if sites else None


def parse_entities(data: dict, names: dict[str, str] | None = None) -> dict[str, dict]:
    """{Wikidata id: {"website", "market_cap", "cap_currency" (its Wikidata id), "cap_date"}}."""
    out = {}
    for qid, entity in (data.get("entities") or {}).items():
        claims = entity.get("claims") or {}
        caps = []
        for c in claims.get("P2226", []):
            try:
                value = c["mainsnak"]["datavalue"]["value"]
                if c.get("rank") != "deprecated":
                    caps.append((_time(c), float(value["amount"]), value["unit"].rsplit("/", 1)[-1]))
            except (KeyError, TypeError, ValueError):
                continue
        newest = max(caps) if caps else (None, None, None)
        out[qid] = {"website": pick_website(claims.get("P856", []), (names or {}).get(qid, "")), "cap_date": newest[0] or None, "market_cap": newest[1], "cap_currency": newest[2]}
    return out


CURRENCIES = {"Q4917": "USD", "Q25224": "GBP", "Q80524": "INR", "Q259502": "AUD", "Q1104069": "CAD", "Q4916": "EUR"}


def slug(name: str) -> str:
    return "x:" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def merge(found: list[dict]) -> list[dict]:
    """One row per company: GOOG and GOOGL are one Alphabet, and Rio Tinto is listed in two countries."""
    merged: dict[str, dict] = {}
    for c in found:
        row = merged.get(c["id"])
        if row is None:
            merged[c["id"]] = {**c, "countries": list(c["countries"]), "indexes": list(c["indexes"])}
            continue
        row["countries"] = sorted(set(row["countries"]) | set(c["countries"]))
        row["indexes"] = sorted(set(row["indexes"]) | set(c["indexes"]))
        for key in ("website", "ticker", "sector", "market_cap", "cap_currency", "cap_date"):
            row[key] = row.get(key) or c.get(key)
    return list(merged.values())


async def _api(web, url: str, **params) -> dict:
    r = await web.get(f"{url}?{urlencode({**params, 'format': 'json', 'formatversion': 2})}", host_gap=2.0)
    if not r.ok:
        raise ValueError(f"{urlsplit(url).netloc}: {r.error}")
    return json.loads(r.text)


async def fetch_index(web, index: dict) -> list[dict]:
    """One index's members, with websites. Raises if the page has fewer than expected: the page's layout has probably changed, and
    the old list is better than half a list."""
    data = await _api(web, WIKI_API, action="parse", page=index["page"], prop="text", redirects=1)
    members = parse_index_table(data.get("parse", {}).get("text", ""))
    if len(members) < index.get("at_least", 1):
        raise ValueError(f"{index['name']}: found only {len(members)} companies on its Wikipedia page, expected {index.get('at_least')} or more")
    titles = sorted({m["title"] for m in members if m["title"]})
    ids: dict[str, str] = {}
    for i in range(0, len(titles), BATCH):
        ids.update(parse_page_ids(await _api(web, WIKI_API, action="query", titles="|".join(titles[i:i + BATCH]), prop="pageprops", ppprop="wikibase_item", redirects=1)))
    qids = sorted(set(ids.values()))
    names = {ids[m["title"]]: m["name"] for m in members if m["title"] in ids}
    facts: dict[str, dict] = {}
    for i in range(0, len(qids), BATCH):
        facts.update(parse_entities(await _api(web, WIKIDATA_API, action="wbgetentities", ids="|".join(qids[i:i + BATCH]), props="claims"), names))
    out = []
    for m in members:
        qid = ids.get(m["title"]) if m["title"] else None
        fact = facts.get(qid, {})
        out.append({"id": qid or slug(m["name"]), "name": m["name"], "countries": [index["country"]], "indexes": [index["name"]], "ticker": m["ticker"], "sector": m["sector"],
                    "website": fact.get("website"), "market_cap": fact.get("market_cap"), "cap_currency": CURRENCIES.get(fact.get("cap_currency")), "cap_date": fact.get("cap_date"), "source": "wikipedia"})
    return out


def extras(settings: dict) -> list[dict]:
    return [{"id": slug(e["name"]), "name": e["name"], "countries": [e["country"]], "indexes": ["added by hand"], "ticker": None, "sector": e.get("sector"), "website": e.get("website"),
             "market_cap": None, "cap_currency": None, "cap_date": None, "source": "settings"} for e in settings.get("extra_companies", [])]
