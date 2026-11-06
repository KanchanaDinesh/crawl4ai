"""The Articles page: read and filter what `python -m articles` saved. It only reads.

Who sees it: the menu right Articles, plus the right to see the dataset Tech articles. The page shows that dataset's rows, so taking the dataset
away from a role in the catalog also closes this page, or it would be a way around the rights.
"""

from dataclasses import dataclass
from functools import wraps
from urllib.parse import urlsplit

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render

from articles import queries as q
from webapp.accounts.rights import menu_required
from webapp.catalog.models import Dataset
from webapp.catalog.rights import can_view, is_admin
from webapp.insights.common import PAGE, Reader, bars, int_param, kpis, list_response, shell, shown, text_param, url_with
from webapp.insights.templatetags.insights_ui import num

TAG_LABELS = {"ai": "AI", "cloud": "Cloud", "email": "Email", "web_services": "Web services", "security": "Security", "data": "Data", "developer": "Developer",
              "business_apps": "Business apps", "networking": "Networking", "payments": "Payments", "ecommerce": "E-commerce", "chips": "Chips"}
PERIODS = ((7, "Last 7 days"), (30, "Last 30 days"), (90, "Last 90 days"), (365, "Last year"))
COMPANY_STATUS = (("articles", "Has articles"), ("feed", "Has a feed, nothing about these services yet"), ("none", "No feed we can read"))


@dataclass(frozen=True)
class Tab:
    code: str
    label: str
    url_name: str


TABS = (Tab("articles", "Articles", "articles"), Tab("companies", "Companies", "articles_companies"))


def _may_read(user) -> bool:
    if is_admin(user):
        return True
    dataset = Dataset.objects.filter(schema="articles", name="v_articles").first()
    return dataset is not None and can_view(user, dataset)


def readers_only(view):
    @wraps(view)
    @menu_required("articles")
    def wrapper(request, *args, **kwargs):
        if not _may_read(request.user):
            raise PermissionDenied("Your roles don't include the dataset Tech articles. An administrator can grant it on the Catalog page.")
        return view(request, *args, **kwargs)
    return wrapper


def tag_label(tag: str) -> str:
    return TAG_LABELS.get(tag, tag.replace("_", " ").capitalize())


# ── articles ────────────────────────────────────────────────────────────────────────────────────────────────
def _filters(request) -> dict:
    days = request.GET.get("days", "")
    country = text_param(request, "country").upper()
    return {"tag": text_param(request, "tag"), "country": country if country in q.COUNTRY_NAMES else "", "company": text_param(request, "company"),
            "days": days if days.isdigit() and int(days) in dict(PERIODS) else "", "q": text_param(request, "q")}


def _item(row: dict) -> dict:
    return {**row, "tag_names": [tag_label(t) for t in row["tags"]]}


def _rows_context(db, f: dict, offset: int = 0) -> dict:
    data = db.get(q.items, f["tag"] or None, f["country"] or None, f["company"] or None, int(f["days"]) if f["days"] else None, f["q"] or None, PAGE, offset, default={"total": 0, "items": []})
    end = offset + len(data["items"])
    more = url_with("articles_rows", f, offset=end) if end < data["total"] else None
    return {"items": [_item(r) for r in data["items"]], "more": more, "count": shown(data["total"], end, "click one to read it")}


def _shell(request, code: str, db, **context):
    ctx = shell(request, code, db, **context)
    ctx.update(tabs=TABS, tab=code, tag_label=tag_label)
    return ctx


