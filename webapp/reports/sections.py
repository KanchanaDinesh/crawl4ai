"""Setting up a newsletter: where to start from, good guesses for a dataset's fields, and reading the section form. Only what the catalog offers passes."""

from webapp.catalog.models import Dataset, Field
from webapp.catalog.rights import can_view
from webapp.charts.forms import choosable_datasets, dataset_named, filter_rows, read_filters  # noqa: F401 - the same lists as the chart builder
from webapp.explore import query

from .models import Report, ReportSection

MAX_SECTIONS = 20
TITLE_NAMES = ("title", "name", "headline", "subject")
TEXT_NAMES = ("summary", "description", "about", "snippet", "details", "reason", "notes")
LINK_NAMES = ("url", "website", "link", "final_url", "homepage")
DATE_NAMES = ("first_seen_at", "first_seen", "created_at", "created", "published_at", "saved_at", "run_at", "checked_at", "date")
SCHEDULE_DAYS = {Report.DAILY: 1, Report.WEEKLY: 7, Report.MONTHLY: 30}
NEWS, COMPANIES, ADS, MARKETS = "news.news_items", "companies.v_companies", "vehicles.v_vehicle_ads_latest", "markets.v_latest"


def _news(heading: str, group: str, **extra) -> dict:
    return {"ref": NEWS, "heading": heading, "title_field": "title", "text_field": "summary", "link_field": "url", "date_field": "first_seen_at", "days": 1,
            "detail_fields": ["source", "published_at"], "sort_field": "published_at", "limit": 8, "filters": [{"field": "group_code", "value": [group]}], **extra}


def _companies(heading: str, filters: list, **extra) -> dict:
    return {"ref": COMPANIES, "heading": heading, "layout": ReportSection.TABLE, "title_field": "name", "link_field": "website", "date_field": "first_seen", "days": 7,
            "detail_fields": ["country", "industry", "website", "site_status"], "limit": 25, "filters": filters, **extra}


def _ads(**extra) -> dict:
    return {"ref": ADS, "heading": "New vehicle ads", "layout": ReportSection.TABLE, "title_field": "title", "link_field": "url", "date_field": "first_seen", "days": 1,
            "detail_fields": ["price", "location", "year", "site"], "limit": 25, **extra}


def _prices(heading: str, classes: list, **extra) -> dict:
    return {"ref": MARKETS, "heading": heading, "layout": ReportSection.TABLE, "title_field": "name", "detail_fields": ["price", "change_pct", "currency"], "sort_field": "change_pct",
            "limit": 15, "filters": [{"field": "asset_class", "value": classes}], **extra}


READY_MADE = {
    "news": {"name": "Daily news", "about": "headlines from Sri Lanka and the world", "sections": [
        {"ref": NEWS, "heading": "Today in numbers", "date_field": "first_seen_at", "days": 1, "limit": 0, "breakdown_field": "group_code"},
        _news("Sri Lanka", "LK"), _news("World", "GLOBAL"), _news("Business and markets", "MARKETS"), _news("Technology", "TECH")]},
    "companies": {"name": "New companies", "about": "companies added to the directory", "sections": [
        _companies("New in Sri Lanka", [{"field": "country", "value": ["LK"]}]),
        _companies("New everywhere", [], breakdown_field="country")]},
    "vehicles": {"name": "New vehicle ads", "about": "the latest ads, with prices", "sections": [_ads(breakdown_field="site")]},
    "markets": {"name": "Markets", "about": "indices, metals, currencies", "sections": [_prices("Indices", ["index"]), _prices("Gold and silver", ["commodity", "commodity_lkr"]), _prices("Currencies", ["fx"])]},
    "briefing": {"name": "Daily briefing", "about": "markets, news, new ads and companies together", "sections": [
        _prices("Markets", ["index", "commodity"]), _news("Sri Lanka", "LK", limit=6), _news("World", "GLOBAL", limit=6),
        _ads(limit=10), _companies("New companies in Sri Lanka", [{"field": "country", "value": ["LK"]}], limit=10)]},
}


