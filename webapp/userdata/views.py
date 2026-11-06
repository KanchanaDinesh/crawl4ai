"""My tables, and people's own fields on any dataset: the pages for making them, and the endpoints the grid uses for links."""

import json

from django.contrib import messages
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, connection, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from webapp.accounts.rights import ADMINISTRATOR, menu_required
from webapp.audit.log import record
from webapp.catalog import rights
from webapp.catalog.models import Dataset, DatasetRight, Field
from webapp.explore.editing import bump_counts

from . import fields as tools
from . import imports, store

SAVE_LIMIT = 50_000


def _dataset(request, ref: str) -> Dataset:
    schema, _, name = ref.partition(".")
    ds = Dataset.objects.filter(schema=schema, name=name, missing=False).first()
    if ds is None:
        raise Http404("No such dataset")
    if not rights.can_view(request.user, ds):
        raise PermissionDenied("Your roles don't include this dataset.")
    return ds


def _may_edit(request, ds: Dataset) -> None:
    if not rights.can_edit(request.user, ds):
        raise PermissionDenied("Your roles don't include changing this dataset.")


def _refresh_count(ds: Dataset) -> None:
    with connection.cursor() as cur:
        Dataset.objects.filter(pk=ds.pk).update(row_count=store.count_rows(cur, ds))


# ── My tables ────────────────────────────────────────────────────────────────────────────────────────────────
@menu_required("my_tables")
def my_tables(request):
    found = rights.visible_datasets(request.user).filter(schema="userdata").select_related("owner").order_by("label")
    mine = [d for d in found if d.owner_id == request.user.pk]
    others = [d for d in found if d.owner_id != request.user.pk]
    return render(request, "userdata/my_tables.html", {"mine": mine, "others": others})


@require_POST
@menu_required("my_tables")
def new_table(request):
    try:
        ds = tools.create_table(request.POST.get("label"), request.user, request.POST.get("description", ""))
    except tools.FieldProblem as e:
        messages.error(request, str(e))
        return redirect("my_tables")
    record(request, "table_created", f"Table “{ds.label}” made", target_type="dataset", target_id=ds.ref)
    return redirect("dataset", ds.ref)


def _read_upload(request, into: Dataset | None = None):
    """The upload as a kept file, ready to confirm; or back with what's wrong."""
    upload = request.FILES.get("file")
    if upload is None:
        messages.error(request, "Choose a file first.")
        return None
    try:
        if upload.size > imports.MAX_BYTES:
            raise imports.ImportProblem(f"That file is over {imports.MAX_BYTES // (1024 * 1024)} MB. Split it, or filter it first.")
        headings, rows = imports.read_file(upload.name, upload.read())
    except imports.ImportProblem as e:
        messages.error(request, str(e))
        return None
    return imports.keep(request.user, upload.name, headings, rows, into)


@require_POST
@menu_required("my_tables")
def upload(request):
    token = _read_upload(request)
    return redirect("my_tables_import", token) if token else redirect("my_tables")


@require_POST
@menu_required("my_tables")
def add_from_file(request, ref):
    ds = _dataset(request, ref)
    _may_edit(request, ds)
    if not ds.is_own:
        raise Http404("Only own tables take files")
    token = _read_upload(request, ds)
    return redirect("my_tables_import", token) if token else redirect("dataset_fields", ds.ref)


def _new_table_from_file(request, kept: dict, into=None) -> tuple[Dataset, list]:
    label = request.POST.get("label") or kept["filename"].rsplit(".", 1)[0]
    ds = tools.create_table(label, request.user, f"From the file {kept['filename']}"[:300], first_field=None)
    mapping = []
    for i, heading in enumerate(kept["headings"]):
        if f"use_{i}" not in request.POST:
            continue
        kind = request.POST.get(f"type_{i}", Field.TEXT)
        mapping.append((i, tools.add_own_field(ds, request.POST.get(f"name_{i}") or heading, kind, request.user)))
    if not mapping:
        raise tools.FieldProblem("Keep at least one column.")
    return ds, mapping


