"""A chart's numbers. What was chosen becomes SQL here, built only from catalog fields the person may see (the same expressions as the
grid, so links, rollups and own fields can all be charted, and hidden rows are left out), with every value as a parameter."""

from dataclasses import dataclass, field

from django.conf import settings
from django.db import DatabaseError, connection, transaction

from webapp.catalog.models import Field
from webapp.catalog.rights import can_view
from webapp.catalog.sqlnames import lit, relation
from webapp.explore import query

from .models import Chart

EMPTY = "(empty)"
OTHER = "Other"
MAX_ROWS = 5000
MAX_SERIES = 8               # more colours than this can't be told apart; the rest go into Other
MAX_PIE = 7
MAX_POINTS = 400


class ChartProblem(ValueError):
    """What to tell the person."""


@dataclass
class ChartData:
    kind: str
    name: str
    categories: list = field(default_factory=list)
    series: list = field(default_factory=list)       # [{"name", "values", "slot"}]
    is_time: bool = False
    time_unit: str = ""
    number: float | None = None
    group_label: str = ""
    measure_label: str = ""
    note: str = ""


def _group_sql(f: Field, unit: str, alias: str) -> tuple[str, str]:
    """The SQL for one value to group by, and what has to be joined to get it: a list counts once per item."""
    expr = query.value_expr(f)
    if f.base == Field.LIST:
        source = f"jsonb_array_elements_text(coalesce({expr}, '[]'::jsonb))" if f.is_jsonb else f"unnest({expr})"
        return f"{alias}.v", f" cross join lateral {source} as {alias}(v)"
    if f.base == Field.DATETIME:
        return f"(date_trunc({lit(unit or 'month')}, ({expr}) at time zone {lit(settings.TIME_ZONE)}))::date::text", ""
    if f.base == Field.DATE:
        return f"(date_trunc({lit(unit or 'month')}, ({expr})::timestamp))::date::text", ""
    if f.base == Field.JSON:
        raise ChartProblem(f"{f.label} can't be grouped by: it holds structured data.")
    return f"({expr})::text", ""


def _measure_sql(chart: Chart, fields: dict) -> str:
    if chart.measure == Chart.COUNT:
        return "count(*)"
    if chart.measure not in Chart.MEASURES:
        raise ChartProblem("Choose what to work out.")
    f = fields.get(chart.measure_field)
    if f is None:
        raise ChartProblem("Choose a number field to work out.")
    if f.base != Field.NUMBER:
        raise ChartProblem(f"{f.label} isn't a number, so it can't be added up.")
    return f"{chart.measure}(({query.value_expr(f)})::float8)"


def _name(f: Field, value) -> str:
    if value is None or value == "":
        return EMPTY
    if f.base == Field.BOOL:
        return {"true": "yes", "false": "no"}.get(str(value), str(value))
    return str(value)


def _fetch(sql: str, params: list) -> list:
    try:
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(f"set local statement_timeout = '{query.QUERY_TIMEOUT}'")
            cur.execute(sql, params)
            return cur.fetchall()
    except DatabaseError as e:
        if "statement timeout" in str(e).lower() or "canceling statement" in str(e).lower():
            raise ChartProblem("That took too long. Add a filter and try again.") from None
        raise ChartProblem("This chart's dataset has changed since it was made. Check its fields.") from None


def _float(value) -> float | None:
    return None if value is None else float(value)


def _measure_label(chart: Chart, fields: dict) -> str:
    if chart.measure == Chart.COUNT:
        return "Rows"
    f = fields.get(chart.measure_field)
    return f"{Chart.MEASURES[chart.measure]} of {f.label}" if f else ""


