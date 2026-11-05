"""The administrator's catalog screens: what datasets exist, their names and fields, and which role may see each."""

from django.contrib import messages
from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from webapp.accounts.rights import ADMINISTRATOR, menu_required
from webapp.audit.log import record

from .models import Dataset, DatasetRight
from .scan import describe, scan


@menu_required("admin_catalog")
def catalog(request):
    q = request.GET.get("q", "").strip()
    datasets = Dataset.objects.annotate(
        field_count=Count("fields", filter=Q(fields__missing=False), distinct=True),
        role_count=Count("rights", filter=Q(rights__can_view=True), distinct=True))
    if q:
        datasets = datasets.filter(Q(name__icontains=q) | Q(label__icontains=q) | Q(schema__icontains=q))
    last = Dataset.objects.order_by("-scanned_at").values_list("scanned_at", flat=True).first()
    template = "catalog/_rows.html" if request.htmx else "catalog/catalog.html"
    return render(request, template, {"datasets": datasets, "q": q, "last_scan": last})


@require_POST
@menu_required("admin_catalog")
def scan_now(request):
    summary = scan()
    text = describe(summary)
    record(request, "catalog_scanned", f"Catalog scan: {text}", target_type="catalog",
           detail={k: summary[k] for k in ("new", "new_fields", "missing", "errors")})
    (messages.warning if summary["errors"] else messages.success)(request, f"Scan finished: {text}.")
    return redirect("catalog")


@menu_required("admin_catalog")
def dataset_edit(request, pk):
    ds = get_object_or_404(Dataset, pk=pk)
    fields = list(ds.fields.all())
    if request.method == "POST":
        before = {"label": ds.label, "enabled": ds.enabled}
        ds.label = request.POST.get("label", ds.label).strip()[:120] or ds.label
        ds.description = request.POST.get("description", "").strip()[:300]
        ds.enabled = "enabled" in request.POST
        taken, problem = set(), None
        for f in fields:
            f.label = request.POST.get(f"label_{f.pk}", f.label).strip()[:120] or f.label
            f.description = request.POST.get(f"desc_{f.pk}", "").strip()[:300]
            f.available = f"available_{f.pk}" in request.POST
            f.shown = f.available and f"shown_{f.pk}" in request.POST
            if f.label.lower() in taken:
                problem = f"Two fields are called “{f.label}”. Names must differ, because exports and the column list use them."
            taken.add(f.label.lower())
        if problem:
            messages.error(request, problem)
        else:
            with transaction.atomic():
                ds.save()
                for f in fields:
                    f.save()
                record(request, "dataset_updated", f"Dataset {ds.ref} changed in the catalog", target_type="dataset",
                       target_id=ds.ref, detail={"before": before, "after": {"label": ds.label, "enabled": ds.enabled}})
            messages.success(request, f"Saved {ds.label}.")
            return redirect("catalog_dataset", pk=ds.pk)
    return render(request, "catalog/dataset.html", {"ds": ds, "fields": fields})


def _matrix(groups):
    rights = {(r.dataset_id, r.group_id): r for r in DatasetRight.objects.all()}
    rows = []
    for ds in Dataset.objects.filter(missing=False):
        cells = [{"group": g, "right": rights.get((ds.pk, g.pk))} for g in groups]
        rows.append({"ds": ds, "cells": cells})
    return rows


@menu_required("admin_roles")
def rights(request):
    groups = list(Group.objects.exclude(name=ADMINISTRATOR).order_by("name"))
    if request.method == "POST":
        changes = []
        with transaction.atomic():
            current = {(r.dataset_id, r.group_id): r for r in DatasetRight.objects.all()}
            for ds in Dataset.objects.filter(missing=False):
                for g in groups:
                    view = f"v_{ds.pk}_{g.pk}" in request.POST
                    export = view and f"e_{ds.pk}_{g.pk}" in request.POST
                    r = current.get((ds.pk, g.pk))
                    was = (r.can_view, r.can_export) if r else (False, False)
                    if was == (view, export):
                        continue
                    if view:
                        DatasetRight.objects.update_or_create(dataset=ds, group=g, defaults={
                            "can_view": True, "can_export": export})
                    elif r:
                        r.delete()
                    changes.append({"dataset": ds.ref, "role": g.name, "view": view, "export": export})
            if changes:
                record(request, "dataset_rights_changed", "Dataset rights changed: " + "; ".join(
                    f"{c['role']} {c['dataset']} {'view' if c['view'] else 'none'}{'+export' if c['export'] else ''}"
                    for c in changes), target_type="dataset", detail={"changes": changes})
        messages.success(request, f"Saved {len(changes)} change(s)." if changes else "Nothing changed.")
        return redirect("dataset_rights")
    return render(request, "catalog/rights.html", {"groups": groups, "rows": _matrix(groups)})