def _into_table(request, kept: dict, ds: Dataset) -> list:
    own = {str(f.pk): f for f in ds.fields.filter(kind=Field.OWN, missing=False, available=True)}
    mapping = [(i, own[request.POST.get(f"into_{i}")]) for i in range(len(kept["headings"])) if request.POST.get(f"into_{i}") in own]
    if not mapping:
        raise tools.FieldProblem("Match at least one column to a field.")
    return mapping


@menu_required("my_tables")
def import_preview(request, token):
    kept = imports.fetch(token, request.user)
    if kept is None:
        messages.error(request, "That file isn't waiting any more. Choose it again.")
        return redirect("my_tables")
    into = Dataset.objects.filter(pk=kept["into"], missing=False).first() if kept.get("into") else None
    if into is not None:
        _may_edit(request, into)
    if request.method == "POST":
        try:
            with transaction.atomic(), connection.cursor() as cur:
                if into is None:
                    ds, mapping = _new_table_from_file(request, kept)
                else:
                    ds, mapping = into, _into_table(request, kept, into)
                done = imports.load_rows(cur, ds, mapping, kept["rows"], request.user.get_username())
                record(request, "rows_imported", f"{done['rows']:,} rows from {kept['filename']} into “{ds.label}”", target_type="dataset", target_id=ds.ref,
                       detail={"file": kept["filename"], **done})
        except (tools.FieldProblem, store.StoreProblem) as e:
            messages.error(request, str(e))
            return redirect("my_tables_import", token)
        imports.discard(token)
        _refresh_count(ds)
        text = f"{done['rows']:,} rows added."
        if done["bad"]:
            text += f" {done['bad']:,} values didn't fit their field and were left empty."
        if done["empty"]:
            text += f" {done['empty']:,} empty rows skipped."
        (messages.warning if done["bad"] else messages.success)(request, text)
        return redirect("dataset", ds.ref)
    columns = imports.columns(kept)
    context = {"kept": kept, "token": token, "columns": columns, "rows_count": len(kept["rows"]), "types": tools.TYPE_NAMES, "into": into,
               "label": kept["filename"].rsplit(".", 1)[0]}
    if into is not None:
        own = list(into.fields.filter(kind=Field.OWN, missing=False, available=True).order_by("order"))
        for c in columns:
            c["match"] = next((f.pk for f in own if f.label.lower() == c["heading"].lower()), None)
        context["own_fields"] = own
    return render(request, "userdata/import.html", context)


@require_POST
@menu_required("datasets")
def save_as_table(request, ref):
    """What's on screen, as a table of one's own: a copy, like an export, so it needs the right to export."""
    from webapp.accounts.rights import can_open
    from webapp.explore import query
    from webapp.explore.views import parse_request
    ds = _dataset(request, ref)
    if not rights.can_export(request.user, ds) or not can_open(request.user, "my_tables"):
        raise PermissionDenied("Your roles don't include copying this dataset.")
    try:
        name = json.loads(request.body or b"{}").get("name")
        q = parse_request(request, ds)
        rows = query.fetch_rows(ds, q["shown"], q["where"], q["params"], q["order"], 0, SAVE_LIMIT)
        with transaction.atomic(), connection.cursor() as cur:
            table = tools.create_table(name, request.user, f"A copy of {ds.label}"[:300], first_field=None)
            mapping = []
            for i, f in enumerate(q["shown"]):
                mapping.append((i, tools.add_own_field(table, f.label, imports.type_from(f), request.user)))
            flat = [[", ".join(map(str, r[f.path])) if isinstance(r[f.path], list) and imports.type_from(f) != Field.MULTI else r[f.path] for f in q["shown"]] for r in rows]
            done = imports.load_rows(cur, table, mapping, flat, request.user.get_username())
            record(request, "rows_imported", f"{done['rows']:,} rows of {ds.label} saved as “{table.label}”", target_type="dataset", target_id=table.ref, detail={"from": ds.ref, **done})
    except (tools.FieldProblem, query.QueryProblem, ValueError) as e:
        return JsonResponse({"error": str(e)}, status=400)
    except DatabaseError:
        return JsonResponse({"error": "That took too long. Narrow it down with a filter and try again."}, status=503)
    _refresh_count(table)
    return JsonResponse({"ok": True, "url": reverse("dataset", args=[table.ref])})


