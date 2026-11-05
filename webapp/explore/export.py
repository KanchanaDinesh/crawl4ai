"""Exports of what's on screen (fields, filters, sorting) as CSV or Excel, built on the server: openpyxl is why we don't
need a paid grid. Written to a temporary file first, so a problem half way gives an error, not a broken download."""

import csv
import io
import re
import tempfile

from django.db import connection, transaction
from django.http import FileResponse

import project_paths

from webapp.catalog.models import Dataset, Field
from webapp.catalog.sqlnames import relation

from .query import plain, select_expr

LIMIT = 200_000
BATCH = 5_000
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def safe_text(value):
    """Scraped text that starts with = + - @ would run as a formula when opened in Excel: make it plain text."""
    if not isinstance(value, str):
        return value
    value = CONTROL_CHARS.sub("", value)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def _cell(value):
    value = plain(value)
    if isinstance(value, (list, dict)):
        value = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
    return safe_text(value)


def _batches(ds: Dataset, shown: list[Field], where: str, params: list, order: str):
    select = ", ".join(select_expr(f) for f in shown)
    with transaction.atomic(), connection.cursor() as settings_cur, connection.chunked_cursor() as cur:
        settings_cur.execute("set local statement_timeout = '180s'")
        cur.execute(f"select {select} from {relation(ds.schema, ds.name)} d{where}{order} limit {LIMIT}", params)
        while rows := cur.fetchmany(BATCH):
            yield rows


def _spool():
    folder = project_paths.LOCAL_DIR / "webapp-exports"     # the project drive: the Mac's own drive can be full
    folder.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryFile(dir=folder)


def write_csv(ds, shown, where, params, order) -> tuple[object, int]:
    out, n = _spool(), 0
    text = io.TextIOWrapper(out, encoding="utf-8-sig", newline="", write_through=True)
    writer = csv.writer(text)
    writer.writerow([f.label for f in shown])
    for rows in _batches(ds, shown, where, params, order):
        writer.writerows([_cell(v) for v in row] for row in rows)
        n += len(rows)
    text.flush()
    text.detach()
    out.seek(0)
    return out, n


def write_xlsx(ds, shown, where, params, order) -> tuple[object, int]:
    from openpyxl import Workbook
    book = Workbook(write_only=True)
    sheet = book.create_sheet(ds.label[:31] or "Data")
    sheet.append([f.label for f in shown])
    n = 0
    for rows in _batches(ds, shown, where, params, order):
        for row in rows:
            sheet.append([_cell(v) for v in row])
        n += len(rows)
    out = _spool()
    book.save(out)
    out.seek(0)
    return out, n


FORMATS = {
    "csv": (write_csv, "text/csv; charset=utf-8"),
    "xlsx": (write_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
}


def respond(fmt: str, filename: str, ds, shown, where, params, order) -> tuple[FileResponse, int]:
    writer, content_type = FORMATS[fmt]
    out, n = writer(ds, shown, where, params, order)
    return FileResponse(out, as_attachment=True, filename=filename, content_type=content_type), n
