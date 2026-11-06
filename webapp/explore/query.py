"""One generic query for every dataset: rows, counts and exports, built from the catalog.

Requests only ever name fields by their catalog path. A path that isn't an available field of this dataset is
refused, and every name in the SQL comes from the catalog (quoted), every value goes as a parameter. So a request
can't reach another table or column, whatever it sends.

Filters:  text "contains";  number ">10", "<=5", "=3", "10..20";  date "7d" (the last 7 days), "2026-11-01",
">2026-11-01", "2026-11-01..2026-11-05";  tick lists: a list of values, EMPTY meaning "no value".
"""

import json
import math
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db import connection, transaction
from django.utils import timezone

from warehouse import edits
from webapp.catalog.models import Dataset, Field
from webapp.catalog.sqlnames import lit, q, relation

EMPTY = "(empty)"
QUERY_TIMEOUT = "30s"
COUNT_CACHE_SECONDS = 30        # scrolling asks page after page; the count of the same filters isn't asked again
MAX_SEARCH_FIELDS = 10
JSON_DISPLAY_CHARS = 300


class QueryProblem(ValueError):
    """Something wrong with what was asked; the message is shown to the user."""


def usable_fields(ds: Dataset, user=None) -> dict[str, Field]:
    """With a user: leaving out links, lookups and rollups into a dataset they may not see, because those show its rows."""
    found = ds.fields.filter(available=True, missing=False).select_related(
        "link_dataset", "link_field", "link_field__link_dataset", "target_field", "target_field__dataset")
    fields = {f.path: f for f in found if not (f.link_dataset and f.link_dataset.missing)}
    if user is not None:
        from webapp.catalog.rights import can_view
        fields = {p: f for p, f in fields.items() if not f.link_dataset or can_view(user, f.link_dataset)}
    return fields


def shown_first(fields: dict[str, Field]) -> list[Field]:
    """What's on screen before anyone chooses: the catalog's choice, or if that leaves nothing (every shown field was
    switched off), the first few that can be displayed."""
    shown = [f for f in fields.values() if f.shown]
    return shown or [f for f in fields.values() if f.base != Field.JSON][:8] or list(fields.values())[:8]


def key_text(ds: Dataset, alias: str = "d") -> str:
    return f"{alias}.{q(ds.record_key)}::text"


def _key_equals(ds: Dataset, other: str) -> str:
    """This dataset's row whose key is the text `other`. An own table's key is a number: comparing it as a number uses its index."""
    if ds.is_own:
        return f'd."id" = ({other})::bigint'
    return f"d.{q(ds.record_key)}::text = {other}"


def _data_column(f: Field) -> str:
    """Where a field's JSON lives: an own table's row, or the separate row of own fields beside a collected row."""
    ds = f.dataset
    if f.kind == Field.OWN and not ds.is_own:
        return f"(select e.data from userdata.extras e where e.dataset_id = {int(ds.pk)} and e.record = {key_text(ds)})"
    return q(f.column)


def _link_rows(link: Field) -> tuple[str, str, str]:
    """A link field's rows for the outer row d: (the WHERE, the column with the other row's key, the one with its name).
    A link's other side reads the same rows from the other end. Rows hidden on the other side don't count."""
    outer = key_text(link.dataset)
    if link.is_reverse_link:
        where, other, label = f"l.field_id = {int(link.link_field_id)} and l.to_record = {outer}", "l.from_record", "l.from_label"
    else:
        where, other, label = f"l.field_id = {int(link.pk)} and l.from_record = {outer}", "l.to_record", "l.to_label"
    hide = link.link_dataset.hide_target
    if hide:
        where += f" and not exists (select 1 from {edits.HIDDEN} h where h.dataset = {lit(hide.table)} and h.record = {other})"
    return where, other, label


