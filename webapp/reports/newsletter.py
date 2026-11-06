"""A newsletter's content: for each section, the latest rows of its dataset, as the person it's for may see them. Everything goes through the catalog's
fields and the grid's query code, so corrections, hidden rows, own fields and rights all behave as they do on screen."""

import re
from datetime import datetime
from html import unescape

from django.db import DatabaseError
from django.utils.html import strip_tags

from webapp.catalog.models import Field
from webapp.catalog.rights import can_view
from webapp.catalog.sqlnames import relation
from webapp.charts import data as chart_sql
from webapp.explore import query

from .models import ReportSection

TEXT_CHARS = 280
CELL_CHARS = 90
BARS = 8
SLOW = "This section took too long to work out, so it was left out this time."


def safe_link(value) -> str:
    """Addresses come from collected pages. Only ordinary web links become links."""
    text = str(value or "").strip()
    return text if re.match(r"https?://[^\s]+$", text, re.I) else ""


def short_link(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url, flags=re.I).rstrip("/")[:CELL_CHARS]


def clean_text(value, limit: int = TEXT_CHARS) -> str:
    """Feeds can carry bits of HTML. In the PDF it would only be shown as text, so it's taken out."""
    text = " ".join(strip_tags(unescape(str(value or ""))).split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


def _when(value: str) -> str:
    try:
        moment = datetime.strptime(str(value), "%Y-%m-%d %H:%M")
    except ValueError:
        return str(value or "")
    return f"{moment.day} {moment:%b %Y, %H:%M}"


def _show(f: Field, value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (list, tuple)):
        return clean_text(", ".join(str(v) for v in value), CELL_CHARS)
    if f.base == Field.NUMBER:
        return chart_sql.show_number(value)
    if f.base == Field.BOOL:
        return "yes" if value in (True, "true") else "no"
    if f.base == Field.DATETIME:
        return _when(value)
    return clean_text(value, CELL_CHARS)


def _fields_used(section: ReportSection, fields: dict) -> list[Field]:
    paths = [section.title_field, section.text_field, section.link_field, section.date_field, *(section.detail_fields or [])]
    found = {}
    for path in paths:
        if path in fields and fields[path].base != Field.JSON:
            found[path] = fields[path]
    return list(found.values())


def _item(section: ReportSection, row: dict, fields: dict) -> dict:
    """One row as an article."""
    title_field = fields.get(section.title_field)
    details = [fields[p] for p in section.detail_fields or [] if p in fields and p != section.title_field]
    meta = [text for text in (_show(f, row.get(f.path)) for f in details) if text]
    date_field = fields.get(section.date_field)
    # the date a row was collected says little once a date of its own is on show: a whole run has the same one
    has_a_date = any(f.base in (Field.DATETIME, Field.DATE) for f in details)
    if date_field and not has_a_date and row.get(date_field.path):
        meta.append(_show(date_field, row[date_field.path]))
    return {"title": clean_text(row.get(title_field.path), 200) if title_field else "", "href": safe_link(row.get(section.link_field)),
            "text": clean_text(row.get(section.text_field)) if section.text_field in fields else "", "meta": meta}


def _table(section: ReportSection, rows: list[dict], fields: dict) -> tuple[list, list]:
    """Rows as table lines: the title first (linked), then the details. A link field chosen as a detail shows as a short address."""
    columns = [fields[p] for p in [section.title_field, *(section.detail_fields or [])] if p in fields and fields[p].base != Field.JSON]
    columns = list({f.path: f for f in columns}.values())
    heads = [{"label": f.label, "num": f.base == Field.NUMBER} for f in columns]
    lines = []
    for row in rows:
        cells = []
        for f in columns:
            value = row.get(f.path)
            href = safe_link(value) if f.base == Field.LINK else (safe_link(row.get(section.link_field)) if f.path == section.title_field else "")
            text = short_link(href) if f.base == Field.LINK and href else _show(f, value)
            cells.append({"text": text, "href": href, "is_link": f.base == Field.LINK, "num": f.base == Field.NUMBER})
        lines.append(cells)
    return heads, lines


def _bars(section: ReportSection, fields: dict, where: str, params: list) -> tuple[str, list]:
    f = fields.get(section.breakdown_field)
    if f is None or f.base in (Field.JSON, Field.DATETIME, Field.DATE):
        return "", []
    value, join = chart_sql._group_sql(f, "", "g")
    ds = section.dataset
    rows = chart_sql._fetch(f"select {value}, count(*) from {relation(ds.schema, ds.name)} d{join}{where} group by 1 order by 2 desc, 1 limit {BARS}", params)
    top = max((r[1] for r in rows), default=0) or 1
    return f.label, [{"label": clean_text(chart_sql._name(f, r[0]), 50), "count": f"{r[1]:,}", "pct": max(1, round(100 * r[1] / top))} for r in rows]


def _section(section: ReportSection, user, anchor: str) -> dict:
    ds = section.dataset
    out = {"heading": section.heading, "anchor": anchor, "dataset": ds.label, "layout": section.layout, "limit": section.limit,
           "period": section.period_label() if section.days and section.date_field else ""}
    if ds.missing:
        return {**out, "problem": f"The dataset {ds.label} isn't there any more."}
    if not can_view(user, ds):
        return {**out, "problem": "Your roles don't include this section's data."}
    fields = query.usable_fields(ds, user)
    filters = list(section.filters or [])
    if section.days and section.date_field not in fields:
        return {**out, "problem": "The date this section goes by isn't available any more. Choose another in its settings."}
    if section.days:
        filters.append({"field": section.date_field, "value": f"{section.days}d"})
    try:
        where, params = query.where_clause(fields, filters)
        where = query.not_hidden(ds, where)
        out["total"] = query.count_rows(ds, where, params)
        out["breakdown"], out["bars"] = _bars(section, fields, where, params)
        if section.limit and out["total"]:
            sort = section.sort_field if section.sort_field in fields else section.date_field
            # then newest first, for rows the first choice can't place (a headline with no date of its own)
            sorts = [{"field": sort, "dir": "desc" if section.sort_desc else "asc"}] if sort else []
            if section.date_field in fields and section.date_field != sort:
                sorts.append({"field": section.date_field, "dir": "desc"})
            order = query.order_clause(ds, fields, sorts)
            rows = query.fetch_rows(ds, _fields_used(section, fields), where, params, order, 0, section.limit)
            if section.layout == ReportSection.TABLE:
                out["heads"], out["lines"] = _table(section, rows, fields)
            else:
                out["articles"] = [_item(section, row, fields) for row in rows]
            out["shown"] = len(rows)
            out["more"] = max(0, out["total"] - len(rows))
    except (query.QueryProblem, chart_sql.ChartProblem) as e:
        return {**out, "problem": str(e)}
    except DatabaseError:
        return {**out, "problem": SLOW}
    return out


def sections_for(report, user) -> list[dict]:
    return [_section(s, user, f"s{i}") for i, s in enumerate(report.sections.select_related("dataset"), 1)]
