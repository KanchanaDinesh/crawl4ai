"""SEC EDGAR company tickers (US listed companies: name, ticker, CIK). No websites.

The SEC's fair-access rules require a User-Agent with a real contact email. We never make one up
and never use anyone's email without being told to, so this source is off until you set
sources.sec.contact_email (and "enabled": true) in companies/config/settings.json.
At most 10 requests a second are allowed; this source makes one request per pass.
"""

import logging

from . import Batch, Source, SourceError, company, new_pass, now_iso, request

logger = logging.getLogger("companies")


def parse(data: dict) -> list[dict]:
    rows = data.values() if isinstance(data, dict) else data
    return [company("sec", f"{int(r['cik_str']):010d}", r.get("title"), "US", None, None, ticker=r.get("ticker"),
                    edgar=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={int(r['cik_str']):010d}")
            for r in rows if isinstance(r, dict) and r.get("cik_str") is not None]


class Sec(Source):
    name = "sec"
    unit_seconds = 10

    async def batches(self, cursors: dict[str, dict]):
        if self.ctx.only_countries and "US" not in self.ctx.only_countries:
            return
        email = (self.config.get("contact_email") or "").strip()
        if "@" not in email:
            raise SourceError("sec: off until you set sources.sec.contact_email (the SEC requires a contact email)")
        cur = cursors.get("tickers")
        if not self.due(cur):
            return
        ua = f"{self.ctx.settings.get('user_agent', 'crawl4ai-companies/1.0')} {email}"
        data = await request(self.ctx.session, "SEC", self.config["url"], headers={"User-Agent": ua})
        found = parse(data)
        yield Batch(found, {"tickers": {**new_pass(), "done_at": now_iso(), "companies": len(found)}}, "US tickers")