def _through_link(f: Field) -> tuple[str, str]:
    """For a lookup or rollup: the value of the linked rows' field, one per link: (FROM ... lateral, WHERE)."""
    link, target = f.link_field, f.target_field
    where, other, _ = _link_rows(link)
    other_ds = link.link_dataset
    source = (f"userdata.links l cross join lateral (select {value_expr(target)} as v from {relation(other_ds.schema, other_ds.name)} d "
              f"where {_key_equals(other_ds, other)} limit 1) x")
    return source, where


def _computed(f: Field) -> str:
    if f.kind == Field.RECORDS:
        where, _, label = _link_rows(f)
        return f"(select jsonb_agg({label} order by l.id) from userdata.links l where {where})"
    if f.kind == Field.ROLLUP and f.rollup == Field.COUNT:
        where, _, _ = _link_rows(f.link_field)
        return f"(select count(*) from userdata.links l where {where})"
    source, where = _through_link(f)
    if f.kind == Field.LOOKUP:
        return f"(select jsonb_agg(x.v order by l.id) from {source} where {where} and x.v is not null)"
    return f"(select {f.rollup}(x.v) from {source} where {where})"


def value_expr(f: Field) -> str:
    """The SQL for a field's value, with the right type (a bad value inside JSON gives NULL, not an error)."""
    if f.kind in (Field.RECORDS, Field.LOOKUP, Field.ROLLUP):
        return _computed(f)
    col = _data_column(f)
    base = f.base
    if not f.json_key:
        if base == Field.LIST and f.is_jsonb:
            return f"(case when jsonb_typeof({col}) = 'array' then {col} end)"
        return col
    key = lit(f.json_key)
    text, raw = f"{col} ->> {key}", f"{col} -> {key}"
    if base == Field.NUMBER:
        return f"(case when jsonb_typeof({raw}) = 'number' then ({text})::float8 end)"
    if base == Field.BOOL:
        return f"(case when jsonb_typeof({raw}) = 'boolean' then ({text})::boolean end)"
    if base == Field.DATETIME:
        return f"(case when pg_input_is_valid({text}, 'timestamptz') then ({text})::timestamptz end)"
    if base == Field.DATE:
        return f"(case when pg_input_is_valid({text}, 'date') then ({text})::date end)"
    if base == Field.LIST:
        return f"(case when jsonb_typeof({raw}) = 'array' then {raw} end)"
    if base == Field.JSON:
        return f"({raw})"
    return f"({text})"


def label_field(ds: Dataset) -> Field | None:
    """The field that names a row, wherever a row is mentioned elsewhere (link pickers, lists of hidden rows)."""
    fields = ds.fields.filter(available=True, missing=False)
    target = ds.edit_target
    found = fields.filter(column=target.label, json_key=None).first() if target else None
    if found is None and ds.is_own:
        found = fields.filter(kind=Field.OWN, type__in=[Field.TEXT, Field.EMAIL, Field.LINK, Field.CHOICE]).order_by("order", "id").first()
    if found is None:
        found = (fields.filter(type=Field.TEXT, shown=True, json_key=None).exclude(column=ds.record_key or "").order_by("order", "id").first()
                 or fields.filter(type=Field.TEXT, json_key=None).order_by("order", "id").first())
    return found


def select_expr(f: Field) -> str:
    expr = value_expr(f)
    return f"left(({expr})::text, {JSON_DISPLAY_CHARS})" if f.base == Field.JSON else expr


def _like(text: str) -> str:
    return "%" + text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


NUMBER_RE = r"-?\d[\d,]*(?:\.\d+)?"


def _number(text: str) -> float:
    return float(text.replace(",", ""))


def _number_filter(expr: str, text: str, label: str) -> tuple[str, list]:
    t = text.strip()
    m = re.fullmatch(rf"({NUMBER_RE})\s*\.\.\s*({NUMBER_RE})", t)
    if m:
        return f"{expr} between %s and %s", [_number(m[1]), _number(m[2])]
    m = re.fullmatch(rf"(>=|<=|>|<|=)?\s*({NUMBER_RE})", t)
    if m:
        return f"{expr} {m[1] or '='} %s", [_number(m[2])]
    raise QueryProblem(f"{label}: type a number, or >10, <=5, 10..20")