# ── the Fields page ──────────────────────────────────────────────────────────────────────────────────────────
def _describe(f: Field) -> str:
    if f.kind == Field.RECORDS:
        side = "Linked from" if f.is_reverse_link else "Link to"
        return f"{side} {f.link_dataset.label}" + (", one row" if f.link_single else "")
    if f.kind == Field.LOOKUP:
        return f"Lookup: {f.target_field.label} through {f.link_field.label}"
    if f.kind == Field.ROLLUP:
        what = "" if f.rollup == Field.COUNT else f" of {f.target_field.label}"
        return f"Rollup: {Field.ROLLUPS[f.rollup]}{what} through {f.link_field.label}"
    text = tools.TYPE_NAMES.get(f.type, f.type)
    if f.type in (Field.CHOICE, Field.MULTI) and f.values:
        return f"{text}: {', '.join(f.values[:8])}" + ("…" if len(f.values) > 8 else "")
    return text


def _fields_context(request, ds: Dataset) -> dict:
    mine = list(ds.fields.filter(missing=False).exclude(kind=Field.DATA).select_related("link_dataset", "link_field", "target_field").order_by("order", "id"))
    for f in mine:
        f.about = _describe(f)
    context = {"ds": ds, "mine": mine, "may_edit": rights.can_edit(request.user, ds), "types": tools.TYPE_NAMES, "kinds": tools.KIND_NAMES, "may_share": rights.can_share(request.user, ds)}
    if context["may_share"]:
        current = {r.group_id: r for r in ds.rights.all()}
        context["roles"] = [{"group": g, "right": current.get(g.pk)} for g in Group.objects.exclude(name=ADMINISTRATOR).order_by("name")]
    return context


def _link_targets(request, ds: Dataset) -> list[Dataset]:
    found = rights.visible_datasets(request.user).exclude(pk=ds.pk).order_by("schema", "label")
    return [d for d in found if d.record_key]


@menu_required("datasets")
def fields_page(request, ref):
    ds = _dataset(request, ref)
    if ds.record_key is None:
        raise Http404("The rows of this dataset have no key, so it can't have fields of its own")
    if request.method == "POST":
        _may_edit(request, ds)
        try:
            f = _add_from_form(request, ds)
        except tools.FieldProblem as e:
            messages.error(request, str(e))
        else:
            record(request, "field_added", f"Field “{f.label}” added to {ds.label}", target_type="dataset", target_id=ds.ref, detail={"field": f.path, "kind": f.kind, "type": f.type})
            messages.success(request, f"“{f.label}” added. It's in the grid now.")
            return redirect("dataset_fields", ds.ref)
    return render(request, "userdata/fields.html", _fields_context(request, ds))


def _add_from_form(request, ds: Dataset) -> Field:
    post, what = request.POST, request.POST.get("what", "")
    label = post.get("label", "")
    if what == Field.RECORDS:
        target = Dataset.objects.filter(pk=post.get("target") if post.get("target", "").isdigit() else None, missing=False).first()
        if target is None:
            raise tools.FieldProblem("Choose which table to link to.")
        return tools.add_link(ds, label, target, request.user, single="single" in post)
    if what == Field.LOOKUP:
        return tools.add_lookup(ds, label, post.get("link"), post.get("target_field"), request.user)
    if what == Field.ROLLUP:
        return tools.add_rollup(ds, label, post.get("link"), post.get("how", ""), post.get("target_field"), request.user)
    return tools.add_own_field(ds, label, what, request.user, post.get("choices", ""), allow_new="allow_new" in post or not post.get("choices_sent"))


