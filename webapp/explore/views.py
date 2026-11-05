"""Datasets: the list, and one page and one set of endpoints that work for every dataset in the catalog."""

import json
import re

from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from webapp.accounts.rights import menu_required
from webapp.audit.log import record
from webapp.catalog import rights
from webapp.catalog.models import Dataset, Field

from . import export, query
from .models import SavedView

MAX_LIMIT = 500
SLOW = "That took too long. Narrow it down with a filter and try again."
CHANGED = "This dataset has changed since the last catalog scan. Ask an administrator to scan again."


@menu_required("datasets")
def dataset_list(request):
    q = request.GET.get("q", "").strip()
    found = rights.visible_datasets(request.user)
    if q:
        found = found.filter(Q(label__icontains=q) | Q(description__icontains=q) | Q(name__icontains=q)
                             | Q(schema__icontains=q) | Q(fields__label__icontains=q)).distinct()
    template = "explore/_cards.html" if request.htmx else "explore/list.html"
    return render(request, template, {"datasets": found.order_by("schema", "label"), "q": q})


def _dataset(request, ref: str) -> Dataset:
    schema, _, name = ref.partition(".")
    ds = Dataset.objects.filter(schema=schema, name=name).first()
    if ds is None:
        raise Http404("No such dataset")
    if not rights.can_view(request.user, ds):
        raise PermissionDenied("Your roles don't include this dataset.")
    return ds


def _column(f: Field, first: set[str]) -> dict:
    col = {"field": f.path, "title": f.label, "type": f.type, "shown": f.path in first, "description": f.description,
           "noun": "value", "filter": "text"}
    if f.type == Field.NUMBER:
        col["filter"] = "number"
    elif f.type == Field.DATETIME:
        col["filter"] = "date"
    elif f.type == Field.BOOL:
        col.update(filter="choice", choices=["yes", "no"])
    elif f.values:
        col.update(filter="choice", choices=list(f.values))
    if col.get("choices") and f.fill_pct < 100:
        col["choices"] = [query.EMPTY, *col["choices"]]
    return col


def _views_for(request, ds: Dataset) -> list[dict]:
    views = ds.saved_views.filter(Q(owner=request.user) | Q(shared=True)).select_related("owner")
    return [{"id": v.pk, "name": v.name, "shared": v.shared, "mine": v.owner_id == request.user.pk,
             "owner": v.owner.get_username(), "state": v.state} for v in views]


@menu_required("datasets")
def dataset_page(request, ref):
    ds = _dataset(request, ref)
    fields = query.usable_fields(ds)
    views = _views_for(request, ds)
    first = {f.path for f in query.shown_first(fields)}
    wanted = request.GET.get("view", "")
    current = next((v for v in views if str(v["id"]) == wanted), None)
    config = {
        "ref": ds.ref, "columns": [_column(f, first) for f in fields.values()], "views": views,
        "currentView": current["id"] if current else None, "state": current["state"] if current else None,
        "canExport": rights.can_export(request.user, ds), "exportLimit": export.LIMIT,
        "defaultSort": {"field": ds.default_sort_field, "dir": ds.default_sort_dir} if ds.default_sort_field in fields else None,
        "urls": {"rows": reverse("dataset_rows", args=[ds.ref]), "export": reverse("dataset_export", args=[ds.ref, "FMT"]),
                 "save": reverse("dataset_view_save", args=[ds.ref]),
                 "delete": reverse("dataset_view_delete", args=[ds.ref, 0]), "page": reverse("dataset", args=[ds.ref])},
    }
    return render(request, "explore/dataset.html", {"ds": ds, "config": config})


def _json_param(request, name: str, default):
    raw = request.GET.get(name)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise query.QueryProblem(f"{name} isn't valid") from None


def _parse(request, ds: Dataset) -> dict:
    """What a rows or export request asks for, checked against the catalog."""
    fields = query.usable_fields(ds)
    wanted = _json_param(request, "cols", None)
    if wanted is None:
        shown = query.shown_first(fields)
    else:
        if not isinstance(wanted, list):
            raise query.QueryProblem("cols must be a list")
        shown = [fields[c] for c in wanted if isinstance(c, str) and c in fields]
    if not shown:
        raise query.QueryProblem("Choose at least one column")
    where, params = query.where_clause(fields, _json_param(request, "filters", []), request.GET.get("q", ""), shown)
    return {"shown": shown, "where": where, "params": params,
            "order": query.order_clause(ds, fields, _json_param(request, "sorts", []))}


def _problem(e: Exception) -> JsonResponse:
    if isinstance(e, query.QueryProblem):
        return JsonResponse({"error": str(e)}, status=400)
    text = str(e).lower()
    if "statement timeout" in text or "canceling statement" in text:
        return JsonResponse({"error": SLOW}, status=503)
    return JsonResponse({"error": CHANGED}, status=500)


