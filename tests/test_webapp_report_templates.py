"""Report templates: someone sets colours, fonts, a logo and a page layout on a form, chooses that design on a report, and its PDF comes out that way."""

import base64
import io

import pytest
from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.urls import reverse
from PIL import Image

from webapp.accounts.models import Menu
from webapp.audit.models import AuditEvent
from webapp.reports import designs, making
from webapp.reports.models import Report, ReportTemplate
from webapp.reports.templatetags.report_design import css_string

pytestmark = pytest.mark.django_db
HOST = {"HTTP_HOST": "localhost"}


def make_user(name, *roles):
    u = User.objects.create_user(name, password="correct-horse-battery-9")
    u.groups.set(Group.objects.filter(name__in=roles))
    return u


@pytest.fixture(autouse=True)
def out_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPERS_OUTPUT_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def eve(client):
    user = make_user("eve", "Editor")
    client.force_login(user)
    client.user = user
    return client


def form(**extra):
    return {"name": "Company style", "accent": "#a51c30", "heading": "#111111", "text": "#222222", "muted": "#555555", "tint": "#eeeeee", "rule": "#dddddd",
            "body_font": "serif", "heading_font": "sans", "text_size": "large", "masthead": "band", "heading_style": "band", "columns": "1", "logo_place": "left", "logo_size": "large",
            "page_size": "Letter", "orientation": "landscape", "margins": "narrow", "header_text": "Oxsora · Internal", "footer_text": "Page foot", "show_page_numbers": "on", **extra}


def picture(fmt="PNG", size=(1800, 600), name="logo.png"):
    out = io.BytesIO()
    Image.new("RGB", size, "#336699").save(out, fmt)
    return SimpleUploadedFile(name, out.getvalue(), content_type=f"image/{fmt.lower()}")


def saved(client, **extra) -> ReportTemplate:
    r = client.post(reverse("report_template_new"), form(**extra), **HOST)
    assert r.status_code == 302, r.content[:400]
    return ReportTemplate.objects.get(pk=int(r.url.rstrip("/").split("/")[-1]))


# ── the menu ──
def test_whoever_has_reports_gets_the_templates_menu():
    menu = Menu.objects.get(code="report_templates")
    assert set(menu.roles.values_list("name", flat=True)) == set(Menu.objects.get(code="reports").roles.values_list("name", flat=True))
    assert "Viewer" not in menu.roles.values_list("name", flat=True)


def test_without_the_menu_the_pages_are_closed(client):
    client.force_login(make_user("vic", "Viewer"))
    assert client.get(reverse("report_templates"), **HOST).status_code == 403
    assert client.post(reverse("report_template_new"), form(), **HOST).status_code == 403


# ── making one ──
def test_make_a_template_and_every_choice_reaches_the_pdf_stylesheet(eve):
    template = saved(eve)
    assert template.owner == eve.user and template.accent == "#a51c30" and template.columns == 1 and not template.show_contents
    assert AuditEvent.objects.filter(action="report_template_saved").exists()
    report = Report.objects.create(owner=eve.user, name="Weekly", kind=Report.NEWSLETTER, template=template)
    html = making.render_html(report, eve.user)
    for expected in ("size: Letter landscape", "margin: 12mm 12mm", "#a51c30", "m-band h-band", "Georgia", "columns: 1;", "font-size: 11.55pt"):
        assert expected in html, expected
    assert "Oxsora" in html and "Page foot" in html


def test_a_name_is_needed_and_what_was_typed_stays(eve):
    r = eve.post(reverse("report_template_new"), form(name="  ", accent="#123456"), **HOST)
    page = r.content.decode()
    assert r.status_code == 200 and "Give the template a name" in page and 'value="#123456"' in page and not ReportTemplate.objects.exists()


def test_starting_designs_fill_in_the_form(eve):
    page = eve.get(reverse("report_template_new"), {"start": "newsroom"}, **HOST).content.decode()
    assert 'value="#a51c30"' in page
    assert " required" not in page.split('id="template-form"')[1]                         # htmx checks the form first: an empty required name would stop the live preview
    assert eve.get(reverse("report_template_new"), {"start": "nonsense"}, **HOST).status_code == 200
    for code in designs.PRESETS:
        design = designs.starting_values(code)
        design.full_clean(exclude=["owner", "name"])
        assert render_to_string("reports/_style.html", {"design": design})


def test_values_that_would_reach_the_stylesheet_are_checked(eve):
    bad = "red;} body{display:none} .x{"
    template = saved(eve, accent=bad, body_font=bad, masthead=bad, page_size="A4;}", columns="99")
    assert template.accent == "#0b6e8a" and template.body_font == "sans" and template.masthead == "line" and template.page_size == "A4" and template.columns == 2


