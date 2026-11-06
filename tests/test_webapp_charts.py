"""The web app, phase 6 (docs/user-platform-plan.md): charts, dashboards, PDF reports and their schedule. Plan's test: a user builds a
dashboard, and a weekly PDF arrives in output/reports/."""

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone as tz
from io import StringIO
from pathlib import Path

import pytest
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.urls import reverse

from warehouse import postgres
from webapp.audit.models import AuditEvent
from webapp.catalog.models import Dataset, DatasetRight, Field
from webapp.catalog.scan import scan
from webapp.charts.data import ChartProblem, chart_data, show_number, table_rows
from webapp.charts.figures import figure
from webapp.charts.models import Chart, Dashboard
from webapp.charts.svg import chart_svg
from webapp.reports import making
from webapp.reports.models import Report
from webapp.userdata import fields as tools

pytestmark = pytest.mark.django_db
COMPANIES = "companies.companies"


def make_user(name, *roles):
    u = User.objects.create_user(name, password="correct-horse-battery-9")
    u.groups.set(Group.objects.filter(name__in=roles))
    return u


def company(id_, name="N", country="LK", website="https://a.lk", industry="Retail"):
    return {"id": id_, "source": "cse", "source_id": id_, "name": name, "country": country, "website": website, "domain": "a.lk", "industry": industry, "extra": {}}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Real tables through the real stores, on the test's connection, with PDFs going to a temp folder."""
    from companies.store import CompanyStore
    from vehicles.store import VehicleStore
    monkeypatch.setenv("SCRAPERS_OUTPUT_DIR", str(tmp_path))
    connection.ensure_connection()
    con = connection.connection
    with connection.cursor() as cur:
        cur.execute("create schema if not exists companies; create schema if not exists vehicles")
    postgres.migrate(con, ("companies", "vehicles"))
    c, v = CompanyStore.__new__(CompanyStore), VehicleStore.__new__(VehicleStore)
    c.con = v.con = con
    rows = [company("a", "Acme", "LK"), company("b", "Beta", "LK", None, "Banks"), company("c", "Gamma", "US", industry="Banks"), company("d", "Delta", None, None, None)]
    c.upsert_companies(rows, "2026-11-01T20:00:00Z")                 # 01:30 on the 2nd in Sri Lanka
    c.upsert_companies([company("e", "Epsilon", "GB")], "2026-11-15T00:00:00Z")
    ads = [{"title": "Toyota", "url": "u1", "price_lkr": 100, "location": "Colombo"}, {"title": "Honda", "url": "u2", "price_lkr": 300, "location": "Kandy"}]
    v.save_report({"site": "ikman", "success": True, "ads": ads}, "2026-11-01T00:00:00Z")
    v.save_report({"site": "riyasewana", "success": True, "ads": [{"title": "Suzuki", "url": "u3", "price_lkr": 50, "location": "Colombo"}]}, "2026-11-01T00:00:00Z")
    scan()
    return tmp_path


def ds_of(ref=COMPANIES):
    schema, name = ref.split(".")
    return Dataset.objects.get(schema=schema, name=name)


def path(label, ref=COMPANIES):
    return ds_of(ref).fields.get(label=label, missing=False).path


def chart_of(user, group=None, ref=COMPANIES, **kw):
    return Chart(owner=user, name="C", dataset=ds_of(ref), group_field=path(group, ref) if group else "", **kw)


@pytest.fixture
def ada(world):
    return make_user("ada", "Administrator")


def values(data):
    return dict(zip(data.categories, data.series[0]["values"]))


# ── the numbers ──────────────────────────────────────────────────────────────────────────────────────────────
def test_count_per_group_biggest_first_with_empty_named(ada):
    data = chart_data(chart_of(ada, "Country"), ada)
    assert data.categories == ["LK", "(empty)", "GB", "US"] and values(data) == {"LK": 2, "(empty)": 1, "GB": 1, "US": 1}
    assert data.group_label == "Country" and data.measure_label == "Rows"