def _day(text: str, label: str) -> date:
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise QueryProblem(f"{label}: type a date like 2026-11-01, >2026-11-01, 2026-11-01..2026-11-05, or 7d") from None


def _start(d: date) -> datetime:
    return timezone.make_aware(datetime.combine(d, time.min))


def _date_filter(expr: str, text: str, label: str) -> tuple[str, list]:
    t = text.strip()
    m = re.fullmatch(r"(\d+)\s*d", t, re.I)
    if m:
        return f"{expr} >= %s", [timezone.now() - timedelta(days=int(m[1]))]
    if ".." in t:
        a, b = t.split("..", 1)
        return f"{expr} >= %s and {expr} < %s", [_start(_day(a, label)), _start(_day(b, label) + timedelta(days=1))]
    m = re.fullmatch(r"(>=|<=|>|<)\s*(.+)", t)
    if m:
        d = _day(m[2], label)
        if m[1] == ">=":
            return f"{expr} >= %s", [_start(d)]
        if m[1] == ">":
            return f"{expr} >= %s", [_start(d + timedelta(days=1))]
        if m[1] == "<":
            return f"{expr} < %s", [_start(d)]
        return f"{expr} < %s", [_start(d + timedelta(days=1))]
    d = _day(t, label)
    return f"{expr} >= %s and {expr} < %s", [_start(d), _start(d + timedelta(days=1))]


def _tick_filter(f: Field, expr: str, picked: list) -> tuple[str, list]:
    picked = [str(v) for v in picked]
    has_empty = EMPTY in picked
    picked = [v for v in picked if v != EMPTY]
    parts, params = [], []
    if f.base == Field.BOOL:
        if "yes" in picked:
            parts.append(f"{expr} is true")
        if "no" in picked:
            parts.append(f"{expr} is false")
    elif f.base == Field.LIST:
        if picked:
            if f.is_jsonb:
                parts.append(f"{expr} ?| %s")
            else:
                parts.append(f"{expr} && %s::text[]")
            params.append(picked)
    elif picked:
        parts.append(f"{expr} = any(%s)")
        params.append(picked)
    if has_empty:
        if f.base == Field.LIST:
            parts.append(f"({expr} is null or {expr}::text in ('[]', '{{}}'))")
        elif f.base == Field.BOOL:
            parts.append(f"{expr} is null")
        else:
            parts.append(f"({expr} is null or {expr}::text = '')")
    return ("(" + " or ".join(parts) + ")") if parts else "", params


def _one_filter(f: Field, value) -> tuple[str, list]:
    expr = value_expr(f)
    if isinstance(value, list):
        return _tick_filter(f, expr, value)
    text = str(value if value is not None else "").strip()
    if not text:
        return "", []
    if f.base == Field.NUMBER:
        return _number_filter(expr, text, f.label)
    if f.base in (Field.DATETIME, Field.DATE):
        return _date_filter(expr, text, f.label)
    if f.base == Field.BOOL:
        return _tick_filter(f, expr, [text])
    return f"lower(({expr})::text) like %s", [_like(text)]


def where_clause(fields: dict[str, Field], filters, search: str = "", shown: list[Field] | None = None) -> tuple[str, list]:
    parts, params = [], []
    for item in filters or []:
        if not isinstance(item, dict):
            raise QueryProblem("Filters must be a list of {field, value}")
        f = fields.get(str(item.get("field", "")))
        if f is None:
            raise QueryProblem(f"There's no field {str(item.get('field', ''))[:60]!r} in this dataset")
        sql, p = _one_filter(f, item.get("value"))
        if sql:
            parts.append(sql)
            params += p
    search = (search or "").strip()
    if search:
        text = [f for f in (shown or fields.values()) if f.base in (Field.TEXT, Field.LINK)][:MAX_SEARCH_FIELDS]
        if text:
            like = _like(search)
            parts.append("(" + " or ".join(f"lower(({value_expr(f)})::text) like %s" for f in text) + ")")
            params += [like] * len(text)
    return (" where " + " and ".join(parts)) if parts else "", params


