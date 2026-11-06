"""Datasets: the list, and one page and one set of endpoints that work for every dataset in the catalog."""

import json
import re

from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from webapp.accounts.rights import menu_required
from webapp.audit.log import record
from webapp.catalog import rights
from webapp.catalog.models import Dataset, Field
from warehouse import edits

from . import editing, export, query
from .models import SavedView

MAX_LIMIT = 500
SLOW = "That took too long. Narrow it down with a filter and try again."
CHANGED = "This dataset has changed since the last catalog scan. Ask an administrator to scan again."
NOT_ALLOWED = "The database doesn't allow this change yet. An administrator can fix that: uv run python -m warehouse migrate"
MAX_BULK = 5000


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


def _column(f: Field, first: set[str], may_edit: bool = False) -> dict:
    col = {"field": f.path, "title": f.label, "type": f.type, "shown": f.path in first, "description": f.description,
           "noun": "value", "filter": "text"}
    if may_edit and f.editable and not f.json_key:
        col["editable"] = True
        if f.values:
            col["edit_choices"] = list(f.values)
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
    may_edit, may_hide = rights.can_edit(request.user, ds), rights.can_hide(request.user, ds)
    target = ds.edit_target
    wanted = request.GET.get("view", "")
    current = next((v for v in views if str(v["id"]) == wanted), None)
    config = {
        "ref": ds.ref, "columns": [_column(f, first, may_edit and bool(target) and f.column in target.columns) for f in fields.values()], "views": views,
        "currentView": current["id"] if current else None, "state": current["state"] if current else None,
        "canExport": rights.can_export(request.user, ds), "exportLimit": export.LIMIT,
        "canEdit": may_edit, "canHide": may_hide, "hasRecords": bool(target), "maxBulk": MAX_BULK,
        "defaultSort": {"field": ds.default_sort_field, "dir": ds.default_sort_dir} if ds.default_sort_field in fields else None,
        "urls": {"rows": reverse("dataset_rows", args=[ds.ref]), "export": reverse("dataset_export", args=[ds.ref, "FMT"]),
                 "save": reverse("dataset_view_save", args=[ds.ref]),
                 "delete": reverse("dataset_view_delete", args=[ds.ref, 0]), "page": reverse("dataset", args=[ds.ref]),
                 "edit": reverse("dataset_edit_cell", args=[ds.ref]), "bulk": reverse("dataset_edit_bulk", args=[ds.ref]),
                 "hide": reverse("dataset_hide", args=[ds.ref]), "history": reverse("dataset_history", args=[ds.ref])},
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
    where = query.not_hidden(ds, where)
    return {"shown": shown, "where": where, "params": params,
            "order": query.order_clause(ds, fields, _json_param(request, "sorts", []))}


def _problem(e: Exception) -> JsonResponse:
    if isinstance(e, query.QueryProblem):
        return JsonResponse({"error": str(e)}, status=400)
    text = str(e).lower()
    if "statement timeout" in text or "canceling statement" in text:
        return JsonResponse({"error": SLOW}, status=503)
    return JsonResponse({"error": CHANGED}, status=500)


def _has_key(ds: Dataset, target: edits.Target) -> bool:
    return ds.fields.filter(column=target.key, json_key=None, missing=False).exists()


def _add_marks(target: edits.Target, found: list[dict]) -> None:
    """Which cells on this page were corrected, or wait for someone to look at them."""
    with connection.cursor() as cur:
        marks = edits.marks(cur, target.table, [r["_id"] for r in found]) if edits.available(cur) else {}
    for r in found:
        mark = marks.get(r["_id"])
        if mark:
            r["_edited"], r["_pending"] = mark["edited"], mark["pending"]


@menu_required("datasets")
def rows(request, ref):
    ds = _dataset(request, ref)
    t0 = timezone.now()
    try:
        start = max(0, int(request.GET.get("start", 0)))
        limit = min(MAX_LIMIT, max(1, int(request.GET.get("limit", 100))))
        q = _parse(request, ds)
        total = query.count_rows(ds, q["where"], q["params"])
        target = ds.edit_target
        key = target.key if target and _has_key(ds, target) else None
        found = query.fetch_rows(ds, q["shown"], q["where"], q["params"], q["order"], start, limit, key)
        if key:
            _add_marks(target, found)
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


# ── corrections ──────────────────────────────────────────────────────────────────────────────────────────────
def _body(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        raise editing.EditProblem("That request isn't valid.")
    return data


def _edit_problem(e: Exception) -> JsonResponse:
    if isinstance(e, editing.EditProblem):
        body = {"error": str(e)}
        if e.current is not editing.NOT_SEEN:
            body.update(conflict=True, current=e.current)
        return JsonResponse(body, status=e.status)
    if "permission denied" in str(e).lower():
        return JsonResponse({"error": NOT_ALLOWED}, status=503)
    return JsonResponse({"error": "That couldn't be saved. Try again in a moment."}, status=500)


def _editable(request, ref) -> Dataset:
    ds = _dataset(request, ref)
    if not rights.can_edit(request.user, ds):
        raise PermissionDenied("Your roles don't include editing this dataset.")
    return ds


def _records(data: dict) -> list[str]:
    ids = data.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise editing.EditProblem("Select at least one row first.")
    if len(ids) > MAX_BULK:
        raise editing.EditProblem(f"That's {len(ids):,} rows; do at most {MAX_BULK:,} at a time.")
    return list(dict.fromkeys(ids))


@require_POST
@menu_required("datasets")
def edit_cell(request, ref):
    """One cell. It carries the value the person saw: if it changed meanwhile, nothing is saved and they see the new one."""
    ds = _editable(request, ref)
    try:
        data = _body(request)
        record_id, seen = str(data.get("id") or ""), data.get("seen", editing.NOT_SEEN)
        f = editing.editable_field(ds, str(data.get("field") or ""))
        with transaction.atomic(), connection.cursor() as cur:
            editing.require_ready(cur)
            done = editing.save_cell(cur, ds, f, record_id, data.get("value"), request.user.get_username(), seen=seen)
            if done["changed"]:
                record(request, "cell_edited", f"{f.label} of a row in {ds.label} corrected", target_type="dataset", target_id=ds.ref,
                       detail={"record": record_id, "field": f.path, "value": done["value"]})
    except (editing.EditProblem, DatabaseError) as e:
        return _edit_problem(e)
    if done["changed"]:
        editing.bump_counts(ds)
    return JsonResponse({"ok": True, "value": done["value"], "linked": done["linked"]})


@require_POST
@menu_required("datasets")
def edit_bulk(request, ref):
    """One value for many rows, in one transaction: all of them or none. No "value I saw" here: the person chose to set them
    all, and the history keeps what each row said."""
    ds = _editable(request, ref)
    try:
        data = _body(request)
        ids = _records(data)
        f = editing.editable_field(ds, str(data.get("field") or ""))
        changed, linked = 0, {}
        with transaction.atomic(), connection.cursor() as cur:
            editing.require_ready(cur)
            value = None
            for record_id in ids:
                try:
                    done = editing.save_cell(cur, ds, f, record_id, data.get("value"), request.user.get_username(), origin="bulk")
                except editing.EditProblem as e:
                    if e.status == 404:      # gone since the list loaded: the others still count
                        continue
                    raise
                value, changed = done["value"], changed + done["changed"]
                if done["linked"]:
                    linked[record_id] = done["linked"]
            if changed:
                record(request, "rows_edited", f"{f.label} set on {changed:,} rows of {ds.label}", target_type="dataset", target_id=ds.ref,
                       detail={"records": ids[:200], "count": changed, "field": f.path, "value": value})
    except (editing.EditProblem, DatabaseError) as e:
        return _edit_problem(e)
    editing.bump_counts(ds)
    return JsonResponse({"ok": True, "value": value, "changed": changed, "linked": linked})


def _hider(request, ref) -> Dataset:
    ds = _dataset(request, ref)
    if not rights.can_hide(request.user, ds):
        raise PermissionDenied("Your roles don't include hiding rows of this dataset.")
    return ds


@require_POST
@menu_required("datasets")
def hide_rows(request, ref):
    """Hidden, not deleted: it stays hidden when a collector sees it again, and can be shown again from the Changes page."""
    ds = _hider(request, ref)
    try:
        ids = _records(_body(request))
        with transaction.atomic(), connection.cursor() as cur:
            editing.require_ready(cur)
            n = editing.hide_rows(cur, ds, ids, request.user.get_username())
            if n:
                record(request, "rows_hidden", f"{n:,} rows of {ds.label} hidden", target_type="dataset", target_id=ds.ref,
                       detail={"records": ids[:200], "count": n})
    except (editing.EditProblem, DatabaseError) as e:
        return _edit_problem(e)
    editing.bump_counts(ds)
    return JsonResponse({"ok": True, "hidden": n})


@require_POST
@menu_required("datasets")
def unhide_row(request, ref):
    ds = _hider(request, ref)
    record_id = request.POST.get("record", "")
    with transaction.atomic(), connection.cursor() as cur:
        if edits.unhide(cur, ds.hide_target.table, record_id):
            record(request, "rows_unhidden", f"A row of {ds.label} shown again", target_type="dataset", target_id=ds.ref, detail={"records": [record_id]})
    editing.bump_counts(ds)
    return _changes_response(request, ds)


def _labels(ds: Dataset) -> dict[str, str]:
    return {f.column: f.label for f in ds.fields.filter(json_key=None)}


def _show(value) -> str:
    return "(empty)" if value in (None, "") else str(value)


def _history_context(request, ds: Dataset, record_id: str) -> dict:
    target = ds.edit_target
    labels = _labels(ds)
    with connection.cursor() as cur:
        if not edits.available(cur):
            raise editing.EditProblem("Editing isn't set up on this database yet. Run: uv run python -m warehouse migrate", 503)
        entries = edits.history_of(cur, target.table, record_id)
        corrections = edits.corrections_of(cur, target.table, record_id)
        current = {}
        for column in edits.protected_columns(target):
            current[column] = editing._current_plain(cur, target, column, record_id)
    for e in entries:
        e.update(label=labels.get(e["field"], e["field"]), old_text=_show(e["old"]), new_text=_show(e["new"]),
                 can_restore=e["field"] in target.columns and e["origin"] != "linked" and not edits.same(current.get(e["field"]), e["old"]))
    fields = [{"field": c, "label": labels.get(c, c), "now": _show(current.get(c)), "source": _show(corrections[c]["source"]),
               "by": corrections[c].get("by"), "pending": _show(corrections[c]["pending"]) if corrections[c]["pending"] is not None else None}
              for c in target.columns if c in corrections]
    return {"ds": ds, "record": record_id, "entries": entries, "corrected": fields, "may_edit": rights.can_edit(request.user, ds)}


@menu_required("datasets")
def history(request, ref):
    """One record's panel: what's corrected, what waits for review, and every change, each with a way back."""
    ds = _dataset(request, ref)
    if ds.edit_target is None:
        raise Http404("This dataset has no history")
    try:
        context = _history_context(request, ds, request.GET.get("id", ""))
    except editing.EditProblem as e:
        return HttpResponse(str(e), status=e.status)
    return render(request, "explore/_history.html", context)


def _panel_after_change(request, ds: Dataset, record_id: str, changes: dict):
    """The panel again, and the grid updates those cells without reloading the list."""
    context = _history_context(request, ds, record_id)
    response = render(request, "explore/_history.html", context)
    response["HX-Trigger"] = json.dumps({"cellsChanged": {"id": record_id, "changes": changes,
                                                          "edited": [f["field"] for f in context["corrected"]]}})
    return response


@require_POST
@menu_required("datasets")
def restore(request, ref):
    """Puts back the value a change replaced. A new change in the history: nothing is rewritten."""
    ds = _editable(request, ref)
    record_id = request.POST.get("record", "")
    try:
        entry_id = int(request.POST.get("entry", ""))
    except ValueError:
        raise Http404("No such change") from None
    target = ds.edit_target
    try:
        with transaction.atomic(), connection.cursor() as cur:
            entry = edits.history_entry(cur, target.table, record_id, entry_id)
            if entry is None:
                raise Http404("No such change")
            f = editing.editable_field(ds, entry["field"])
            done = editing.save_cell(cur, ds, f, record_id, entry["old"], request.user.get_username(), origin="restore",
                                     note=f"Back to the value before change {entry_id}")
            if done["changed"]:
                record(request, "edit_restored", f"{f.label} of a row in {ds.label} put back", target_type="dataset", target_id=ds.ref,
                       detail={"record": record_id, "field": f.path, "value": done["value"], "from_change": entry_id})
    except editing.EditProblem as e:
        return HttpResponse(str(e), status=e.status)
    editing.bump_counts(ds)
    return _panel_after_change(request, ds, record_id, {f.path: done["value"], **done["linked"]})


@require_POST
@menu_required("datasets")
def review(request, ref):
    """Rule C: the source said something new and a person decides. Accept: its value is saved. Keep: theirs stays."""
    ds = _editable(request, ref)
    record_id, column, choice = request.POST.get("record", ""), request.POST.get("field", ""), request.POST.get("choice", "")
    target = ds.edit_target
    changes = {}
    try:
        f = editing.editable_field(ds, column)
        with transaction.atomic(), connection.cursor() as cur:
            pending = edits.corrections_of(cur, target.table, record_id).get(f.column, {}).get("pending")
            if pending is None:
                raise editing.EditProblem("That was already decided.", 409)
            if choice == "accept":
                done = editing.save_cell(cur, ds, f, record_id, pending, request.user.get_username(), origin="review", note="The source's new value was accepted")
                changes = {f.path: done["value"], **done["linked"]}
            elif choice == "keep":
                edits.clear_pending(cur, target.table, record_id, f.column, pending)
                edits.add_history(cur, target.table, record_id, f.column, pending, None, request.user.get_username(), "review", "The correction was kept; the source's new value was declined")
            else:
                raise editing.EditProblem("Choose accept or keep.")
            record(request, "edit_reviewed", f"Flagged {f.label} of a row in {ds.label}: {choice}", target_type="dataset", target_id=ds.ref,
                   detail={"record": record_id, "field": f.path, "choice": choice, "source_value": pending})
    except editing.EditProblem as e:
        return HttpResponse(str(e), status=e.status)
    return _panel_after_change(request, ds, record_id, changes)


def _changes_context(request, ds: Dataset) -> dict:
    target = ds.hide_target
    labels = _labels(ds)
    with connection.cursor() as cur:
        ready = target is not None and edits.available(cur)
        entries = edits.history_of(cur, target.table) if ready else []
        hidden = edits.hidden_list(cur, target.table) if ready else []
    for e in entries:
        e.update(label=labels.get(e["field"], e["field"]), old_text=_show(e["old"]), new_text=_show(e["new"]))
    return {"ds": ds, "entries": entries, "hidden": hidden, "may_hide": rights.can_hide(request.user, ds)}


def _changes_response(request, ds: Dataset):
    return render(request, "explore/_hidden.html" if request.htmx else "explore/changes.html", _changes_context(request, ds))


@menu_required("datasets")
def changes(request, ref):
    """Everything people changed in this dataset, newest first, and what they hid."""
    ds = _dataset(request, ref)
    if ds.hide_target is None:
        raise Http404("This dataset has no changes")
    return _changes_response(request, ds)