@readers_only
def page_view(request):
    f = _filters(request)
    with Reader() as db:
        s = db.get(q.summary, default=None)
        context = {"f": f, "s": s if s and s["articles"] else None}
        if context["s"]:
            context.update(
                kpis=kpis((num(s["articles"]), "Articles"), (f"{num(s['companies'])} of {num(s['list_size'])}", "Companies with articles"), (num(s["full_text"]), "With the full text"),
                          (num(s["new_24h"]), "Added in 24 hours"), ((s["newest"] or "–")[:10], "Newest article")),
                tag_chips=[{"code": "", "name": "All tags", "n": s["articles"]}, *[{"code": r["tag"], "name": tag_label(r["tag"]), "n": r["n"]} for r in s["by_tag"]]],
                country_chips=[{"code": "", "name": "All countries", "n": None}, *[{"code": c, "name": n, "n": next((r["articles"] for r in s["coverage"] if r["country"] == c), 0)} for c, n in q.COUNTRY_NAMES.items()]],
                companies=db.get(q.company_choices, default=[]), periods=PERIODS, coverage=_coverage(s["coverage"]),
                top_companies=bars(s["top_companies"], "company", set_field="company", set_value=lambda r: r["company_id"]), **_rows_context(db, f))
    return render(request, "articles/page.html", _shell(request, "articles", db, **context))


def _coverage(rows: list[dict]) -> list[dict]:
    return [{**r, "name": q.COUNTRY_NAMES.get(r["country"], r["country"])} for r in rows]


@readers_only
def rows_view(request):
    f = _filters(request)
    offset = int_param(request, "offset", 0, lo=0)
    with Reader() as db:
        context = _rows_context(db, f, offset)
    return list_response(request, "articles/_items.html", context, context["count"], url_with("articles", f) if offset == 0 else None)


@readers_only
def read_view(request):
    """One article, in the side panel. Its text is the company's work, for reading here: the link goes to the original."""
    with Reader() as db:
        row = db.get(q.article, request.GET.get("url") or "", default=None)
    if row is None:
        if db.error:
            return render(request, "articles/_read.html", {"banner": db.error})
        raise Http404
    return render(request, "articles/_read.html", {"a": _item(row), "paragraphs": [p for p in (row.get("text") or "").split("\n") if p.strip()], "status": q.TEXT_STATUS.get(row["text_status"], row["text_status"]),
                                                   "countries": ", ".join(q.COUNTRY_NAMES.get(c, c) for c in row["countries"])})


# ── companies ───────────────────────────────────────────────────────────────────────────────────────────────
def _company_filters(request) -> dict:
    status = request.GET.get("status")
    country = text_param(request, "country").upper()
    return {"country": country if country in q.COUNTRY_NAMES else "", "status": status if status in dict(COMPANY_STATUS) else "", "q": text_param(request, "q")}


def _company_rows(db, f: dict, offset: int = 0) -> dict:
    data = db.get(q.companies, f["country"] or None, f["status"] or None, f["q"] or None, PAGE, offset, default={"total": 0, "items": []})
    end = offset + len(data["items"])
    more = url_with("articles_company_rows", f, offset=end) if end < data["total"] else None
    rows = [{**r, "articles_url": url_with("articles", {"company": r["id"]}), "site": urlsplit(r["website"]).netloc.removeprefix("www.") if r["website"] else ""} for r in data["items"]]
    return {"rows": rows, "more": more, "count": shown(data["total"], end, "click a company with articles to see them")}


@readers_only
def companies_view(request):
    f = _company_filters(request)
    with Reader() as db:
        coverage = db.get(q.coverage, default=[])
        context = {"f": f, "coverage": _coverage(coverage), "has_data": bool(coverage), "statuses": COMPANY_STATUS,
                   "country_chips": [{"code": "", "name": "All countries", "n": None}, *[{"code": c, "name": n, "n": next((r["companies"] for r in coverage if r["country"] == c), 0)} for c, n in q.COUNTRY_NAMES.items()]], **_company_rows(db, f)}
    return render(request, "articles/companies.html", _shell(request, "companies", db, **context))


@readers_only
def company_rows_view(request):
    f = _company_filters(request)
    offset = int_param(request, "offset", 0, lo=0)
    with Reader() as db:
        context = _company_rows(db, f, offset)
    return list_response(request, "articles/_company_rows.html", context, context["count"], url_with("articles_companies", f) if offset == 0 else None)