def test_header_and_footer_text_cant_break_out_of_the_style_block(eve):
    template = saved(eve, header_text='"; } </style><script>alert(1)</script>', footer_text="Sales-team's report")
    html = making.render_html(Report.objects.create(owner=eve.user, name="R", kind=Report.NEWSLETTER, template=template), eve.user)
    assert "<script" not in html and "</style><script" not in html
    assert css_string("Sales-team's") == '"Sales\\2d team\\27 s"'


def test_a_report_name_with_a_dash_prints_properly_in_the_footer(eve):
    """It used to go through a JavaScript escape, which means nothing in a stylesheet: "Sales-team" printed as "Salesu002Dteam"."""
    html = making.render_html(Report.objects.create(owner=eve.user, name="Sales-team", kind=Report.NEWSLETTER), eve.user)
    assert 'content: "Sales\\2d team"' in html and "u002D" not in html


# ── a report with no template looks as it always did ──
def test_a_report_without_a_template_keeps_the_plain_design(eve):
    report = Report.objects.create(owner=eve.user, name="Old", kind=Report.NEWSLETTER)
    html = making.render_html(report, eve.user)
    for expected in ("size: A4 portrait", "margin: 18mm 16mm", "#0b6e8a", "font-size: 10.5pt", "columns: 2;", "Helvetica", "m-line h-underlined", 'counter(pages)'):
        assert expected in html, expected
    assert "<img" not in html


# ── logos ──
def test_a_logo_goes_into_the_pdf_redrawn_and_smaller(eve, out_folder):
    template = saved(eve, logo=picture())
    assert template.logo.startswith(designs.LOGO_PREFIX)
    kept = Image.open(io.BytesIO(base64.b64decode(template.logo[len(designs.LOGO_PREFIX):])))
    assert kept.format == "PNG" and kept.width <= 900 and kept.height <= 400
    report = Report.objects.create(owner=eve.user, name="With logo", kind=Report.NEWSLETTER, template=template)
    assert 'class="t-logo logo-left"' in making.render_html(report, eve.user)
    run = making.make_report(report, eve.user)
    pdf = (out_folder / "reports" / run.filename).read_bytes()
    assert not run.problem and b"/Subtype /Image" in pdf


