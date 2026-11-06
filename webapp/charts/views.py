"""Charts and dashboards: the builder with its live preview, tiles that load on their own, and arranging them."""

import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from webapp.accounts.rights import menu_required
from webapp.audit.log import record
from webapp.catalog.rights import is_admin

from . import forms
from .data import ChartData, ChartProblem, chart_data, show_number, table_rows
from .figures import figure
from .models import Chart, Dashboard, DashboardItem

PREVIEW_NAME = "Preview"


def _may_change(user, thing) -> bool:
    return thing.owner_id == user.pk or is_admin(user)


def _chart_context(chart: Chart, user) -> dict:
    """What the tile template draws: a figure, a number, a table, or why not."""
    try:
        data: ChartData = chart_data(chart, user)
    except ChartProblem as e:
        return {"problem": str(e), "chart": chart}
    context = {"chart": chart, "data": data, "kind": data.kind}
    if data.kind == Chart.NUMBER:
        context["number"] = show_number(data.number)
    elif data.kind == Chart.TABLE:
        heads, rows = table_rows(data)
        context.update(heads=heads, rows=[[row[0], *[show_number(v) for v in row[1:]]] for row in rows])
    else:
        context["figure"] = figure(data)
    return context


# ── the list ─────────────────────────────────────────────────────────────────────────────────────────────────
@menu_required("charts")
def overview(request):
    charts = forms.mine_or_shared(Chart, request.user).select_related("dataset", "owner").filter(dataset__missing=False)
    dashboards = forms.mine_or_shared(Dashboard, request.user).select_related("owner")
    return render(request, "charts/overview.html", {"charts": charts, "dashboards": dashboards})


# ── the builder ──────────────────────────────────────────────────────────────────────────────────────────────
def _builder_context(request, chart: Chart | None, ds, saved: Chart | None = None) -> dict:
    return {"chart": chart, "saved": saved, "datasets": forms.choosable_datasets(request.user), "ds": ds, "kinds": Chart.KINDS, "measures": Chart.MEASURES,
            "time_units": Chart.TIME_UNITS, "fields": forms.field_groups(ds, request.user) if ds else None,
            "filters": forms.filter_rows(chart)}


@menu_required("charts")
def builder(request, pk=None):
    saved = get_object_or_404(forms.mine_or_shared(Chart, request.user).select_related("dataset"), pk=pk) if pk else None
    if saved is not None and request.method == "POST" and not _may_change(request.user, saved):
        raise PermissionDenied("Only its owner or an administrator can change a chart.")
    if request.method == "POST":
        try:
            chart = forms.read_chart(request.POST, request.user, saved)
            if not chart.name:
                raise forms.FormProblem("Give the chart a name.")
            chart_data(chart, request.user)            # a chart that can't be drawn isn't kept
        except (forms.FormProblem, ChartProblem) as e:
            messages.error(request, str(e))
            chart = _unsaved(request)
            return render(request, "charts/builder.html", _builder_context(request, chart, chart.dataset if chart else None, saved))
        chart.save()
        record(request, "chart_saved", f"Chart “{chart.name}” saved", target_type="chart", target_id=chart.pk, detail={"dataset": chart.dataset.ref, "kind": chart.kind})
        messages.success(request, f"Saved “{chart.name}”.")
        return redirect("chart", chart.pk)
    if saved is not None:
        return render(request, "charts/builder.html", _builder_context(request, saved, saved.dataset, saved))
    ds = forms.dataset_named(request.user, request.GET.get("dataset"))
    chart = _from_grid(request, ds) if ds else None
    return render(request, "charts/builder.html", _builder_context(request, chart, ds))


def _unsaved(request) -> Chart | None:
    try:
        return forms.read_chart(request.POST, request.user)
    except forms.FormProblem:
        return None


def _from_grid(request, ds) -> Chart:
    """Opened from a dataset's page: its filters come along."""
    chart = Chart(owner=request.user, dataset=ds)
    try:
        wanted = json.loads(request.GET.get("filters") or "[]")
    except json.JSONDecodeError:
        wanted = []
    fields = forms.query.usable_fields(ds, request.user)
    chart.filters = [{"field": f["field"], "value": f.get("value")} for f in wanted if isinstance(f, dict) and f.get("field") in fields][:forms.FILTER_ROWS]
    return chart


@menu_required("charts")
def builder_fields(request):
    """The lists that depend on the dataset chosen."""
    ds = forms.dataset_named(request.user, request.GET.get("dataset"))
    return render(request, "charts/_builder_fields.html", _builder_context(request, None, ds))


