"""Reports: making them, seeing them on screen, the files."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from webapp.accounts.rights import menu_required
from webapp.audit.log import record
from webapp.catalog.rights import can_view, is_admin
from webapp.charts.forms import mine_or_shared
from webapp.charts.models import Dashboard

from .making import make_report, report_context, reports_dir
from .models import Report


def _report(request, pk) -> Report:
    return get_object_or_404(mine_or_shared(Report, request.user).select_related("dashboard", "owner"), pk=pk)


def _may_change(user, report) -> bool:
    return report.owner_id == user.pk or is_admin(user)


def _changing(request, pk) -> Report:
    report = _report(request, pk)
    if not _may_change(request.user, report):
        raise PermissionDenied("Only its owner or an administrator can change a report.")
    return report


def _read_form(request, report: Report) -> str | None:
    """Fills in the report from the form; returns what's wrong, if anything."""
    post = request.POST
    report.name = " ".join(post.get("name", "").split())[:120]
    if not report.name:
        return "Give the report a name."
    board = mine_or_shared(Dashboard, request.user).filter(pk=post.get("dashboard") if post.get("dashboard", "").isdigit() else None).first()
    if board is None:
        return "Choose which dashboard it prints."
    report.dashboard = board
    report.intro = post.get("intro", "").strip()[:3000]
    report.include_tables = "include_tables" in post
    schedule = post.get("schedule", "")
    report.schedule = schedule if schedule in Report.SCHEDULES else ""
    weekday = post.get("weekday", "0")
    report.weekday = int(weekday) if weekday.isdigit() and int(weekday) < 7 else 0
    report.shared = "shared" in post
    return None


@menu_required("reports")
def overview(request):
    if request.method == "POST":
        report = Report(owner=request.user)
        problem = _read_form(request, report)
        if problem:
            messages.error(request, problem)
        else:
            report.save()
            record(request, "report_saved", f"Report “{report.name}” made", target_type="report", target_id=report.pk)
            return redirect("report", report.pk)
    reports = mine_or_shared(Report, request.user).select_related("dashboard", "owner")
    return render(request, "reports/overview.html", {"reports": reports, "boards": mine_or_shared(Dashboard, request.user), "schedules": Report.SCHEDULES, "weekdays": list(enumerate(Report.WEEKDAYS))})


@menu_required("reports")
def report_page(request, pk):
    report = _report(request, pk)
    if request.method == "POST":
        if not _may_change(request.user, report):
            raise PermissionDenied("Only its owner or an administrator can change a report.")
        problem = _read_form(request, report)
        if problem:
            messages.error(request, problem)
        else:
            report.save()
            record(request, "report_saved", f"Report “{report.name}” changed", target_type="report", target_id=report.pk, detail={"schedule": report.schedule})
            messages.success(request, "Saved.")
            return redirect("report", report.pk)
    return render(request, "reports/report.html", {"report": report, "runs": report.runs.select_related("made_by")[:30], "may_change": _may_change(request.user, report),
                                                   "may_download": _may_download(request.user, report), "boards": mine_or_shared(Dashboard, request.user),
                                                   "schedules": Report.SCHEDULES, "weekdays": list(enumerate(Report.WEEKDAYS))})


@menu_required("reports")
def preview(request, pk):
    """On screen, with the rights of whoever is looking: a chart they may not see says so instead of showing."""
    report = _report(request, pk)
    return render(request, "reports/preview.html", report_context(report, request.user))


@require_POST
@menu_required("reports")
def make_now(request, pk):
    report = _report(request, pk)
    run = make_report(report, request.user)
    if run.problem:
        messages.error(request, f"The PDF couldn't be made: {run.problem}")
        record(request, "report_failed", f"Report “{report.name}” failed: {run.problem[:150]}", target_type="report", target_id=report.pk)
    else:
        record(request, "report_made", f"Report “{report.name}”: {run.filename}", target_type="report", target_id=report.pk, detail={"file": run.filename, "size": run.size})
        messages.success(request, "The PDF is ready, below.")
    return redirect("report", report.pk)


def _may_download(user, report: Report) -> bool:
    """A file holds what its maker could see. Someone else gets it only if they may see all the data in it too."""
    if _may_change(user, report):
        return True
    return all(can_view(user, i.chart.dataset) for i in report.dashboard.items.select_related("chart__dataset"))


@menu_required("reports")
def download(request, pk, run_id):
    report = _report(request, pk)
    run = report.runs.filter(pk=run_id).exclude(filename="").first()
    if run is None:
        raise Http404("No such file")
    made_by_me = run.made_by_id == request.user.pk
    if not made_by_me and not _may_download(request.user, report):
        raise PermissionDenied("That file holds data your roles don't include.")
    path = reports_dir() / run.filename
    if not path.is_file():
        raise Http404("That file isn't there any more")
    return FileResponse(path.open("rb"), as_attachment=True, filename=run.filename, content_type="application/pdf")


@require_POST
@menu_required("reports")
def delete_report(request, pk):
    report = _changing(request, pk)
    name = report.name
    with transaction.atomic():
        record(request, "report_deleted", f"Report “{name}” deleted", target_type="report", target_id=pk)
        report.delete()
    messages.success(request, f"“{name}” deleted. PDFs already made stay in output/reports/.")
    return redirect("reports")