@pytest.mark.parametrize("upload", [
    SimpleUploadedFile("x.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', content_type="image/svg+xml"),
    SimpleUploadedFile("x.png", b"not a picture at all", content_type="image/png"),
])
def test_only_real_pictures_are_taken_as_a_logo(eve, upload):
    upload.seek(0)
    r = eve.post(reverse("report_template_new"), form(logo=upload), **HOST)
    assert r.status_code == 200 and "PNG or JPEG" in r.content.decode() and not ReportTemplate.objects.exists()


def test_other_picture_kinds_and_big_files_are_refused(eve):
    r = eve.post(reverse("report_template_new"), form(logo=picture("GIF", name="x.gif")), **HOST)
    assert "PNG or JPEG" in r.content.decode()
    r = eve.post(reverse("report_template_new"), form(logo=SimpleUploadedFile("big.png", b"x" * (designs.MAX_UPLOAD + 1))), **HOST)
    assert "over 2 MB" in r.content.decode() and not ReportTemplate.objects.exists()


def test_a_logo_can_be_removed_and_kept(eve):
    template = saved(eve, logo=picture())
    url = reverse("report_template", args=[template.pk])
    assert eve.post(url, form(), **HOST).status_code == 302
    template.refresh_from_db()
    assert template.logo                                                                 # saving without a new file keeps it
    eve.post(url, form(remove_logo="on"), **HOST)
    template.refresh_from_db()
    assert not template.logo


def test_only_the_logo_is_let_through_when_a_pdf_is_made():
    fetcher = making.no_fetching()
    for address in ("http://169.254.169.254/latest", "file:///etc/passwd", "data:image/svg+xml;base64,AAAA", "data:text/html,hello"):
        with pytest.raises(ValueError, match="don't fetch"):
            fetcher.fetch(address)
    pixel = base64.b64encode(picture(size=(1, 1)).read()).decode()
    assert fetcher.fetch(designs.LOGO_PREFIX + pixel)


# ── the preview ──
def test_the_preview_shows_the_form_as_it_stands_on_made_up_content(eve):
    page = eve.get(reverse("report_template_preview"), {**form(name="Trying"), "show_contents": "on"}, **HOST).content.decode()
    assert "#a51c30" in page and "A section of articles" in page and "In this issue" in page and "Trying" in page and "Oxsora" in page
    assert not ReportTemplate.objects.exists()
    assert "In this issue" not in eve.get(reverse("report_template_preview"), form(), **HOST).content.decode()


def test_the_preview_shows_a_saved_logo_only_to_those_who_may_see_it(eve, client):
    template = saved(eve, logo=picture())
    params = {**form(), "saved": template.pk}
    assert 'class="t-logo' in eve.get(reverse("report_template_preview"), params, **HOST).content.decode()
    assert 'class="t-logo' not in eve.get(reverse("report_template_preview"), {**params, "remove_logo": "on"}, **HOST).content.decode()
    eve.force_login(make_user("ann", "Analyst"))
    assert 'class="t-logo' not in eve.get(reverse("report_template_preview"), params, **HOST).content.decode()


# ── choosing one on a report ──
def test_choose_a_design_on_a_report_and_on_a_new_newsletter(eve):
    template = saved(eve)
    r = eve.post(reverse("report_new_newsletter"), {"start": "blank", "schedule": "", "name": "Mine", "template": template.pk}, **HOST)
    assert r.status_code == 302
    report = Report.objects.get(name="Mine")
    assert report.template == template
    page = eve.get(reverse("report", args=[report.pk]), **HOST).content.decode()
    assert "Design: Company style" in page and f'<option value="{template.pk}" selected>' in page
    eve.post(reverse("report", args=[report.pk]), {"name": "Mine", "template": ""}, **HOST)
    report.refresh_from_db()
    assert report.template is None and "m-line" in making.render_html(report, eve.user)


def test_someone_elses_private_template_cant_be_chosen(eve, client):
    other = ReportTemplate.objects.create(owner=make_user("ann", "Analyst"), name="Hers, private")
    assert eve.get(reverse("report_template", args=[other.pk]), **HOST).status_code == 404
    report = Report.objects.create(owner=eve.user, name="Mine", kind=Report.NEWSLETTER)
    eve.post(reverse("report", args=[report.pk]), {"name": "Mine", "template": other.pk}, **HOST)
    report.refresh_from_db()
    assert report.template is None
    assert "Hers, private" not in eve.get(reverse("reports"), **HOST).content.decode()


def test_a_report_keeps_its_design_after_the_template_stops_being_shared(eve):
    ann = make_user("ann", "Analyst")
    theirs = ReportTemplate.objects.create(owner=ann, name="Team style", shared=True)
    report = Report.objects.create(owner=eve.user, name="Mine", kind=Report.NEWSLETTER, template=theirs)
    ReportTemplate.objects.filter(pk=theirs.pk).update(shared=False)
    url = reverse("report", args=[report.pk])
    assert "Team style" in eve.get(url, **HOST).content.decode()
    eve.post(url, {"name": "Renamed", "template": theirs.pk}, **HOST)
    report.refresh_from_db()
    assert report.name == "Renamed" and report.template_id == theirs.pk


# ── sharing, copying, deleting ──
def test_a_shared_template_can_be_used_and_copied_but_only_its_owner_changes_it(eve, client):
    ann = make_user("ann", "Analyst")
    theirs = ReportTemplate.objects.create(owner=ann, name="Team style", shared=True, accent="#112233")
    url = reverse("report_template", args=[theirs.pk])
    page = eve.get(url, **HOST).content.decode()
    assert "Make a copy" in page and "#112233" in page and "Delete this template" not in page and 'name="accent"' not in page
    assert eve.post(url, form(), **HOST).status_code == 403
    assert eve.post(reverse("report_template_delete", args=[theirs.pk]), {"confirmed": "yes"}, **HOST).status_code == 403
    assert eve.post(reverse("report_template_copy", args=[theirs.pk]), **HOST).status_code == 302
    mine = ReportTemplate.objects.get(owner=eve.user)
    assert mine.name == "Team style (copy)" and mine.accent == "#112233" and not mine.shared
    assert ReportTemplate.objects.get(pk=theirs.pk).owner == ann


def test_deleting_a_template_asks_first_and_its_reports_go_back_to_plain(eve):
    template = saved(eve)
    report = Report.objects.create(owner=eve.user, name="Monday report", kind=Report.NEWSLETTER, template=template)
    url = reverse("report_template_delete", args=[template.pk])
    page = eve.post(url, **HOST).content.decode()
    assert "Delete the template" in page and "Monday report" in page and "plain design" in page and ReportTemplate.objects.filter(pk=template.pk).exists()
    eve.post(url, {"confirmed": "yes"}, **HOST)
    report.refresh_from_db()
    assert not ReportTemplate.objects.exists() and report.template is None
    assert AuditEvent.objects.filter(action="report_template_deleted").exists()


def test_the_list_says_how_many_reports_use_each(eve):
    template = saved(eve)
    Report.objects.create(owner=eve.user, name="A", kind=Report.NEWSLETTER, template=template)
    page = eve.get(reverse("report_templates"), **HOST).content.decode()
    assert "Company style" in page and "1 report" in page and "Newsroom" in page


def test_a_chart_in_a_report_takes_the_main_colour():
    from webapp.charts.data import ChartData
    from webapp.charts.models import Chart
    from webapp.charts.svg import SERIES
    data = ChartData(kind=Chart.COLUMN, name="C", categories=["a", "b"], series=[{"name": "Rows", "values": [1, 2], "slot": 0}], measure_label="Rows")
    svg = making._chart_svg(data, ReportTemplate(accent="#a51c30"))
    assert "#a51c30" in svg and SERIES[0] not in svg