def test_sum_average_smallest_largest(ada):
    ref = "vehicles.v_vehicle_ads_latest"
    price = path("Price LKR", ref)
    for measure, ikman, riya in (("sum", 400, 50), ("avg", 200, 50), ("min", 100, 50), ("max", 300, 50)):
        data = chart_data(chart_of(ada, "Site", ref, measure=measure, measure_field=price), ada)
        assert values(data) == {"ikman": ikman, "riyasewana": riya}, measure
        assert data.measure_label.endswith("Price LKR")


def test_dates_group_in_local_time(ada):
    day = chart_data(chart_of(ada, "First seen", kind="line", time_unit="day"), ada)
    assert day.is_time and day.categories == ["2026-11-02", "2026-11-15"] and values(day) == {"2026-11-02": 4, "2026-11-15": 1}
    month = chart_data(chart_of(ada, "First seen", kind="line"), ada)
    assert month.time_unit == "month" and month.categories == ["2026-11-01"] and values(month) == {"2026-11-01": 5}


def test_split_makes_a_series_each_and_keeps_colours_by_name(ada):
    data = chart_data(chart_of(ada, "Industry", split_field=path("Country")), ada)
    names = [s["name"] for s in data.series]
    assert names == sorted(names) and [s["slot"] for s in data.series] == list(range(len(names)))
    banks = data.categories.index("Banks")
    assert {s["name"]: s["values"][banks] for s in data.series if s["values"][banks]} == {"LK": 1, "US": 1}


def test_the_rest_go_into_other_when_they_can(ada):
    data = chart_data(chart_of(ada, "Name", limit=2), ada)
    assert len(data.categories) == 3 and data.categories[-1] == "Other" and sum(data.series[0]["values"]) == 5 and "in Other" in data.note
    ref = "vehicles.v_vehicle_ads_latest"
    avg = chart_data(chart_of(ada, "Title", ref, measure="avg", measure_field=path("Price LKR", ref), limit=1), ada)
    assert avg.categories == ["Honda"] and "not shown" in avg.note                   # an average can't be added into Other


def test_series_stop_at_eight_colours(ada, world):
    from companies.store import CompanyStore
    c = CompanyStore.__new__(CompanyStore)
    c.con = connection.connection
    c.upsert_companies([company(f"x{i}", f"X{i}", f"A{i}") for i in range(9)])
    data = chart_data(chart_of(ada, "Source", split_field=path("Country")), ada)
    assert len(data.series) == 8 and data.series[-1]["name"] == "Other" and data.series[-1]["slot"] == 7


def test_one_number_filters_and_hidden_rows(ada):
    assert chart_data(chart_of(ada, kind=Chart.NUMBER), ada).number == 5
    ch = chart_of(ada, kind=Chart.NUMBER, filters=[{"field": path("Country"), "value": ["LK", "GB"]}])
    assert chart_data(ch, ada).number == 3
    assert chart_data(chart_of(ada, kind=Chart.NUMBER, filters=[{"field": path("Name"), "value": "ACME"}]), ada).number == 1
    with connection.cursor() as cur:
        from warehouse import edits
        edits.hide(cur, COMPANIES, "a", "Acme", "ada")
    assert chart_data(chart_of(ada, kind=Chart.NUMBER), ada).number == 4
    assert chart_data(chart_of(ada, kind=Chart.NUMBER, ref="companies.v_companies"), ada).number == 4


def test_own_fields_lists_and_rollups_can_be_charted(ada):
    ads = ds_of("vehicles.v_vehicle_ads_latest")
    dealers = tools.create_table("Dealers", ada)
    link = tools.add_link(ads, "Dealer", dealers, ada)
    reverse_side = dealers.fields.get(link_field=link)
    count = tools.add_rollup(dealers, "Ad count", reverse_side.pk, "count", None, ada)
    tags = tools.add_own_field(dealers, "Tags", Field.MULTI, ada)
    from webapp.userdata import store
    with connection.cursor() as cur:
        a = store.add_row(cur, dealers, "ada", {dealers.fields.get(label="Name").json_key: "Auto", tags.json_key: ["x", "y"]})
        b = store.add_row(cur, dealers, "ada", {dealers.fields.get(label="Name").json_key: "Beta", tags.json_key: ["x"]})
        store.set_links(cur, ads, link, "u1", [str(a)], "ada")
        store.set_links(cur, ads, link, "u2", [str(a)], "ada")
        store.set_links(cur, ads, link, "u3", [str(b)], "ada")
    by_name = chart_data(Chart(owner=ada, name="C", dataset=dealers, group_field=dealers.fields.get(label="Name").path, measure="sum", measure_field=count.path), ada)
    assert values(by_name) == {"Auto": 2, "Beta": 1}
    by_tag = chart_data(Chart(owner=ada, name="C", dataset=dealers, group_field=tags.path), ada)
    assert values(by_tag) == {"x": 2, "y": 1}                                        # a list counts once per item
    by_dealer = chart_data(Chart(owner=ada, name="C", dataset=ads, group_field=link.path), ada)
    assert values(by_dealer) == {"Auto": 2, "Beta": 1}


