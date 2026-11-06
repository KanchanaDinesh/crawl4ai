"""Reading the chart builder's form. Only what the catalog offers passes; the preview and saving use the same reading."""

from django.db.models import Q

from webapp.catalog.models import Dataset, Field
from webapp.catalog.rights import can_view, visible_datasets
from webapp.explore import query

from .models import Chart

FILTER_ROWS = 4


class FormProblem(ValueError):
    """What to tell the person."""


def choosable_datasets(user):
    return visible_datasets(user).order_by("schema", "label")


def _dataset(user, ref: str | None) -> Dataset | None:
    schema, _, name = (ref or "").partition(".")
    ds = Dataset.objects.filter(schema=schema, name=name, missing=False).first()
    return ds if ds is not None and can_view(user, ds) else None


def dataset_named(user, ref):
    return _dataset(user, ref)


def field_groups(ds: Dataset, user) -> dict:
    """What the form's lists offer for this dataset."""
    fields = list(query.usable_fields(ds, user).values())
    groupable = [f for f in fields if f.base != Field.JSON and f.path != ds.record_key]
    return {"group": groupable, "split": [f for f in groupable if f.base not in (Field.DATETIME, Field.DATE)],
            "numbers": [f for f in fields if f.base == Field.NUMBER], "filter": [f for f in fields if f.base != Field.JSON],
            "dates": [f.path for f in fields if f.base in (Field.DATETIME, Field.DATE)]}


def _filter_value(f: Field, text: str):
    """A field with a list of choices filters by those choices (comma between them); others as the grid's filters do."""
    if f.values or f.base == Field.BOOL or f.type in (Field.CHOICE, Field.MULTI):
        return [part.strip() for part in text.split(",") if part.strip()]
    return text


def read_filters(post, ds: Dataset, user) -> list[dict]:
    fields = query.usable_fields(ds, user)
    out = []
    for i in range(FILTER_ROWS):
        path, text = post.get(f"filter_field_{i}", ""), post.get(f"filter_value_{i}", "").strip()
        if path in fields and text:
            out.append({"field": path, "value": _filter_value(fields[path], text)})
    return out


def filter_rows(chart: Chart | None) -> list[dict]:
    """The filter rows to show: what's set, then empty ones."""
    rows = []
    for item in (chart.filters if chart else []) or []:
        value = item.get("value")
        rows.append({"field": item.get("field"), "value": ", ".join(map(str, value)) if isinstance(value, list) else (value or "")})
    while len(rows) < FILTER_ROWS:
        rows.append({"field": "", "value": ""})
    return [{"i": i, **r} for i, r in enumerate(rows[:FILTER_ROWS])]


def read_chart(post, user, chart: Chart | None = None) -> Chart:
    """A chart from the form, not saved: the preview draws it, and saving keeps it."""
    ds = _dataset(user, post.get("dataset"))
    if ds is None:
        raise FormProblem("Choose a dataset.")
    chart = chart or Chart(owner=user)
    chart.dataset = ds
    chart.name = " ".join(post.get("name", "").split())[:120]
    kind = post.get("kind", Chart.COLUMN)
    chart.kind = kind if kind in Chart.KINDS else Chart.COLUMN
    chart.group_field = post.get("group_field", "")
    unit = post.get("time_unit", "")
    chart.time_unit = unit if unit in Chart.TIME_UNITS else ""
    chart.split_field = post.get("split_field", "")
    measure = post.get("measure", Chart.COUNT)
    chart.measure = measure if measure in Chart.MEASURES else Chart.COUNT
    chart.measure_field = post.get("measure_field", "") if chart.measure != Chart.COUNT else ""
    chart.filters = read_filters(post, ds, user)
    try:
        chart.limit = max(1, min(50, int(post.get("limit") or 12)))
    except ValueError:
        chart.limit = 12
    chart.shared = "shared" in post
    return chart


def mine_or_shared(model, user):
    from webapp.catalog.rights import is_admin
    if is_admin(user):
        return model.objects.all()
    return model.objects.filter(Q(owner=user) | Q(shared=True))
