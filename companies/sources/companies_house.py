"""UK Companies House "Free Company Data Product" (Open Government Licence): every UK company.

https://download.companieshouse.gov.uk/en_output.html lists the monthly snapshot in 7 zip files
(~70 MB each, one CSV inside). robots.txt allows everything. Off by default: it is ~5.5 million
companies with no websites, so turn it on only if you want the whole UK register.

One file at a time is downloaded to data/companies/downloads/, imported chunk_rows rows per batch,
and deleted when it's done. Cursor "basic": {"date": "2026-11-01", "part": 3, "parts": 7, "row": 120000, ...}.
"""

import csv
import io
import logging
import re
import zipfile
from pathlib import Path

from . import Batch, Source, SourceError, company, new_pass, now_iso, request

logger = logging.getLogger("companies")

FILE = re.compile(r"BasicCompanyData-(\d{4}-\d{2}-\d{2})-part(\d+)_(\d+)\.zip")


def latest_files(html: str) -> tuple[str, int] | None:
    """('2026-11-01', 7) for the newest snapshot listed on the download page."""
    found = sorted({(m.group(1), int(m.group(3))) for m in FILE.finditer(html or "")})
    return found[-1] if found else None


def file_name(date: str, part: int, parts: int) -> str:
    return f"BasicCompanyData-{date}-part{part}_{parts}.zip"


def row_to_company(row: dict, active_only: bool = True) -> dict | None:
    row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
    number, name = row.get("CompanyNumber"), row.get("CompanyName")
    if not number or not name or (active_only and row.get("CompanyStatus", "").lower() != "active"):
        return None
    sic = row.get("SICCode.SicText_1", "")
    industry = sic.split(" - ", 1)[1] if " - " in sic else None
    return company("companies_house", number, name, "GB", None, industry and industry.capitalize(),
                   status=row.get("CompanyStatus"), category=row.get("CompanyCategory"),
                   incorporated=row.get("IncorporationDate"), town=(row.get("RegAddress.PostTown") or "").title() or None,
                   postcode=row.get("RegAddress.PostCode"),
                   companies_house=f"https://find-and-update.company-information.service.gov.uk/company/{number}")


class CompaniesHouse(Source):
    name = "companies_house"
    unit_seconds = 20

    async def batches(self, cursors: dict[str, dict]):
        if self.ctx.only_countries and "GB" not in self.ctx.only_countries:
            return
        cur = cursors.get("basic")
        index_url = self.config["index_url"]
        latest = latest_files(await request(self.ctx.session, "Companies House", index_url, want="text"))
        if not latest:
            raise SourceError("Companies House: no BasicCompanyData files on the download page")
        date, parts = latest
        if cur and cur.get("date") == date and not self.due(cur):
            return                               # this month's snapshot is already in
        if not cur or cur.get("date") != date or cur.get("done_at"):
            cur = {**new_pass(), "date": date, "part": 1, "parts": parts, "row": 0}
        chunk = int(self.config.get("chunk_rows", 20000))
        active_only = bool(self.config.get("active_only", True))
        base = index_url.rsplit("/", 1)[0]
        while cur["part"] <= cur["parts"] and self.has_time():
            name = file_name(cur["date"], cur["part"], cur["parts"])
            path = Path(self.ctx.download_dir) / name
            if not path.exists():
                await self._download(f"{base}/{name}", path)
                if not self.has_time():
                    return
            with zipfile.ZipFile(path) as z, z.open(z.namelist()[0]) as raw:
                reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline=""))
                for _ in range(cur["row"]):
                    next(reader, None)
                finished = False
                while self.has_time():
                    found, n = [], 0
                    for row in reader:
                        n += 1
                        c = row_to_company(row, active_only)
                        if c:
                            found.append(c)
                        if n >= chunk:
                            break
                    finished = n < chunk
                    cur["row"] += n
                    if finished:
                        cur.update(part=cur["part"] + 1, row=0)
                        if cur["part"] > cur["parts"]:
                            cur["done_at"] = now_iso()
                    yield Batch(found, {"basic": dict(cur)}, f"GB file {min(cur['part'], cur['parts'])}/{cur['parts']}")
                    if finished:
                        break
            if finished:
                path.unlink(missing_ok=True)

    async def _download(self, url: str, path: Path) -> None:
        logger.info("companies_house: downloading %s (about 70 MB)", path.name)
        tmp = path.with_suffix(".part")
        body, _, _ = await request(self.ctx.session, "Companies House", url, timeout=900, want="bytes")
        tmp.write_bytes(body)
        tmp.rename(path)