@pytest.mark.parametrize("change,problem", [
    ({"group_field": "nonsense"}, "isn't there any more"), ({"group_field": ""}, "Choose what to group by"), ({"measure": "sum"}, "number field"),
    ({"measure": "drop"}, "Choose what to work out"), ({"filters": [{"field": "nonsense", "value": "x"}]}, "no field"), ({"split_field": "gone"}, "splits by"),
])
def test_what_cant_be_drawn_says_why(ada, change, problem):
    ch = chart_of(ada, "Country")
    for k, v in change.items():
        setattr(ch, k, v)
    with pytest.raises(ChartProblem, match=problem):
        chart_data(ch, ada)
    with pytest.raises(ChartProblem, match="isn't a number"):
        chart_data(chart_of(ada, "Country", measure="sum", measure_field=path("Name")), ada)
    with pytest.raises(ChartProblem, match="one measure"):
        chart_data(chart_of(ada, "Country", kind=Chart.PIE, split_field=path("Name")), ada)


def test_a_chart_follows_the_viewers_rights(ada):
    ch = chart_of(ada, "Country")
    outsider = make_user("otto")
    with pytest.raises(ChartProblem, match="right to see"):
        chart_data(ch, outsider)
    private = tools.create_table("Secret", ada)
    link = tools.add_link(ds_of("vehicles.v_vehicle_ads_latest"), "Secret link", private, ada)
    viewer = make_user("vic", "Viewer")
    with pytest.raises(ChartProblem, match="isn't there any more"):
        chart_data(Chart(owner=ada, name="C", dataset=ds_of("vehicles.v_vehicle_ads_latest"), group_field=link.path), viewer)


def test_odd_filter_text_stays_a_value(ada):
    bad = "x'; drop table companies.companies; --"
    assert chart_data(chart_of(ada, kind=Chart.NUMBER, filters=[{"field": path("Name"), "value": bad}]), ada).number == 0
    assert chart_data(chart_of(ada, kind=Chart.NUMBER), ada).number == 5


# ── drawing ──────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kind", [Chart.COLUMN, Chart.BAR, Chart.LINE, Chart.AREA, Chart.PIE])
def test_figures_and_pictures_for_every_kind(ada, kind):
    data = chart_data(chart_of(ada, "Country", kind=kind), ada)
    fig = figure(data)
    assert fig["data"] and json.dumps(fig)
    assert {"bar": "bar", "column": "bar", "line": "scatter", "area": "scatter", "pie": "pie"}[kind] == fig["data"][0]["type"]
    root = ET.fromstring(chart_svg(data))                                            # well-formed
    assert root.tag.endswith("svg") and any(t.text == "LK" for t in root.iter() if t.tag.endswith("text"))


def test_pictures_escape_text_and_cope_with_edges(ada, world):
    from companies.store import CompanyStore
    c = CompanyStore.__new__(CompanyStore)
    c.con = connection.connection
    c.upsert_companies([company("z", "Z", "LK", industry='<script>alert("x")</script> & co')])
    for kind in (Chart.COLUMN, Chart.BAR, Chart.PIE):
        svg = chart_svg(chart_data(chart_of(ada, "Industry", kind=kind), ada))
        assert "<script" not in svg
        ET.fromstring(svg)
    one = chart_of(ada, "Source", kind=Chart.PIE)
    assert "<circle" in chart_svg(chart_data(one, ada))                              # a single slice is the whole ring
    nothing = chart_data(chart_of(ada, "Country", filters=[{"field": path("Name"), "value": "zzz"}]), ada)
    assert chart_svg(nothing) == "" and "nothing to draw" in figure(nothing)["empty"]
    single = chart_of(ada, "First seen", kind=Chart.LINE, filters=[{"field": path("Name"), "value": "Epsilon"}], time_unit="day")
    ET.fromstring(chart_svg(chart_data(single, ada)))