class SectionProblem(ValueError):
    """What to tell the person."""


def _spec_for(item: dict, user, schedule: str) -> dict | None:
    """A ready-made section, if this person may see its dataset and it still has those fields."""
    schema, _, name = item["ref"].partition(".")
    ds = Dataset.objects.filter(schema=schema, name=name, missing=False).first()
    if ds is None or not can_view(user, ds):
        return None
    fields = query.usable_fields(ds, user)
    spec = {k: v for k, v in item.items() if k != "ref"}
    if spec.get("date_field") and spec.get("days") and schedule in SCHEDULE_DAYS:
        spec["days"] = SCHEDULE_DAYS[schedule]
    spec["detail_fields"] = [p for p in spec.get("detail_fields", []) if p in fields]
    spec["filters"] = [f for f in spec.get("filters", []) if f["field"] in fields]
    for key in ("title_field", "text_field", "link_field", "date_field", "sort_field", "breakdown_field"):
        if spec.get(key) and spec[key] not in fields:
            spec[key] = ""
    return {"dataset": ds, **spec}


def ready_made(user) -> list[dict]:
    """Only what would have something in it for this person."""
    out = []
    for code, start in READY_MADE.items():
        if any(_spec_for(item, user, "") for item in start["sections"]):
            out.append({"code": code, "name": start["name"], "about": start["about"]})
    return out


def _spec_from_section(section: ReportSection) -> dict:
    names = ("dataset", "heading", "layout", "title_field", "text_field", "link_field", "detail_fields", "date_field", "days", "sort_field", "sort_desc", "limit", "breakdown_field", "filters")
    return {n: getattr(section, n) for n in names}


def starting_point(start: str, user, schedule: str) -> tuple[str, list[dict]]:
    """A name and the sections to begin with."""
    if start == "blank":
        return "Newsletter", []
    if start.startswith("dataset:"):
        ds = dataset_named(user, start.removeprefix("dataset:"))
        if ds is None:
            raise SectionProblem("Choose a dataset you may see.")
        return ds.label, [_spec_from_section(guessed_section(ds, user, None, schedule))]
    chosen = READY_MADE.get(start)
    if chosen is None:
        raise SectionProblem("Choose where to start from.")
    specs = [spec for spec in (_spec_for(item, user, schedule) for item in chosen["sections"]) if spec]
    if not specs:
        raise SectionProblem("You don't have the right to see the data this one uses.")
    return chosen["name"], specs


def _all_fields(ds: Dataset, user) -> list[Field]:
    """In the catalog's order, including ones not on screen in the grid: a headline's summary can be off there and still belong in a newsletter."""
    return sorted((f for f in query.usable_fields(ds, user).values() if f.base != Field.JSON), key=lambda f: (f.order, f.pk))


def _named(fields: list[Field], names: tuple, bases: tuple) -> Field | None:
    usable = [f for f in fields if f.base in bases]
    for name in names:
        for f in usable:
            if (f.column if not f.json_key else f.json_key).lower() == name or f.path.lower() == name:
                return f
    return None


def guessed_section(ds: Dataset, user, report: Report | None, schedule: str | None = None) -> ReportSection:
    """Good first choices, so a dataset gives a sensible section without touching anything."""
    fields = _all_fields(ds, user)
    title = _named(fields, TITLE_NAMES, (Field.TEXT,)) or query.label_field(ds)
    text = _named(fields, TEXT_NAMES, (Field.TEXT,))
    links = [f for f in fields if f.base == Field.LINK and "image" not in f.path.lower()]
    link = _named(fields, LINK_NAMES, (Field.LINK,)) or (links[0] if links else None)
    dates = [f for f in fields if f.base in (Field.DATETIME, Field.DATE)]
    date = _named(fields, DATE_NAMES, (Field.DATETIME, Field.DATE)) or (dates[0] if dates else None)
    taken = {f.path for f in (title, text, link, date) if f}
    details = [f.path for f in fields if f.path not in taken and f.shown and f.values and f.base in (Field.TEXT, Field.CHOICE)][:3]
    period = SCHEDULE_DAYS.get(schedule if schedule is not None else (report.schedule if report else ""), 7)
    return ReportSection(report=report, dataset=ds, heading=ds.label, layout=ReportSection.ARTICLES if text else ReportSection.TABLE, title_field=title.path if title else "",
                         text_field=text.path if text else "", link_field=link.path if link else "", date_field=date.path if date else "", days=period if date else 0,
                         detail_fields=details, limit=10 if text else 25)