@menu_required("datasets")
def field_options(request, ref):
    """The rest of the add-a-field form, which depends on the kind of field chosen."""
    ds = _dataset(request, ref)
    what = request.GET.get("what", "")
    context = {"ds": ds, "what": what, "is_choice": what in (Field.CHOICE, Field.MULTI)}
    if what == Field.RECORDS:
        context["targets"] = _link_targets(request, ds)
    if what in (Field.LOOKUP, Field.ROLLUP):
        links = list(ds.fields.filter(kind=Field.RECORDS, missing=False, available=True).select_related("link_dataset"))
        links = [f for f in links if not f.link_dataset.missing and rights.can_view(request.user, f.link_dataset)]
        chosen = next((f for f in links if str(f.pk) == request.GET.get("link")), links[0] if links else None)
        how = request.GET.get("how", Field.COUNT)
        targets = list(tools.lookup_targets(chosen)) if chosen else []
        context.update(links=links, chosen=chosen, how=how, rollups=Field.ROLLUPS,
                       target_fields=[f for f in targets if what == Field.LOOKUP or f.base == Field.NUMBER])
    return render(request, "userdata/_field_options.html", context)


@require_POST
@menu_required("datasets")
def field_change(request, ref, pk):
    ds = _dataset(request, ref)
    _may_edit(request, ds)
    f = ds.fields.filter(pk=pk, missing=False).exclude(kind=Field.DATA).first()
    if f is None:
        raise Http404("No such field")
    if request.POST.get("action") == "remove":
        gone = tools.remove_field(f)
        record(request, "field_removed", f"“{f.label}” removed from {ds.label}" + (f" (with {', '.join(gone[1:])})" if gone[1:] else ""), target_type="dataset", target_id=ds.ref, detail={"fields": gone})
        messages.success(request, f"“{f.label}” removed. Its values stay in the database, in case it's wanted back: ask an administrator.")
        return redirect("dataset_fields", ds.ref)
    try:
        f.label = tools._label(ds, request.POST.get("label"), keep=f)
    except tools.FieldProblem as e:
        messages.error(request, str(e))
        return redirect("dataset_fields", ds.ref)
    update = ["label"]
    if f.type in (Field.CHOICE, Field.MULTI) and f.kind == Field.OWN and "choices" in request.POST:
        wanted = tools._choices(request.POST["choices"])
        f.values = wanted
        f.allow_new = "allow_new" in request.POST
        update += ["values", "allow_new"]
    f.save(update_fields=update)
    record(request, "field_changed", f"Field “{f.label}” of {ds.label} changed", target_type="dataset", target_id=ds.ref, detail={"field": f.path})
    messages.success(request, f"Saved “{f.label}”.")
    return redirect("dataset_fields", ds.ref)


@require_POST
@menu_required("datasets")
def share(request, ref):
    ds = _dataset(request, ref)
    if not rights.can_share(request.user, ds):
        raise PermissionDenied("Only its owner or an administrator can share a table.")
    changes = []
    with transaction.atomic():
        for g in Group.objects.exclude(name=ADMINISTRATOR):
            view = f"view_{g.pk}" in request.POST
            edit = view and f"edit_{g.pk}" in request.POST
            if view:
                DatasetRight.objects.update_or_create(dataset=ds, group=g, defaults={"can_view": True, "can_export": view, "can_edit": edit, "can_hide": edit})
                changes.append(f"{g.name}: {'edit' if edit else 'see'}")
            else:
                DatasetRight.objects.filter(dataset=ds, group=g).delete()
        record(request, "table_shared", f"Table “{ds.label}” shared with: {', '.join(changes) or 'nobody'}", target_type="dataset", target_id=ds.ref)
    messages.success(request, "Saved who can use it.")
    return redirect("dataset_fields", ds.ref)


@require_POST
@menu_required("datasets")
def delete_table(request, ref):
    """Taken out of use, not erased: an administrator can bring it back from the catalog data."""
    ds = _dataset(request, ref)
    if not rights.can_share(request.user, ds):
        raise PermissionDenied("Only its owner or an administrator can delete a table.")
    if request.POST.get("confirm", "").strip().lower() != ds.label.strip().lower():
        messages.error(request, f"Type the table's name, “{ds.label}”, to delete it.")
        return redirect("dataset_fields", ds.ref)
    with transaction.atomic():
        for f in Field.objects.filter(link_dataset=ds, missing=False):
            tools.remove_field(f)
        Dataset.objects.filter(pk=ds.pk).update(missing=True)
        record(request, "table_deleted", f"Table “{ds.label}” deleted", target_type="dataset", target_id=ds.ref)
    messages.success(request, f"“{ds.label}” deleted.")
    return redirect("my_tables")