def test_a_table_and_how_numbers_print(ada):
    heads, rows = table_rows(chart_data(chart_of(ada, "Country", kind=Chart.TABLE), ada))
    assert heads == ["Country", "Rows"] and rows[0] == ["LK", 2]
    assert show_number(1234567.0) == "1,234,567" and show_number(2.5) == "2.50" and show_number(None) == "–"


# ── the builder ──────────────────────────────────────────────────────────────────────────────────────────────
def form(ref=COMPANIES, **extra):
    return {"name": "By country", "dataset": ref, "kind": "column", "group_field": path("Country", ref), "measure": "count", "limit": "12", **extra}


def test_build_preview_save_and_change(client, ada):
    client.force_login(ada)
    assert "New chart" in client.get(reverse("chart_new")).content.decode()
    preview = client.get(reverse("chart_preview"), form(name=""))
    assert preview.status_code == 200 and b'class="plot"' in preview.content and not Chart.objects.exists()
    assert b"Choose a dataset" in client.get(reverse("chart_preview"), {"dataset": ""}).content
    assert b"Choose what to group by" in client.get(reverse("chart_preview"), form(group_field="")).content
    res = client.post(reverse("chart_new"), form())
    ch = Chart.objects.get()
    assert res.status_code == 302 and res.url == reverse("chart", args=[ch.pk]) and ch.owner == ada and ch.dataset == ds_of()
    client.post(reverse("chart", args=[ch.pk]), form(name="Renamed", kind="bar", **{"filter_field_0": path("Country"), "filter_value_0": "LK, GB"}))
    ch.refresh_from_db()
    assert ch.name == "Renamed" and ch.kind == "bar" and ch.filters == [{"field": path("Country"), "value": ["LK", "GB"]}]
    page = client.get(reverse("chart", args=[ch.pk])).content.decode()
    assert "Renamed" in page and "LK, GB" in page
    assert AuditEvent.objects.filter(action="chart_saved").count() == 2


def test_a_chart_that_cant_be_drawn_isnt_kept(client, ada):
    client.force_login(ada)
    res = client.post(reverse("chart_new"), form(group_field=""))
    assert res.status_code == 200 and "Choose what to group by" in res.content.decode() and not Chart.objects.exists()
    assert "Give the chart a name" in client.post(reverse("chart_new"), form(name=" ")).content.decode()


def test_the_lists_follow_the_dataset(client, ada):
    client.force_login(ada)
    page = client.get(reverse("chart_fields"), {"dataset": "vehicles.v_vehicle_ads_latest"}).content.decode()
    assert "Price LKR" in page and "Industry" not in page
    assert b"depends on the dataset" in client.get(reverse("chart_fields"), {"dataset": "x.y"}).content


def test_opened_from_a_grid_brings_its_filters(client, ada):
    client.force_login(ada)
    filters = json.dumps([{"field": path("Country"), "value": ["LK"]}, {"field": "nonsense", "value": "x"}])
    page = client.get(reverse("chart_new"), {"dataset": COMPANIES, "filters": filters})
    assert page.context["chart"].filters == [{"field": path("Country"), "value": ["LK"]}]
    assert client.get(reverse("dataset", args=[COMPANIES])).context["config"]["canChart"] is True


