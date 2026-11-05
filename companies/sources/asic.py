"""ASIC company dataset (Australia, CC BY 3.0 AU): every registered Australian company. No websites.

data.gov.au's robots.txt says "Disallow: /" to every robot, so this tool doesn't download it.
Download "Company Dataset - Current CSV" yourself (~400 MB, updated monthly) from
https://data.gov.au/data/dataset/asic-companies and set sources.asic.file to its path (and
"enabled": true). It is read chunk_bytes at a time; the cursor is the byte offset in the file,
so a new file (another name, size or date) starts again from the top.

The file is tab-separated. A company appears once per name it has had; the row with
"Current Name Indicator" = Y is the current name. Only registered companies (Status REGD) are kept.
"""

import csv
import io
import logging
import os
from pathlib import Path

from . import Batch, Source, SourceError, company, new_pass, now_iso

logger = logging.getLogger("companies")


def row_to_company(row: dict, registered_only: bool = True) -> dict | None:
    row = {(k or "").strip().lstrip("﻿"): (v or "").strip() for k, v in row.items()}
    acn, name = row.get("ACN"), row.get("Company Name")
    if not acn or not name or row.get("Current Name Indicator", "").upper() != "Y":
        return None
    if registered_only and row.get("Status", "").upper() != "REGD":
        return None
    return company("asic", acn, name, "AU", None, None, abn=row.get("ABN"), type=row.get("Type"),
                   company_class=row.get("Class"), registered=row.get("Date of Registration"),
                   state=row.get("Previous State of Registration"))


class Asic(Source):
    name = "asic"
    unit_seconds = 5

    async def batches(self, cursors: dict[str, dict]):
        if self.ctx.only_countries and "AU" not in self.ctx.only_countries:
            return
        path = Path(os.path.expanduser(self.config.get("file") or ""))
        if not self.config.get("file") or not path.is_file():
            raise SourceError(f"asic: no file to read ({path or 'sources.asic.file is empty'}). Download the "
                              f"'Current CSV' from {self.config.get('dataset_page')} and set sources.asic.file")
        stat = path.stat()
        fingerprint = f"{path.name}:{stat.st_size}:{int(stat.st_mtime)}"
        cur = cursors.get("file")
        if cur and cur.get("file") == fingerprint and cur.get("done_at"):
            return                                       # this file is already in
        if not cur or cur.get("file") != fingerprint:
            cur = {**new_pass(), "file": fingerprint, "offset": 0, "header": None}
        chunk = int(self.config.get("chunk_bytes", 8_000_000))
        registered_only = bool(self.config.get("registered_only", True))
        with path.open("rb") as f:
            while cur["offset"] < stat.st_size and self.has_time():
                f.seek(cur["offset"])
                data = f.read(chunk)
                end = len(data) if cur["offset"] + len(data) >= stat.st_size else data.rfind(b"\n") + 1
                if end <= 0:
                    raise SourceError("asic: a line in the file is longer than chunk_bytes")
                lines = data[:end].decode("utf-8", "replace").splitlines()
                if cur["header"] is None:
                    cur["header"] = [h.strip().lstrip("﻿") for h in lines.pop(0).split("\t")]
                reader = csv.DictReader(io.StringIO("\n".join(lines)), fieldnames=cur["header"], delimiter="\t",
                                        quoting=csv.QUOTE_NONE)
                found = [c for c in (row_to_company(r, registered_only) for r in reader) if c]
                cur["offset"] += end
                if cur["offset"] >= stat.st_size:
                    cur["done_at"] = now_iso()
                pct = 100 * cur["offset"] / stat.st_size
                yield Batch(found, {"file": dict(cur)}, f"AU file {pct:.0f}%")
