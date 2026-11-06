"""The Report templates pages: how reports look, kept apart from what's in them, so one design can serve many reports."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from webapp.accounts.rights import menu_required
from webapp.audit.log import record
from webapp.catalog.rights import is_admin
from webapp.charts.forms import mine_or_shared
from webapp.core.confirm import ask_first

from . import customcss, designs
from .models import ReportTemplate


def _template(request, pk) -> ReportTemplate:
    return get_object_or_404(mine_or_shared(ReportTemplate, request.user).select_related("owner"), pk=pk)


def _may_change(user, template: ReportTemplate) -> bool:
    return template.owner_id == user.pk or is_admin(user)


def _changing(request, pk) -> ReportTemplate:
    template = _template(request, pk)
    if not _may_change(request.user, template):
        raise PermissionDenied("Only its owner or an administrator can change a template. Make a copy to have your own.")
    return template


def _form_context(template: ReportTemplate) -> dict:
    return {"template": template, "colours": [(name, label, getattr(template, name)) for name, label in ReportTemplate.COLOURS.items()],
            "fonts": ReportTemplate.FONT_NAMES, "text_sizes": ReportTemplate.TEXT_SIZES, "mastheads": ReportTemplate.MASTHEADS, "headings": ReportTemplate.HEADINGS,
            "columns": ReportTemplate.COLUMNS, "logo_places": ReportTemplate.LOGO_PLACES, "logo_sizes": ReportTemplate.LOGO_SIZES, "pages": ReportTemplate.PAGES,
            "orientations": ReportTemplate.ORIENTATIONS, "margins": ReportTemplate.MARGINS}


def _save_form(request, template: ReportTemplate):
    """Saves and goes to its page, or returns None with the problem shown, keeping what was typed. The form is read first, so nothing is lost if the logo is refused."""
    post = request.POST
    template.name = " ".join(post.get("name", "").split())[:80]
    designs.apply_design(post, template)
    problem = None if template.name else "Give the template a name."
    if "remove_logo" in post:
        template.logo = ""
    if request.FILES.get("logo"):
        try:
            template.logo = designs.read_logo(request.FILES["logo"])
        except designs.DesignProblem as e:
            problem = str(e)
    template.shared = "shared" in post
    try:
        customcss.clean(template.custom_css)
    except customcss.CSSProblem as e:
        problem = str(e)
    if problem:
        messages.error(request, problem)
        return None
    with transaction.atomic():
        template.save()
        record(request, "report_template_saved", f"Report template “{template.name}” saved", target_type="report_template", target_id=template.pk, detail={"shared": template.shared, "own_css": bool(template.custom_css.strip())})
    messages.success(request, "Saved. Choose it under Design on any report.")
    return redirect("report_template", template.pk)


@menu_required("report_templates")
def overview(request):
    templates = mine_or_shared(ReportTemplate, request.user).select_related("owner").annotate(used=Count("reports")).defer("logo")
    return render(request, "reports/templates.html", {"templates": templates, "presets": [(code, p["label"]) for code, p in designs.PRESETS.items()]})


@menu_required("report_templates")
def new(request):
    template = designs.starting_values(request.GET.get("start", "plain"))
    template.owner = request.user
    if request.method == "POST":
        done = _save_form(request, template)
        if done:
            return done
    return render(request, "reports/template.html", {**_form_context(template), "saved": None, "may_change": True})


@menu_required("report_templates")
def page(request, pk):
    template = _template(request, pk)
    may_change = _may_change(request.user, template)
    if request.method == "POST":
        if not may_change:
            raise PermissionDenied("Only its owner or an administrator can change a template. Make a copy to have your own.")
        done = _save_form(request, template)
        if done:
            return done
    context = {**_form_context(template), "saved": template, "may_change": may_change, "used_by": template.reports.count()}
    if not may_change:
        context.update(designs.sample_context(template))
    return render(request, "reports/template.html", context)


@menu_required("report_templates")
def preview(request):
    """What the form says now, on made-up content. The file chosen for a logo isn't sent until saving, so the one already saved is shown."""
    template = ReportTemplate(name=" ".join(request.GET.get("name", "").split())[:80])
    designs.apply_design(request.GET, template)
    saved = request.GET.get("saved", "")
    if saved.isdigit() and "remove_logo" not in request.GET:
        found = mine_or_shared(ReportTemplate, request.user).filter(pk=saved).first()
        template.logo = found.logo if found else ""
    context = designs.sample_context(template)
    try:
        customcss.clean(template.custom_css)
    except customcss.CSSProblem as e:
        context["css_problem"] = str(e)
    return render(request, "reports/_sheet.html", context)


@require_POST
@menu_required("report_templates")
def copy(request, pk):
    original = _template(request, pk)
    duplicate = ReportTemplate.objects.get(pk=original.pk)
    duplicate.pk = None
    duplicate.owner, duplicate.shared = request.user, False
    duplicate.name = f"{original.name[:73]} (copy)"
    with transaction.atomic():
        duplicate.save()
        record(request, "report_template_saved", f"Report template “{duplicate.name}” copied from “{original.name}”", target_type="report_template", target_id=duplicate.pk)
    messages.success(request, "This is your own copy. Change it as you like.")
    return redirect("report_template", duplicate.pk)


@require_POST
@menu_required("report_templates")
def delete(request, pk):
    template = _changing(request, pk)
    name = template.name
    reports = list(template.reports.values_list("name", flat=True))
    shown = ", ".join(f"“{n}”" for n in reports[:5]) + (f" and {len(reports) - 5} more" if len(reports) > 5 else "")
    asked = ask_first(request, f"Delete the template “{name}”?", back=reverse("report_template", args=[pk]),
                      detail=(f"These reports will go back to the plain design: {shown}. " if reports else "") + "This can't be undone. PDFs already made don't change.")
    if asked:
        return asked
    with transaction.atomic():
        record(request, "report_template_deleted", f"Report template “{name}” deleted", target_type="report_template", target_id=pk)
        template.delete()
    messages.success(request, f"“{name}” deleted.")
    return redirect("report_templates")