def chart_data(chart: Chart, user) -> ChartData:
    ds = chart.dataset
    if not can_view(user, ds):
        raise ChartProblem("You don't have the right to see this chart's data.")
    if ds.missing:
        raise ChartProblem(f"The dataset {ds.label} isn't there any more.")
    fields = query.usable_fields(ds, user)
    try:
        where, params = query.where_clause(fields, chart.filters or [])
    except query.QueryProblem as e:
        raise ChartProblem(str(e)) from None
    where = query.not_hidden(ds, where)
    data = ChartData(kind=chart.kind, name=chart.name, measure_label=_measure_label(chart, fields))
    measure = _measure_sql(chart, fields)
    rel = relation(ds.schema, ds.name)
    if chart.kind == Chart.NUMBER:
        data.number = _float(_fetch(f"select {measure} from {rel} d{where}", params)[0][0])
        return data
    group = fields.get(chart.group_field)
    if group is None:
        raise ChartProblem("Choose what to group by." if not chart.group_field else "The field it groups by isn't there any more.")
    split = fields.get(chart.split_field) if chart.split_field else None
    if chart.split_field and split is None:
        raise ChartProblem("The field it splits by isn't there any more.")
    if chart.kind == Chart.PIE and split:
        raise ChartProblem("A doughnut shows one measure: remove the split, or choose columns.")
    if chart.kind == Chart.PIE and chart.lowest_first:
        raise ChartProblem("A doughnut shows shares of the whole, so it starts with the biggest. Choose bars or columns to show the smallest first.")
    group_sql, join = _group_sql(group, chart.time_unit, "g")
    split_sql, split_join = _group_sql(split, chart.time_unit, "s") if split else ("null", "")
    data.group_label = group.label
    data.is_time = group.base in (Field.DATETIME, Field.DATE)
    data.time_unit = (chart.time_unit or "month") if data.is_time else ""
    order = "1, 2" if data.is_time else f"3 {'asc' if chart.lowest_first else 'desc'} nulls last, 1, 2"
    rows = _fetch(f"select {group_sql}, {split_sql}, {measure} from {rel} d{join}{split_join}{where} group by 1, 2 order by {order} limit {MAX_ROWS}", params)
    _pivot(data, chart, group, split, rows)
    return data


def _pivot(data: ChartData, chart: Chart, group: Field, split: Field | None, rows: list) -> None:
    folds = chart.measure in (Chart.COUNT, Chart.SUM)     # only counts and sums can be added into an Other
    named = [(_name(group, g), _name(split, s) if split else "", _float(m)) for g, s, m in rows if not (data.is_time and g is None)]
    totals: dict[str, float] = {}
    measured = set()                      # groups with a value: one with none (an average of nothing) goes last, never first as a 0
    for g, _, m in named:
        totals[g] = totals.get(g, 0) + (m or 0)
        if m is not None:
            measured.add(g)
    if data.is_time:
        categories = sorted(totals)[-MAX_POINTS:]
        hidden = len(totals) - len(categories)
        keep = set(categories)
        fold_rest = False
    else:
        limit = min(chart.limit or 12, MAX_PIE - 1) if chart.kind == Chart.PIE else (chart.limit or 12)
        lowest = chart.lowest_first
        ranked = sorted(totals, key=lambda k: (k not in measured, totals[k] if lowest else -totals[k], k))
        categories, hidden, keep = ranked[:limit], max(0, len(ranked) - limit), set(ranked[:limit])
        fold_rest = folds and hidden > 0 and not lowest      # an Other of everything bigger would dwarf the smallest ones
        if fold_rest:
            categories = [*categories, OTHER]
    series_total: dict[str, float] = {}
    for _, s, m in named:
        series_total[s] = series_total.get(s, 0) + (m or 0)
    top = sorted(series_total, key=lambda k: (-series_total[k], k))[:MAX_SERIES - 1 if len(series_total) > MAX_SERIES else MAX_SERIES]
    has_other_series = folds and len(series_total) > len(top)
    series_names = sorted(top)            # the same colour for the same name, whichever is biggest today
    cells: dict[tuple[str, str], float] = {}
    for g, s, m in named:
        cat = g if g in keep else (OTHER if fold_rest else None)
        ser = s if s in top else (OTHER if has_other_series else None)
        if cat is None or ser is None or m is None:
            continue
        cells[(cat, ser)] = cells.get((cat, ser), 0) + m
    if has_other_series:
        series_names.append(OTHER)
    data.categories = categories
    data.series = [{"name": s or data.measure_label, "slot": i, "values": [cells.get((c, s)) for c in categories]} for i, s in enumerate(series_names)]
    if hidden and chart.lowest_first and not data.is_time:
        data.note = f"{hidden:,} bigger ones not shown"
    elif hidden and not fold_rest:
        data.note = f"{hidden:,} more not shown"
    elif hidden:
        data.note = f"{hidden:,} smaller ones are in Other"


def table_rows(data: ChartData, limit: int | None = None) -> tuple[list[str], list[list]]:
    """The same numbers as a table: for the table chart, and beside charts in reports."""
    heads = [data.group_label or "", *[s["name"] for s in data.series]]
    rows = [[c, *[s["values"][i] for s in data.series]] for i, c in enumerate(data.categories)]
    return heads, rows[:limit] if limit else rows


def show_number(value) -> str:
    if value is None:
        return "–"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"