@menu_required("charts")
def preview(request):
    """The chart as chosen so far, without saving anything."""
    try:
        chart = forms.read_chart(request.GET, request.user)
    except forms.FormProblem as e:
        return render(request, "charts/_tile_body.html", {"problem": str(e)})
    chart.name = chart.name or PREVIEW_NAME
    return render(request, "charts/_tile_body.html", _chart_context(chart, request.user))


@require_POST
@menu_required("charts")
def delete_chart(request, pk):
    chart = get_object_or_404(Chart, pk=pk)
    if not _may_change(request.user, chart):
        raise PermissionDenied("Only its owner or an administrator can delete a chart.")
    name = chart.name
    with transaction.atomic():
        record(request, "chart_deleted", f"Chart “{name}” deleted", target_type="chart", target_id=pk)
        chart.delete()
    messages.success(request, f"“{name}” deleted.")
    return redirect("charts")


@menu_required("charts")
def tile(request, pk):
    chart = get_object_or_404(forms.mine_or_shared(Chart, request.user).select_related("dataset"), pk=pk)
    return render(request, "charts/_tile_body.html", _chart_context(chart, request.user))


# ── dashboards ───────────────────────────────────────────────────────────────────────────────────────────────
@require_POST
@menu_required("charts")
def new_dashboard(request):
    name = " ".join(request.POST.get("name", "").split())[:120]
    if not name:
        messages.error(request, "Give the dashboard a name.")
        return redirect("charts")
    board = Dashboard.objects.create(owner=request.user, name=name, description=request.POST.get("description", "").strip()[:300])
    record(request, "dashboard_saved", f"Dashboard “{name}” made", target_type="dashboard", target_id=board.pk)
    return redirect("dashboard_page", board.pk)


def _board(request, pk) -> Dashboard:
    return get_object_or_404(forms.mine_or_shared(Dashboard, request.user), pk=pk)


@menu_required("charts")
def dashboard_page(request, pk):
    board = _board(request, pk)
    may_change = _may_change(request.user, board)
    placed = {i.chart_id for i in board.items.all()}
    choices = forms.mine_or_shared(Chart, request.user).exclude(pk__in=placed).filter(dataset__missing=False) if may_change else []
    return render(request, "charts/dashboard.html", {"board": board, "items": board.items.select_related("chart", "chart__dataset"), "may_change": may_change, "choices": choices,
                                                     "arranging": may_change and request.GET.get("arrange") == "1"})


def _changing(request, pk) -> Dashboard:
    board = _board(request, pk)
    if not _may_change(request.user, board):
        raise PermissionDenied("Only its owner or an administrator can change a dashboard.")
    return board


@require_POST
@menu_required("charts")
def dashboard_change(request, pk):
    """Everything that arranges a dashboard: one form per action, so the page works without any script."""
    board = _changing(request, pk)
    action = request.POST.get("action")
    item = board.items.filter(pk=request.POST.get("item") or None).first() if request.POST.get("item", "").isdigit() else None
    if action == "add":
        chart = forms.mine_or_shared(Chart, request.user).filter(pk=request.POST.get("chart") or None).first() if request.POST.get("chart", "").isdigit() else None
        if chart is None:
            raise Http404("No such chart")
        last = board.items.order_by("-order").values_list("order", flat=True).first() or 0
        DashboardItem.objects.create(dashboard=board, chart=chart, order=last + 10)
    elif action == "details":
        board.name = " ".join(request.POST.get("name", "").split())[:120] or board.name
        board.description = request.POST.get("description", "").strip()[:300]
        board.shared = "shared" in request.POST
        board.save()
    elif item is None:
        raise Http404("No such chart on this dashboard")
    elif action == "remove":
        item.delete()
    elif action == "wide":
        item.wide = not item.wide
        item.save(update_fields=["wide"])
    elif action in ("up", "down"):
        _move(board, item, -1 if action == "up" else 1)
    else:
        raise Http404("No such action")
    record(request, "dashboard_saved", f"Dashboard “{board.name}” changed", target_type="dashboard", target_id=board.pk, detail={"action": action})
    return redirect(f"{reverse('dashboard_page', args=[board.pk])}?arrange=1")


def _move(board: Dashboard, item: DashboardItem, by: int) -> None:
    items = list(board.items.all())
    at = items.index(item)
    to = at + by
    if 0 <= to < len(items):
        items[at], items[to] = items[to], items[at]
        for n, it in enumerate(items):
            DashboardItem.objects.filter(pk=it.pk).update(order=(n + 1) * 10)


@require_POST
@menu_required("charts")
def delete_dashboard(request, pk):
    board = _changing(request, pk)
    name = board.name
    with transaction.atomic():
        record(request, "dashboard_deleted", f"Dashboard “{name}” deleted", target_type="dashboard", target_id=pk)
        board.delete()
    messages.success(request, f"“{name}” deleted. Its charts are still there.")
    return redirect("charts")
