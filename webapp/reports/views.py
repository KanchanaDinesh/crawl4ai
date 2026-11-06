"""Reports: making them, seeing them on screen, the files."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from webapp.accounts.rights import menu_required
from webapp.audit.log import record
from webapp.catalog.rights import can_view, is_admin
from webapp.charts.forms import mine_or_shared
from webapp.charts.models import Dashboard
from webapp.core.confirm import ask_first

from . import sections as section_forms
from .making import make_report, report_context, reports_dir
from .models import Report, ReportSection


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
    if not report.is_newsletter:
        board = mine_or_shared(Dashboard, request.user).filter(pk=post.get("dashboard") if post.get("dashboard", "").isdigit() else None).first()
        if board is None:
            return "Choose which dashboard it prints."
        report.dashboard = board
        report.include_tables = "include_tables" in post
    report.intro = post.get("intro", "").strip()[:3000]
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
    return render(request, "reports/overview.html", {"reports": reports, "boards": mine_or_shared(Dashboard, request.user), "schedules": Report.SCHEDULES, "weekdays": list(enumerate(Report.WEEKDAYS)),
                                                     "starts": section_forms.ready_made(request.user), "datasets": section_forms.choosable_datasets(request.user)})


@require_POST
@menu_required("reports")
def new_newsletter(request):
    """From something ready-made or any one dataset, so the first PDF is one click away. Its sections can all be changed afterwards."""
    start, schedule = request.POST.get("start", ""), request.POST.get("schedule", "")
    schedule = schedule if schedule in Report.SCHEDULES else ""
    try:
        name, specs = section_forms.starting_point(start, request.user, schedule)
    except section_forms.SectionProblem as e:
        messages.error(request, str(e))
        return redirect("reports")
    report = Report(owner=request.user, kind=Report.NEWSLETTER, schedule=schedule, name=" ".join(request.POST.get("name", "").split())[:120] or name)
    with transaction.atomic():
        report.save()
        for i, spec in enumerate(specs):
            ReportSection.objects.create(report=report, order=i, **spec)
        record(request, "report_saved", f"Newsletter “{report.name}” made", target_type="report", target_id=report.pk, detail={"start": start, "schedule": schedule})
    return redirect("report", report.pk)


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
                                                   "sections": report.sections.select_related("dataset"),
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
    datasets = [s.dataset for s in report.sections.select_related("dataset")]
    if report.dashboard_id:
        datasets += [i.chart.dataset for i in report.dashboard.items.select_related("chart__dataset")]
    return all(can_view(user, ds) for ds in datasets)


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
    made = report.runs.exclude(filename="").count()
    asked = ask_first(request, f"Delete the report “{name}”?", back=reverse("report", args=[pk]),
                      detail=f"Its settings{', sections' if report.sections.exists() else ''} and list of PDFs go, and this can't be undone." + (" The PDF already made stays in output/reports/." if made == 1 else f" The {made} PDFs already made stay in output/reports/." if made else ""))
    if asked:
        return asked
    with transaction.atomic():
        record(request, "report_deleted", f"Report “{name}” deleted", target_type="report", target_id=pk)
        report.delete()
    messages.success(request, f"“{name}” deleted. PDFs already made stay in output/reports/.")
    return redirect("reports")


# ── a report's sections: a newsletter is made of them, and a report of a dashboard can have them after its charts ──
def _section_of(report: Report, section_id) -> ReportSection:
    return get_object_or_404(report.sections.select_related("dataset"), pk=section_id)


def _section_context(request, report, section, ds) -> dict:
    return {"report": report, "section": section, "ds": ds, "datasets": section_forms.choosable_datasets(request.user), **section_forms.choices(ds, request.user, section)}


def _section_form(request, report: Report, section: ReportSection | None):
    if request.method == "POST":
        try:
            filled = section_forms.read_section(request.POST, request.user, report, section)
        except section_forms.SectionProblem as e:
            messages.error(request, str(e))
        else:
            if filled.pk is None:
                filled.order = report.sections.count()
            filled.save()
            record(request, "report_saved", f"Report “{report.name}”: section “{filled.heading}” saved", target_type="report", target_id=report.pk)
            messages.success(request, f"Saved “{filled.heading}”.")
            return redirect("report", report.pk)
    ds = section.dataset if section else None
    return render(request, "reports/section.html", _section_context(request, report, section, ds))


@menu_required("reports")
def section_new(request, pk):
    report = _changing(request, pk)
    if request.method == "POST" and report.sections.count() >= section_forms.MAX_SECTIONS:
        messages.error(request, f"A report has at most {section_forms.MAX_SECTIONS} sections.")
        return redirect("report", report.pk)
    return _section_form(request, report, None)


@menu_required("reports")
def section_edit(request, pk, section_id):
    report = _changing(request, pk)
    return _section_form(request, report, _section_of(report, section_id))


@menu_required("reports")
def section_fields(request, pk):
    """The choices that depend on the dataset chosen, with good guesses filled in."""
    report = _changing(request, pk)
    ds = section_forms.dataset_named(request.user, request.GET.get("dataset"))
    section = section_forms.guessed_section(ds, request.user, report) if ds else None
    return render(request, "reports/_section_fields.html", _section_context(request, report, section, ds))


@require_POST
@menu_required("reports")
def section_change(request, pk, section_id):
    report = _changing(request, pk)
    section = _section_of(report, section_id)
    action = request.POST.get("action")
    if action == "remove":
        asked = ask_first(request, f"Remove the section “{section.heading}”?", back=reverse("report", args=[pk]), yes="Yes, remove it",
                          detail=f"It goes from “{report.name}”, and its choices are lost. The data in {section.dataset.label} isn't touched.")
        if asked:
            return asked
        record(request, "report_saved", f"Report “{report.name}”: section “{section.heading}” removed", target_type="report", target_id=report.pk)
        section.delete()
    elif action in ("up", "down"):
        section_forms.move(report, section, -1 if action == "up" else 1)
    return redirect("report", report.pk)