@menu_required("datasets")
def rows(request, ref):
    ds = _dataset(request, ref)
    t0 = timezone.now()
    try:
        start = max(0, int(request.GET.get("start", 0)))
        limit = min(MAX_LIMIT, max(1, int(request.GET.get("limit", 100))))
        q = _parse(request, ds)
        total = query.count_rows(ds, q["where"], q["params"])
        found = query.fetch_rows(ds, q["shown"], q["where"], q["params"], q["order"], start, limit)
    except ValueError as e:
        return _problem(e if isinstance(e, query.QueryProblem) else query.QueryProblem("start and limit must be numbers"))
    except DatabaseError as e:
        return _problem(e)
    for i, r in enumerate(found):
        r["_row"] = start + i
    return JsonResponse({"total": total, "start": start, "rows": found,
                         "ms": round((timezone.now() - t0).total_seconds() * 1000)})


@menu_required("datasets")
def export_file(request, ref, fmt):
    ds = _dataset(request, ref)
    if not rights.can_export(request.user, ds):
        raise PermissionDenied("Your roles don't include exporting this dataset.")
    if fmt not in export.FORMATS:
        raise Http404("No such format")
    try:
        q = _parse(request, ds)
        name = re.sub(r"[^\w-]+", "-", ds.label).strip("-").lower() or "data"
        filename = f"{name}-{timezone.localtime():%Y-%m-%d}.{fmt}"
        response, n = export.respond(fmt, filename, ds, q["shown"], q["where"], q["params"], q["order"])
    except query.QueryProblem as e:
        return _problem(e)
    except DatabaseError as e:
        return _problem(e)
    record(request, "export", f"Exported {n:,} rows of {ds.label} as {fmt.upper()}", target_type="dataset", target_id=ds.ref,
           detail={"format": fmt, "rows": n, "limit_reached": n >= export.LIMIT, "columns": [f.path for f in q["shown"]],
                   "filters": _json_param(request, "filters", []), "q": request.GET.get("q", "")})
    return response


def _clean_state(ds: Dataset, state) -> dict:
    """Only what a view is made of, and only fields that exist; rows are checked again whenever they're asked for."""
    fields = query.usable_fields(ds)
    state = state if isinstance(state, dict) else {}
    columns = []
    for c in state.get("columns") or []:
        if isinstance(c, dict) and c.get("field") in fields:
            width = c.get("width")
            columns.append({"field": c["field"], "visible": bool(c.get("visible", True)),
                            "width": int(width) if isinstance(width, (int, float)) and 20 <= width <= 2000 else None})
    sorts = [{"field": s["field"], "dir": "desc" if s.get("dir") == "desc" else "asc"}
             for s in state.get("sorts") or [] if isinstance(s, dict) and s.get("field") in fields]
    filters = [{"field": f["field"], "value": f.get("value")}
               for f in state.get("filters") or [] if isinstance(f, dict) and f.get("field") in fields]
    return {"columns": columns, "sorts": sorts, "filters": filters, "q": str(state.get("q") or "")[:200]}


@require_POST
@menu_required("datasets")
def view_save(request, ref):
    """Save under a name: a person's own view of that name is replaced, otherwise a new one is made."""
    ds = _dataset(request, ref)
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        data = {}
    name = " ".join(str(data.get("name") or "").split())[:80]
    if not name:
        return JsonResponse({"error": "Give the view a name"}, status=400)
    state, shared = _clean_state(ds, data.get("state")), bool(data.get("shared"))
    try:
        with transaction.atomic():
            view, created = SavedView.objects.update_or_create(dataset=ds, owner=request.user, name=name,
                                                               defaults={"state": state, "shared": shared})
            record(request, "view_saved", f"View “{name}” {'saved' if created else 'updated'} on {ds.label}"
                   + (" and shared" if shared else ""), target_type="dataset", target_id=ds.ref, detail={"view": view.pk})
    except IntegrityError:
        return JsonResponse({"error": "That name was just taken; try again"}, status=409)
    return JsonResponse({"ok": True, "id": view.pk, "url": f"{reverse('dataset', args=[ds.ref])}?view={view.pk}"})


@require_POST
@menu_required("datasets")
def view_delete(request, ref, pk):
    ds = _dataset(request, ref)
    view = SavedView.objects.filter(pk=pk, dataset=ds).first()
    if view is None:
        raise Http404("No such view")
    if view.owner_id != request.user.pk and not rights.is_admin(request.user):
        raise PermissionDenied("Only its owner or an administrator can delete a view.")
    with transaction.atomic():
        name = view.name
        record(request, "view_deleted", f"View “{name}” deleted from {ds.label}", target_type="dataset", target_id=ds.ref)
        view.delete()
    return JsonResponse({"ok": True})