def test_rights_to_charts(client, ada):
    client.force_login(make_user("vic", "Viewer"))
    assert client.get(reverse("charts")).status_code == 403
    assert client.get(reverse("reports")).status_code == 403
    assert client.get(reverse("dataset", args=[COMPANIES])).context["config"]["canChart"] is False
    client.force_login(ada)
    client.post(reverse("chart_new"), form())
    ch = Chart.objects.get()
    ana = make_user("ana", "Analyst")
    client.force_login(ana)
    assert client.get(reverse("chart", args=[ch.pk])).status_code == 404                # private
    assert client.get(reverse("chart_tile", args=[ch.pk])).status_code == 404
    Chart.objects.filter(pk=ch.pk).update(shared=True)
    assert client.get(reverse("chart", args=[ch.pk])).status_code == 200
    assert client.post(reverse("chart", args=[ch.pk]), form(name="Mine now")).status_code == 403
    assert client.post(reverse("chart_delete", args=[ch.pk])).status_code == 403
    DatasetRight.objects.filter(dataset=ds_of(), group__name="Analyst").delete()
    tile = client.get(reverse("chart_tile", args=[ch.pk]))
    assert tile.status_code == 200 and b"right to see" in tile.content and b"plot" not in tile.content
    client.force_login(ada)
    assert client.post(reverse("chart_delete", args=[ch.pk])).status_code == 200 and Chart.objects.exists()          # asks first
    assert client.post(reverse("chart_delete", args=[ch.pk]), {"confirmed": "yes"}).status_code == 302 and not Chart.objects.exists()


def test_preview_cant_reach_a_dataset_you_cant_see(client, ada):
    secret = tools.create_table("Secret", ada)
    client.force_login(make_user("ana", "Analyst"))
    res = client.get(reverse("chart_preview"), {"dataset": secret.ref, "kind": "number"})
    assert b"Choose a dataset" in res.content


# ── dashboards ───────────────────────────────────────────────────────────────────────────────────────────────
def board_with_charts(client, user, n=2):
    client.force_login(user)
    client.post(reverse("dashboard_new"), {"name": "Week"})
    board = Dashboard.objects.get(name="Week")
    made = []
    for i in range(n):
        client.post(reverse("chart_new"), form(name=f"Chart {i}"))
        ch = Chart.objects.get(name=f"Chart {i}")
        client.post(reverse("dashboard_change", args=[board.pk]), {"action": "add", "chart": ch.pk})
        made.append(ch)
    return board, made


def test_arrange_a_dashboard(client, ada):
    board, (a, b) = board_with_charts(client, ada)
    url = reverse("dashboard_change", args=[board.pk])
    order = lambda: [i.chart_id for i in board.items.all()]  # noqa: E731
    assert order() == [a.pk, b.pk]
    page = client.get(reverse("dashboard_page", args=[board.pk])).content.decode()
    assert reverse("chart_tile", args=[a.pk]) in page and "Chart 1" in page
    second = board.items.get(chart=b)
    client.post(url, {"action": "up", "item": second.pk})
    assert order() == [b.pk, a.pk]
    client.post(url, {"action": "up", "item": second.pk})
    assert order() == [b.pk, a.pk]                                                       # already first
    client.post(url, {"action": "wide", "item": second.pk})
    second.refresh_from_db()
    assert second.wide
    client.post(url, {"action": "details", "name": "Weekly", "description": "d", "shared": "on"})
    board.refresh_from_db()
    assert board.name == "Weekly" and board.shared
    assert "Full width" in client.get(reverse("dashboard_page", args=[board.pk]), {"arrange": 1}).content.decode()
    client.post(url, {"action": "remove", "item": second.pk})
    assert order() == [a.pk] and Chart.objects.filter(pk=b.pk).exists()                  # the chart stays
    assert client.post(url, {"action": "nonsense", "item": board.items.first().pk}).status_code == 404
    assert client.post(url, {"action": "add", "chart": "99999"}).status_code == 404
    client.post(reverse("dashboard_delete", args=[board.pk]))
    assert Dashboard.objects.exists()                                                    # asks first
    client.post(reverse("dashboard_delete", args=[board.pk]), {"confirmed": "yes"})
    assert not Dashboard.objects.exists() and Chart.objects.count() == 2


def test_a_shared_dashboard_shows_each_person_what_they_may_see(client, ada):
    board, (a, _) = board_with_charts(client, ada)
    ana = make_user("ana", "Analyst")
    client.force_login(ana)
    assert client.get(reverse("dashboard_page", args=[board.pk])).status_code == 404
    Dashboard.objects.filter(pk=board.pk).update(shared=True)
    Chart.objects.update(shared=True)
    assert client.get(reverse("dashboard_page", args=[board.pk])).status_code == 200
    assert client.post(reverse("dashboard_change", args=[board.pk]), {"action": "remove"}).status_code == 403
    assert client.post(reverse("dashboard_delete", args=[board.pk])).status_code == 403
    assert "Arrange" not in client.get(reverse("dashboard_page", args=[board.pk])).content.decode()
    assert b"plot" in client.get(reverse("chart_tile", args=[a.pk])).content


