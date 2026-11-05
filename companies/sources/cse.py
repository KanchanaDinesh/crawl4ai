"""Colombo Stock Exchange: every listed Sri Lankan company, from cse.lk's own JSON API (robots.txt allows it).

    POST https://www.cse.lk/api/tradeSummary                      -> every listed security: symbol, name
    POST https://www.cse.lk/api/companyProfile  symbol=JKH.N0000  -> website ("web"), sector, established,
                                                                     registered office, board

The list is read once per pass; then each company's profile, one request every delay_seconds,
per_batch companies per batch. Cursor "listed": {"symbols": [[symbol, name], ...], "index": n, ...}.
"""

import logging

from . import Batch, Refused, Source, SourceError, company, new_pass, now_iso, request

logger = logging.getLogger("companies")


def parse_list(data) -> list[list[str]]:
    """[[symbol, name], ...], one per company: a company with voting and non-voting shares (AGST.N0000 and
    AGST.X0000) is listed twice, so only its first symbol is kept (.N0000 sorts before .X0000)."""
    rows = data.get("reqTradeSummery", []) if isinstance(data, dict) else (data or [])
    out, names = [], set()
    for symbol, name in sorted((r["symbol"], r.get("name") or r["symbol"]) for r in rows if r.get("symbol")):
        if name.strip().upper() not in names:
            names.add(name.strip().upper())
            out.append([symbol, name])
    return out


def parse_profile(symbol: str, name: str, data: dict | None) -> dict:
    info = ((data or {}).get("reqComSumInfo") or [{}])[0] or {}
    summary = ((data or {}).get("infoCompanyBusinessSummary") or [{}])[0] or {}
    return company("cse", symbol, info.get("name") or name, "LK", info.get("web"), (info.get("sector") or "").strip() or None,
                   symbol=symbol, established=info.get("established"), board=info.get("boardType"),
                   address=(info.get("registeredOffice") or info.get("registerOffice") or "").strip() or None,
                   business=" ".join((summary.get("body") or "").split())[:400] or None)


class Cse(Source):
    name = "cse"
    unit_seconds = 15

    async def batches(self, cursors: dict[str, dict]):
        if self.ctx.only_countries and "LK" not in self.ctx.only_countries:
            return
        cur = cursors.get("listed")
        if not self.due(cur):
            return
        api = self.config.get("api", "https://www.cse.lk/api").rstrip("/")
        if not cur or cur.get("done_at") or not cur.get("symbols"):
            symbols = parse_list(await request(self.ctx.session, "cse.lk", f"{api}/tradeSummary", method="POST"))
            if not symbols:
                raise SourceError("cse.lk: the list of listed companies was empty")
            cur = {**new_pass(), "symbols": symbols, "index": 0}
        delay = float(self.config.get("delay_seconds", 1.0))
        per_batch = int(self.config.get("per_batch", 10))
        while cur["index"] < len(cur["symbols"]) and self.has_time():
            found, refused = [], None
            for symbol, name in cur["symbols"][cur["index"]: cur["index"] + per_batch]:
                try:
                    data = await request(self.ctx.session, "cse.lk", f"{api}/companyProfile", method="POST",
                                         data={"symbol": symbol}, timeout=30)
                except Refused as e:       # keep what this batch already has, then stop for this run
                    refused = e
                    break
                except SourceError as e:
                    logger.info("cse: %s profile not read (%s); saved with its name only", symbol, e)
                    data = None
                found.append(parse_profile(symbol, name, data))
                await self.pause(delay)
            cur["index"] += len(found)
            if cur["index"] >= len(cur["symbols"]):
                cur["done_at"] = now_iso()
            yield Batch(found, {"listed": dict(cur)}, f"LK listed {cur['index']}/{len(cur['symbols'])}")
            if refused:
                raise refused