def order_clause(ds: Dataset, fields: dict[str, Field], sorts) -> str:
    """The sorting asked for, then the key columns, so rows don't jump between pages."""
    terms = []
    for item in sorts or []:
        if isinstance(item, dict) and item.get("field") in fields:
            f = fields[item["field"]]
            if f.base != Field.JSON:
                terms.append(f"{value_expr(f)} {'desc' if item.get('dir') == 'desc' else 'asc'} nulls last")
    if not terms and ds.default_sort_field in fields:
        terms.append(f"{value_expr(fields[ds.default_sort_field])} {'desc' if ds.default_sort_dir == 'desc' else 'asc'} nulls last")
    terms += [q(k) for k in ds.key_columns if any(f.column == k for f in fields.values())]
    return (" order by " + ", ".join(terms)) if terms else ""


def plain(value):
    """A database value as something JSON and exports can carry; times in the local time zone, to the minute."""
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def shown_value(f: Field, value):
    """A value for the grid and exports. jsonb comes back as text on Django's connection, so a list has to be read back into
    one here: the grid draws a list as chips, and can't draw text that only looks like a list."""
    if f.base == Field.LIST and isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value
    return plain(value)


def _timeout(cur, seconds: str = QUERY_TIMEOUT) -> None:
    cur.execute(f"set local statement_timeout = '{seconds}'")


def not_hidden(ds: Dataset, where: str) -> str:
    """Adds "and not hidden" for datasets of records people can hide. Views may already leave them out; this also covers
    the plain tables. Reads audit's table only if it exists yet (a database that hasn't run `warehouse migrate`)."""
    target = ds.hide_target
    if target is None or ds.is_own:          # an own table's view already leaves them out
        return where
    with connection.cursor() as cur:
        if not edits.available(cur):
            return where
    clause = (f"not exists (select 1 from {edits.HIDDEN} h where h.dataset = {lit(target.table)} "
              f"and h.record = d.{q(target.key)}::text)")
    return f"{where} and {clause}" if where else f" where {clause}"


def count_rows(ds: Dataset, where: str, params: list) -> int:
    generation = cache.get(f"dsgen:{ds.pk}", 0)        # goes up when someone edits or hides rows
    key = f"dscount:{ds.pk}:{generation}:{hash((where, repr(params)))}"
    found = cache.get(key)
    if found is None:
        with transaction.atomic(), connection.cursor() as cur:
            _timeout(cur)
            cur.execute(f"select count(*) from {relation(ds.schema, ds.name)} d{where}", params)
            found = cur.fetchone()[0]
        cache.set(key, found, COUNT_CACHE_SECONDS)
    return found


def fetch_rows(ds: Dataset, shown: list[Field], where: str, params: list, order: str, start: int, limit: int,
               key_column: str | None = None) -> list[dict]:
    """key_column: also return each row's key as "_id", for people who may edit or hide rows (it may not be on screen)."""
    select = ", ".join(f"{select_expr(f)} as c{i}" for i, f in enumerate(shown)) or "null"
    if key_column:
        select += f", d.{q(key_column)}::text as _id"
    sql = f"select {select} from {relation(ds.schema, ds.name)} d{where}{order} limit %s offset %s"
    with transaction.atomic(), connection.cursor() as cur:
        _timeout(cur)
        cur.execute(sql, [*params, limit, start])
        found = []
        for row in cur.fetchall():
            item = {f.path: shown_value(f, v) for f, v in zip(shown, row)}
            if key_column:
                item["_id"] = row[len(shown)]
            found.append(item)
        return found