# ── reports ──────────────────────────────────────────────────────────────────────────────────────────────────
def report_for(client, user, **extra):
    board, _ = board_with_charts(client, user)
    res = client.post(reverse("reports"), {"name": "Weekly report", "dashboard": board.pk, "intro": "First para.\n\nSecond <b>para</b>.", "include_tables": "on", "schedule": "weekly", "weekday": "0", **extra})
    assert res.status_code == 302, res.content.decode()[:300]
    return Report.objects.get(name="Weekly report")


def test_the_plans_test_a_dashboard_becomes_a_weekly_pdf(client, ada, world):
    report = report_for(client, ada)
    out = StringIO()
    call_command("run_reports", stdout=out)
    pdf = list((Path(world) / "reports").glob("weekly-report-*.pdf"))
    assert len(pdf) == 1 and pdf[0].read_bytes().startswith(b"%PDF") and pdf[0].stat().st_size > 1000
    assert "Weekly report" in out.getvalue() and str(pdf[0]) in out.getvalue()
    run = report.runs.get()
    assert run.scheduled and run.made_by == ada and run.filename == pdf[0].name and not run.problem
    report.refresh_from_db()
    assert report.last_scheduled is not None
    out = StringIO()
    call_command("run_reports", stdout=out)
    assert "No report is due" in out.getvalue() and report.runs.count() == 1
    assert AuditEvent.objects.filter(action="report_made", username="ada").count() == 1
    assert not list((Path(world) / "reports").glob(".*"))                               # no half-written file left


def test_preview_and_the_document(client, ada):
    report = report_for(client, ada)
    page = client.get(reverse("report_preview", args=[report.pk])).content.decode()
    assert "First para." in page and "Second &lt;b&gt;para&lt;/b&gt;." in page and "<svg" in page and "Chart 0" in page and "LK" in page
    html = making.render_html(report, ada)
    assert html.startswith("<!doctype html>") and "@page" in html and "<svg" in html
    Report.objects.filter(pk=report.pk).update(include_tables=False)
    report.refresh_from_db()
    assert "r-table" not in making.render_html(report, ada).replace(".r-table", "")


def test_make_now_download_and_who_may_have_the_file(client, ada, world):
    report = report_for(client, ada, shared="on")
    Dashboard.objects.update(shared=True)
    res = client.post(reverse("report_make", args=[report.pk]))
    assert res.status_code == 302
    run = report.runs.get()
    got = client.get(reverse("report_download", args=[report.pk, run.pk]))
    assert got.status_code == 200 and got["Content-Type"] == "application/pdf" and "attachment" in got["Content-Disposition"]
    assert b"".join(got.streaming_content).startswith(b"%PDF")
    assert run.filename in client.get(reverse("report", args=[report.pk])).content.decode()
    ana = make_user("ana", "Analyst")
    client.force_login(ana)
    assert client.get(reverse("report_download", args=[report.pk, run.pk])).status_code == 200    # may see all its data
    DatasetRight.objects.filter(dataset=ds_of(), group__name="Analyst").delete()
    assert client.get(reverse("report_download", args=[report.pk, run.pk])).status_code == 403
    page = client.get(reverse("report", args=[report.pk])).content.decode()
    assert "Holds data your roles" in page and run.filename not in page
    preview = client.get(reverse("report_preview", args=[report.pk])).content.decode()
    assert "right to see" in preview and "<svg" not in preview
    assert client.post(reverse("report", args=[report.pk]), {"name": "x"}).status_code == 403
    assert client.post(reverse("report_delete", args=[report.pk])).status_code == 403
    Report.objects.filter(pk=report.pk).update(shared=False)
    assert client.get(reverse("report", args=[report.pk])).status_code == 404
    assert client.get(reverse("report_download", args=[report.pk, run.pk])).status_code == 404


