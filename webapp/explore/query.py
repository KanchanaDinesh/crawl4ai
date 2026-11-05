"""One generic query for every dataset: rows, counts and exports, built from the catalog.

Requests only ever name fields by their catalog path. A path that isn't an available field of this dataset is
refused, and every name in the SQL comes from the catalog (quoted), every value goes as a parameter. So a request
can't reach another table or column, whatever it sends.

Filters:  text "contains";  number ">10", "<=5", "=3", "10..20";  date "7d" (the last 7 days), "2026-11-01",
">2026-11-01", "2026-11-01..2026-11-05";  tick lists: a list of values, EMPTY meaning "no value".
"""

import math
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db import connection, transaction
from django.utils import timezone

from webapp.catalog.models import Dataset, Field
from webapp.catalog.sqlnames import lit, q, relation

EMPTY = "(empty)"
QUERY_TIMEOUT = "30s"
COUNT_CACHE_SECONDS = 30        # scrolling asks page after page; the count of the same filters isn't asked again
MAX_SEARCH_FIELDS = 10
JSON_DISPLAY_CHARS = 300


class QueryProblem(ValueError):
    """Something wrong with what was asked; the message is shown to the user."""


def usable_fields(ds: Dataset) -> dict[str, Field]:
    return {f.path: f for f in ds.fields.filter(available=True, missing=False)}


def shown_first(fields: dict[str, Field]) -> list[Field]:
    """What's on screen before anyone chooses: the catalog's choice, or if that leaves nothing (every shown field was
    switched off), the first few that can be displayed."""
    shown = [f for f in fields.values() if f.shown]
    return shown or [f for f in fields.values() if f.type != Field.JSON][:8] or list(fields.values())[:8]


def value_expr(f: Field) -> str:
    """The SQL for a field's value, with the right type (a bad value inside JSON gives NULL, not an error)."""
    col = q(f.column)
    if not f.json_key:
        if f.type == Field.LIST and f.is_jsonb:
            return f"(case when jsonb_typeof({col}) = 'array' then {col} end)"
        return col
    key = lit(f.json_key)
    text, raw = f"{col} ->> {key}", f"{col} -> {key}"
    if f.type == Field.NUMBER:
        return f"(case when jsonb_typeof({raw}) = 'number' then ({text})::float8 end)"
    if f.type == Field.BOOL:
        return f"(case when jsonb_typeof({raw}) = 'boolean' then ({text})::boolean end)"
    if f.type == Field.DATETIME:
        return f"(case when pg_input_is_valid({text}, 'timestamptz') then ({text})::timestamptz end)"
    if f.type == Field.LIST:
        return f"(case when jsonb_typeof({raw}) = 'array' then {raw} end)"
    if f.type == Field.JSON:
        return f"({raw})"
    return f"({text})"


def select_expr(f: Field) -> str:
    expr = value_expr(f)
    return f"left(({expr})::text, {JSON_DISPLAY_CHARS})" if f.type == Field.JSON else expr


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
    if f.type == Field.BOOL:
        if "yes" in picked:
            parts.append(f"{expr} is true")
        if "no" in picked:
            parts.append(f"{expr} is false")
    elif f.type == Field.LIST:
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
        if f.type == Field.LIST:
            parts.append(f"({expr} is null or {expr}::text in ('[]', '{{}}'))")
        elif f.type == Field.BOOL:
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
    if f.type == Field.NUMBER:
        return _number_filter(expr, text, f.label)
    if f.type == Field.DATETIME:
        return _date_filter(expr, text, f.label)
    if f.type == Field.BOOL:
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
        text = [f for f in (shown or fields.values()) if f.type in (Field.TEXT, Field.LINK)][:MAX_SEARCH_FIELDS]
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
            if f.type != Field.JSON:
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


def _timeout(cur, seconds: str = QUERY_TIMEOUT) -> None:
    cur.execute(f"set local statement_timeout = '{seconds}'")


def count_rows(ds: Dataset, where: str, params: list) -> int:
    key = f"dscount:{ds.pk}:{hash((where, repr(params)))}"
    found = cache.get(key)
    if found is None:
        with transaction.atomic(), connection.cursor() as cur:
            _timeout(cur)
            cur.execute(f"select count(*) from {relation(ds.schema, ds.name)} d{where}", params)
            found = cur.fetchone()[0]
        cache.set(key, found, COUNT_CACHE_SECONDS)
    return found


def fetch_rows(ds: Dataset, shown: list[Field], where: str, params: list, order: str, start: int, limit: int) -> list[dict]:
    select = ", ".join(f"{select_expr(f)} as c{i}" for i, f in enumerate(shown)) or "null"
    sql = f"select {select} from {relation(ds.schema, ds.name)} d{where}{order} limit %s offset %s"
    with transaction.atomic(), connection.cursor() as cur:
        _timeout(cur)
        cur.execute(sql, [*params, limit, start])
        return [{f.path: plain(v) for f, v in zip(shown, row)} for row in cur.fetchall()]