def choices(ds: Dataset | None, user, section: ReportSection | None) -> dict:
    """What the form's lists offer for this dataset."""
    context = {"layouts": ReportSection.LAYOUTS, "periods": ReportSection.PERIODS, "max_items": ReportSection.MAX_ITEMS, "filters": filter_rows(section)}
    if ds is not None:
        fields = _all_fields(ds, user)
        chosen = set(section.detail_fields or []) if section else set()
        context["fields"] = {"all": fields, "text": [f for f in fields if f.base in (Field.TEXT, Field.CHOICE, Field.EMAIL)], "links": [f for f in fields if f.base == Field.LINK],
                             "dates": [f for f in fields if f.base in (Field.DATETIME, Field.DATE)], "filter": fields,
                             "breakdown": [f for f in fields if f.base not in (Field.DATETIME, Field.DATE) and f.path != ds.record_key],
                             "details": [{"f": f, "on": f.path in chosen} for f in fields]}
    return context


def _picked(post, name: str, usable: dict, bases: tuple | None = None) -> str:
    path = post.get(name, "")
    if path in usable and (bases is None or usable[path].base in bases):
        return path
    return ""


def read_section(post, user, report: Report, section: ReportSection | None = None) -> ReportSection:
    ds = dataset_named(user, post.get("dataset"))
    if ds is None:
        raise SectionProblem("Choose a dataset.")
    usable = {p: f for p, f in query.usable_fields(ds, user).items() if f.base != Field.JSON}
    section = section or ReportSection(report=report)
    section.dataset = ds
    section.heading = " ".join(post.get("heading", "").split())[:120] or ds.label
    layout = post.get("layout", "")
    section.layout = layout if layout in ReportSection.LAYOUTS else ReportSection.TABLE
    section.title_field = _picked(post, "title_field", usable)
    section.text_field = _picked(post, "text_field", usable)
    section.link_field = _picked(post, "link_field", usable)
    section.date_field = _picked(post, "date_field", usable, (Field.DATETIME, Field.DATE))
    section.sort_field = _picked(post, "sort_field", usable)
    section.sort_desc = post.get("sort_dir", "desc") != "asc"
    section.breakdown_field = _picked(post, "breakdown_field", usable)
    section.detail_fields = [p for p in post.getlist("detail_fields") if p in usable][:8]
    days = post.get("days", "0")
    section.days = int(days) if days.isdigit() and int(days) in ReportSection.PERIODS else 0
    try:
        section.limit = max(0, min(ReportSection.MAX_ITEMS, int(post.get("limit") or 0)))
    except ValueError:
        section.limit = 10
    if section.days and not section.date_field:
        raise SectionProblem("Choose which date decides how recent a row is, or choose Everything.")
    if section.limit and not section.title_field and section.layout == ReportSection.ARTICLES:
        raise SectionProblem("Choose which field is each article's heading.")
    section.filters = read_filters(post, ds, user)
    return section


def move(report: Report, section: ReportSection, by: int) -> None:
    ordered = list(report.sections.all())
    i = ordered.index(section)
    j = i + by
    if 0 <= j < len(ordered):
        ordered[i], ordered[j] = ordered[j], ordered[i]
        for n, item in enumerate(ordered):
            ReportSection.objects.filter(pk=item.pk).update(order=n)