def test_a_file_that_is_gone_and_a_run_that_failed(client, ada, world, monkeypatch):
    report = report_for(client, ada)
    client.post(reverse("report_make", args=[report.pk]))
    run = report.runs.get()
    (Path(world) / "reports" / run.filename).unlink()
    assert client.get(reverse("report_download", args=[report.pk, run.pk])).status_code == 404

    def broken(html, path):
        raise making.ReportProblem("brew install pango")
    monkeypatch.setattr(making, "write_pdf", broken)
    res = client.post(reverse("report_make", args=[report.pk]), follow=True)
    assert "brew install pango" in res.content.decode()
    assert AuditEvent.objects.filter(action="report_failed").exists()
    assert client.get(reverse("report_download", args=[report.pk, report.runs.first().pk])).status_code == 404
    with pytest.raises(CommandError, match="pango"):
        call_command("run_reports", stdout=StringIO())
    report.refresh_from_db()
    assert report.last_scheduled is None                                                # it tries again next time


def at(y, m, d, h=8):
    return datetime(y, m, d, h, tzinfo=tz.utc)


@pytest.mark.parametrize("schedule,weekday,last,now,due", [
    ("", 0, None, at(2026, 11, 2), False),
    ("weekly", 0, None, at(2026, 11, 4), True),                      # the first one straight away
    ("weekly", 0, at(2026, 11, 2), at(2026, 11, 2, 10), False),       # same day
    ("weekly", 0, at(2026, 11, 2), at(2026, 11, 6), False),           # Friday
    ("weekly", 0, at(2026, 11, 2), at(2026, 11, 9), True),            # the next Monday
    ("weekly", 0, at(2026, 11, 2), at(2026, 11, 11), True),           # missed: at the next chance
    ("weekly", 0, at(2026, 11, 11), at(2026, 11, 16), True),          # and back on Mondays
    ("daily", 0, at(2026, 11, 2), at(2026, 11, 2, 12), False),
    ("daily", 0, at(2026, 11, 2), at(2026, 11, 3), True),
    ("monthly", 0, at(2026, 11, 2), at(2026, 11, 30), False),
    ("monthly", 0, at(2026, 11, 2), at(2026, 12, 1), True),
])
def test_when_a_report_is_due(schedule, weekday, last, now, due):
    assert making.is_due(Report(schedule=schedule, weekday=weekday, last_scheduled=last), now) is due


def test_scheduled_reports_use_their_owners_rights_and_stop_with_them(client, ada, world):
    report = report_for(client, ada)
    Report.objects.filter(pk=report.pk).update(owner=(owner := make_user("otto", "Analyst")))
    Chart.objects.update(shared=True)
    DatasetRight.objects.filter(dataset=ds_of(), group__name="Analyst").delete()
    assert len(making.run_due()) == 1
    report.refresh_from_db()
    text = making.render_html(report, report.owner)
    assert "right to see" in text and "<svg" not in text                                 # no data its owner may not see
    Report.objects.filter(pk=report.pk).update(last_scheduled=None)
    User.objects.filter(pk=owner.pk).update(is_active=False)
    assert making.run_due() == []                                                        # someone who has left


def test_a_report_can_only_print_a_dashboard_you_can_open(client, ada):
    board, _ = board_with_charts(client, ada)
    client.force_login(make_user("ana", "Analyst"))
    res = client.post(reverse("reports"), {"name": "R", "dashboard": board.pk})
    assert res.status_code == 200 and "Choose which dashboard" in res.content.decode() and not Report.objects.exists()


def test_reports_fetch_nothing():
    making._weasyprint()
    with pytest.raises(ValueError, match="don't fetch"):
        making.no_fetching().fetch("http://169.254.169.254/latest")


def test_a_pdf_is_written_whole(tmp_path):
    target = tmp_path / "x.pdf"
    making.write_pdf('<!doctype html><title>t</title><p>Hello සිංහල</p><img src="http://example.invalid/x.png">', target)
    assert target.read_bytes().startswith(b"%PDF") and not list(tmp_path.glob(".*"))


def test_the_step_is_in_the_automation():
    root = Path(__file__).resolve().parent.parent
    assert "STEP_reports" in (root / "run_all.sh").read_text() and " reports backup" in (root / "automation.conf").read_text()
