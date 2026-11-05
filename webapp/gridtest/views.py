"""The grid test (plan phase 1): the same company list in AG Grid Community and in Tabulator, to choose the grid.

Rows come from companies.v_companies (about 130,000), paged, sorted and filtered in PostgreSQL. Edits go to
GridTestEdit (never to the collected data) and the audit log, and are shown on top of the real values.
"""

import json
import time

from django.db import connection, transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from webapp.accounts.rights import menu_required
from webapp.audit.log import record

from .models import GridTestEdit

TAGS = ["Priority", "Partner", "Follow up", "Checked", "Not a company"]
COLUMNS = [   # what the grids show; in phase 2 this comes from the catalog
    {"field": "name", "title": "Name", "type": "text", "width": 260},
    {"field": "country", "title": "Country", "type": "choice", "editable": True, "width": 90},
    {"field": "source", "title": "Source", "type": "text", "width": 100},
    {"field": "website", "title": "Website", "type": "link", "width": 220},
    {"field": "industry", "title": "Industry", "type": "text", "editable": True, "width": 220},
    {"field": "tags", "title": "Tags (test field)", "type": "multi", "editable": True, "choices": TAGS, "width": 200},
    {"field": "notes", "title": "Notes (test field)", "type": "text", "editable": True, "width": 200},
    {"field": "site_status", "title": "Website read", "type": "text", "width": 110},
    {"field": "first_seen", "title": "First seen", "type": "date", "width": 120},
]
EDITABLE = {c["field"]: c for c in COLUMNS if c.get("editable")}
SORTABLE = {"name", "country", "source", "website", "industry", "site_status", "first_seen"}
FILTERS = {"country": "country", "source": "source", "site_status": "site_status"}
MAX_LIMIT = 500


def _countries() -> list[str]:
    with connection.cursor() as cur:
        cur.execute("select distinct country from companies.companies where country is not null order by 1")
        return [r[0] for r in cur.fetchall()]


@menu_required("grid_test")
def page(request):
    columns = [dict(c) for c in COLUMNS]
    for c in columns:
        if c["field"] == "country":
            c["choices"] = _countries()
    with connection.cursor() as cur:
        cur.execute("select distinct source from companies.companies order by 1")
        sources = [r[0] for r in cur.fetchall()]
        cur.execute("select distinct site_status from companies.v_companies order by 1")
        statuses = [r[0] for r in cur.fetchall()]
    return render(request, "gridtest/page.html", {
        "config": {"columns": columns, "tags": TAGS},
        "countries": [c for c in columns if c["field"] == "country"][0]["choices"],
        "sources": sources, "statuses": statuses})


def _where(request) -> tuple[str, list]:
    where, params = [], []
    q = request.GET.get("q", "").strip().lower()
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(lower(name) like %s or domain like %s or lower(coalesce(industry, '')) like %s)")
        params += [like, like, like]
    for key, col in FILTERS.items():
        if request.GET.get(key):
            where.append(f"{col} = %s")
            params.append(request.GET[key])
    return (" where " + " and ".join(where)) if where else "", params


def _overlay(rows: list[dict]) -> None:
    """Show the test edits on top of the real values (the newest edit per field wins)."""
    ids = [r["id"] for r in rows]
    for e in GridTestEdit.objects.filter(company_id__in=ids).order_by("at", "id"):
        for r in rows:
            if r["id"] == e.company_id:
                r[e.field] = e.value
                r.setdefault("_edited", []).append(e.field)


@menu_required("grid_test")
def rows(request):
    t0 = time.perf_counter()
    try:
        start = max(0, int(request.GET.get("start", 0)))
        limit = min(MAX_LIMIT, max(1, int(request.GET.get("limit", 100))))
    except ValueError:
        return JsonResponse({"error": "start and limit must be numbers"}, status=400)
    sort = request.GET.get("sort") if request.GET.get("sort") in SORTABLE else "name"
    direction = "desc" if request.GET.get("dir") == "desc" else "asc"
    where, params = _where(request)
    with connection.cursor() as cur:
        cur.execute(f"select count(*) from companies.v_companies{where}", params)
        total = cur.fetchone()[0]
        cur.execute(f"""select id, name, country, source, website, domain, industry, site_status,
                to_char(first_seen at time zone 'Asia/Colombo', 'YYYY-MM-DD') as first_seen
            from companies.v_companies{where}
            order by {sort} {direction} nulls last, id limit %s offset %s""", [*params, limit, start])
        cols = [c.name for c in cur.description]
        found = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in found:
        r["tags"], r["notes"] = [], ""
    _overlay(found)
    return JsonResponse({"total": total, "start": start, "rows": found,
                         "ms": round((time.perf_counter() - t0) * 1000)})


def _clean(field: str, value):
    col = EDITABLE.get(field)
    if col is None:
        raise ValueError(f"{field} can't be edited")
    if col["type"] == "multi":
        if isinstance(value, str):
            value = [v.strip() for v in value.split(",") if v.strip()]
        if not isinstance(value, list) or any(v not in col["choices"] for v in value):
            raise ValueError(f"Tags must be some of: {', '.join(col['choices'])}")
        return sorted(set(value), key=col["choices"].index)
    value = "" if value is None else str(value).strip()
    if len(value) > 200:
        raise ValueError("At most 200 characters")
    if field == "country" and value and value not in _countries():
        raise ValueError(f"{value} isn't a country code in the data")
    return value


def _body(request) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return {}


@require_POST
@menu_required("grid_test")
def edit(request):
    data = _body(request)
    cid, field = str(data.get("id", "")), data.get("field", "")
    try:
        value = _clean(field, data.get("value"))
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    with transaction.atomic():
        GridTestEdit.objects.create(company_id=cid, field=field, value=value, user=request.user,
                                    engine=str(data.get("engine", ""))[:20])
        record(request, "gridtest_edit", f"Grid test: {field} of {cid} set to {value!r}", target_type="company",
               target_id=cid, detail={"field": field, "value": value, "engine": data.get("engine")})
    return JsonResponse({"ok": True, "value": value})


@require_POST
@menu_required("grid_test")
def bulk(request):
    data = _body(request)
    ids = [str(i) for i in data.get("ids") or []][:5000]
    field = data.get("field", "")
    if not ids:
        return JsonResponse({"error": "Select some rows first"}, status=400)
    try:
        value = _clean(field, data.get("value"))
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    with transaction.atomic():
        GridTestEdit.objects.bulk_create([GridTestEdit(company_id=i, field=field, value=value, user=request.user,
                                                       engine=str(data.get("engine", ""))[:20]) for i in ids])
        record(request, "gridtest_bulk_edit", f"Grid test: {field} set to {value!r} on {len(ids)} rows",
               target_type="company", detail={"field": field, "value": value, "ids": ids[:200], "count": len(ids),
                                              "engine": data.get("engine")})
    return JsonResponse({"ok": True, "count": len(ids), "value": value})


@require_POST
@menu_required("grid_test")
def reset(request):
    n, _ = GridTestEdit.objects.filter(user=request.user).delete()
    return JsonResponse({"ok": True, "removed": n})