# ── what the grid asks ───────────────────────────────────────────────────────────────────────────────────────
def _link_field(request, ds: Dataset, path) -> Field:
    f = ds.fields.filter(path=str(path or ""), kind=Field.RECORDS, missing=False, available=True).select_related("link_dataset", "link_field").first()
    if f is None or f.link_dataset.missing:
        raise Http404("No such link")
    if not rights.can_view(request.user, f.link_dataset):
        raise PermissionDenied("Your roles don't include the table it links to.")
    return f


@menu_required("datasets")
def link_options(request, ref):
    f = _link_field(request, _dataset(request, ref), request.GET.get("field"))
    with connection.cursor() as cur:
        return JsonResponse({"options": store.search_rows(cur, f.link_dataset, request.GET.get("q", ""))})


@menu_required("datasets")
def link_values(request, ref):
    f = _link_field(request, _dataset(request, ref), request.GET.get("field"))
    with connection.cursor() as cur:
        return JsonResponse({"rows": store.linked_rows(cur, f, request.GET.get("id", ""))})


@require_POST
@menu_required("datasets")
def set_links(request, ref):
    ds = _dataset(request, ref)
    _may_edit(request, ds)
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "That request isn't valid."}, status=400)
    ids, to = data.get("ids"), data.get("to")
    if not isinstance(ids, list) or not ids or not isinstance(to, list) or len(ids) > 5000:
        return JsonResponse({"error": "Select between 1 and 5,000 rows."}, status=400)
    f = _link_field(request, ds, data.get("field"))
    changed, labels = 0, []
    try:
        with transaction.atomic(), connection.cursor() as cur:
            for record_id in ids:
                done = store.set_links(cur, ds, f, str(record_id), to, request.user.get_username())
                changed, labels = changed + done["changed"], done["labels"]
            if changed:
                record(request, "links_changed", f"{f.label} changed on {changed:,} rows of {ds.label}", target_type="dataset", target_id=ds.ref,
                       detail={"field": f.path, "records": [str(i) for i in ids[:200]], "to": [str(t) for t in to[:200]]})
    except store.StoreProblem as e:
        return JsonResponse({"error": str(e)}, status=e.status)
    bump_counts(ds)
    return JsonResponse({"ok": True, "changed": changed, "labels": labels})


@require_POST
@menu_required("datasets")
def add_choice(request, ref):
    """A new choice typed in a several-choices list: shared with everyone using the field."""
    ds = _dataset(request, ref)
    _may_edit(request, ds)
    try:
        data = json.loads(request.body or b"{}")
        f = ds.fields.filter(path=str(data.get("field") or ""), kind=Field.OWN, type__in=[Field.CHOICE, Field.MULTI], missing=False).first()
        if f is None:
            raise Http404("No such field")
        text = " ".join(str(data.get("value") or "").split())
        if not text or "," in text:
            return JsonResponse({"error": "A choice needs a name, and can't have a comma in it."}, status=400)
        before = len(f.values)
        value = store._choice_named(f, text)
    except json.JSONDecodeError:
        return JsonResponse({"error": "That request isn't valid."}, status=400)
    except store.StoreProblem as e:
        return JsonResponse({"error": str(e)}, status=400)
    if len(f.values) > before:
        record(request, "choice_added", f"Choice “{value}” added to {f.label} of {ds.label}", target_type="dataset", target_id=ds.ref, detail={"field": f.path, "value": value})
    return JsonResponse({"ok": True, "value": value, "choices": f.values})


@require_POST
@menu_required("datasets")
def add_row(request, ref):
    ds = _dataset(request, ref)
    _may_edit(request, ds)
    if not ds.is_own:
        raise Http404("Rows can be added to own tables only")
    with transaction.atomic(), connection.cursor() as cur:
        row = store.add_row(cur, ds, request.user.get_username())
        record(request, "row_added", f"Row added to “{ds.label}”", target_type="dataset", target_id=ds.ref, detail={"record": str(row)})
    _refresh_count(ds)
    bump_counts(ds)
    return JsonResponse({"ok": True, "row": {"_id": str(row), "id": row, "created_by": request.user.get_username()}})
