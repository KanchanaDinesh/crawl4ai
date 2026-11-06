"""Newsletter reports: choose a dataset (news, companies, ...) or something ready-made, and get a PDF of the latest rows, on demand or every day."""

import json
from datetime import datetime, timedelta, timezone as tz
from io import StringIO
from pathlib import Path

import pytest
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.urls import reverse

from warehouse import postgres
from webapp.catalog.models import Dataset, DatasetRight, Field
from webapp.catalog.scan import scan
from webapp.reports import making, newsletter, sections
from webapp.reports.models import Report, ReportSection

pytestmark = pytest.mark.django_db
NOW = datetime.now(tz.utc)


def make_user(name, *roles):
    u = User.objects.create_user(name, password="correct-horse-battery-9")
    u.groups.set(Group.objects.filter(name__in=roles))
    return u


def headline(url, title, group="LK", summary="", when=None, source="Ada Derana", topics=None):
    with connection.cursor() as cur:
        cur.execute("insert into news.news_items (url, title, summary, group_code, source, topics, first_seen_at) values (%s, %s, %s, %s, %s, %s, %s)", [url, title, summary, group, source, topics or [], when or NOW])


def company(id_, name, country="LK", when=None, website="https://a.lk"):
    with connection.cursor() as cur:
        cur.execute("insert into companies.companies (id, source, source_id, name, country, website, domain, industry, first_seen, last_seen) values (%s, 'cse', %s, %s, %s, %s, 'a.lk', 'Banks', %s, %s)", [id_, id_, name, country, website, when or NOW, when or NOW])


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPERS_OUTPUT_DIR", str(tmp_path))
    cache.clear()
    connection.ensure_connection()
    with connection.cursor() as cur:
        cur.execute("create schema if not exists companies; create schema if not exists vehicles; create schema if not exists news; create schema if not exists markets")
    postgres.migrate(connection.connection, ("companies", "vehicles", "news", "markets"))
    old = NOW - timedelta(days=3)
    for i in range(12):
        headline(f"https://n.lk/{i}", f"Colombo story {i}", summary=f"What happened in story {i}.", topics=["Politics"] if i % 2 else ["Politics", "Economy"], when=NOW - timedelta(minutes=i))
    headline("https://w.com/1", "World story", "GLOBAL", source="BBC")
    headline("https://n.lk/old", "Old story from days ago", when=old)
    company("a", "Acme Bank", when=NOW)
    company("b", "Beta Stores", "US", when=NOW)
    company("c", "Gamma Old", when=old)
    scan()
    return Path(tmp_path)


def ds(ref):
    schema, name = ref.split(".")
    return Dataset.objects.get(schema=schema, name=name)


def new_report(client, start, **extra):
    r = client.post(reverse("report_new_newsletter"), {"start": start, "schedule": "", **extra}, HTTP_HOST="localhost")
    assert r.status_code == 302, r.content[:300]
    return Report.objects.get(pk=int(r.url.rstrip("/").split("/")[-1]))


@pytest.fixture
def boss(client):
    u = make_user("boss", "Administrator")
    client.force_login(u)
    return u


# ── the request itself ──────────────────────────────────────────────
def test_daily_news_makes_a_real_pdf_in_one_go(world, client, boss):
    report = new_report(client, "news", schedule="daily")
    assert report.is_newsletter and report.name == "Daily news" and report.schedule == "daily" and report.dashboard is None
    assert [s.heading for s in report.sections.all()] == ["Today in numbers", "Sri Lanka", "World", "Business and markets", "Technology"]
    done = client.post(reverse("report_make", args=[report.pk]), HTTP_HOST="localhost")
    assert done.status_code == 302
    run = report.runs.get()
    assert not run.problem and run.size > 0
    pdf = world / "reports" / run.filename
    assert pdf.read_bytes().startswith(b"%PDF")
    download = client.get(reverse("report_download", args=[report.pk, run.pk]), HTTP_HOST="localhost")
    assert download.status_code == 200 and download["Content-Type"] == "application/pdf"


def test_what_a_daily_news_issue_says(world, client, boss):
    report = new_report(client, "news", schedule="daily")
    html = making.render_html(report, boss)
    assert "Colombo story 0" in html and "What happened in story 0." in html and "World story" in html
    assert "https://n.lk/0" in html and "Ada Derana" in html
    assert "Old story from days ago" not in html                        # only the last 24 hours
    assert "Colombo story 11" not in html and "the first 8 shown" in html  # the newest 8
    assert "In this issue" in html and 'href="#s2"' in html and 'id="s2"' in html
    assert "Issue 1" in html and "By group code" in html
    assert "Nothing new in this period." in html                        # nothing yet for business or technology
    assert "<img" not in html                                           # a PDF fetches nothing from outside


def test_any_dataset_gives_a_sensible_section_with_no_choices(world, client, boss):
    report = new_report(client, "dataset:companies.v_companies")
    section = report.sections.get()
    assert report.name == "Companies" and section.title_field == "name" and section.link_field == "website" and section.date_field == "first_seen"
    assert section.layout == ReportSection.TABLE
    html = making.render_html(report, boss)
    assert "Acme Bank" in html and "Beta Stores" in html
    assert 'href="https://a.lk"' in html
    news = new_report(client, "dataset:news.news_items")
    s = news.sections.get()
    assert (s.title_field, s.text_field, s.link_field, s.date_field, s.layout) == ("title", "summary", "url", "first_seen_at", ReportSection.ARTICLES)


def test_the_schedule_sets_how_recent(world, client, boss):
    daily = new_report(client, "dataset:news.news_items", schedule="daily").sections.get()
    weekly = new_report(client, "dataset:news.news_items", schedule="weekly", name="W").sections.get()
    assert daily.days == 1 and weekly.days == 7


def test_ready_made_ones_all_work(world, client, boss):
    for code in sections.READY_MADE:
        report = new_report(client, code)
        assert report.sections.exists()
        assert "problem" not in json.dumps(newsletter.sections_for(report, boss))


def test_every_day_on_its_own_and_only_once(world, client, boss):
    report = new_report(client, "news", schedule="daily")
    out = StringIO()
    call_command("run_reports", stdout=out)
    assert report.runs.count() == 1 and "Daily news" in out.getvalue()
    call_command("run_reports", stdout=StringIO())
    assert report.runs.count() == 1
    report.refresh_from_db()
    assert making.is_due(report, report.last_scheduled + timedelta(days=1))
    assert "Issue 2" in making.render_html(report, boss)


# ── what's in a section ──────────────────────────────────────────────
def section_of(report, **change):
    section = report.sections.first()
    for k, v in change.items():
        setattr(section, k, v)
    section.save()
    return newsletter._section(section, report.owner, "s1")


def test_news_goes_by_its_own_date_when_it_has_one(world, client, boss):
    with connection.cursor() as cur:
        cur.execute("update news.news_items set published_at = %s where url = 'https://n.lk/11'", [NOW + timedelta(hours=1)])
    report = new_report(client, "news")
    out = newsletter._section(report.sections.get(heading="Sri Lanka"), boss, "s2")
    assert out["articles"][0]["title"] == "Colombo story 11"            # the newest story, not the last one collected
    assert len(out["articles"][0]["meta"]) == 2 and len(out["articles"][1]["meta"]) == 1    # its source and date; no date of its own, no collected date in its place


def test_a_section_with_no_rows_prints_no_empty_lines(world, client, boss):
    html = making.render_html(new_report(client, "news"), boss)
    assert "(no heading)" not in html


def test_filters_sorting_limit_and_count_only(world, client, boss):
    report = new_report(client, "dataset:companies.v_companies")
    out = section_of(report, days=0, filters=[{"field": "country", "value": ["LK"]}], sort_field="name", sort_desc=False)
    assert [line[0]["text"] for line in out["lines"]] == ["Acme Bank", "Gamma Old"]
    out = section_of(report, sort_desc=True, limit=1)
    assert [line[0]["text"] for line in out["lines"]] == ["Gamma Old"] and (out["more"], out["total"]) == (1, 2)
    out = section_of(report, limit=0, breakdown_field="country", filters=[])
    assert "lines" not in out and out["total"] == 3 and out["bars"][0] == {"label": "LK", "count": "2", "pct": 100}


def test_a_list_counts_once_per_item_in_the_breakdown(world, client, boss):
    report = new_report(client, "news")
    section = report.sections.first()
    section.breakdown_field = "topics"
    out = newsletter._section(section, boss, "s1")
    bars = {b["label"]: b["count"] for b in out["bars"]}
    assert bars == {"Politics": "12", "Economy": "6"}


def test_hidden_rows_and_corrections_behave_as_on_screen(world, client, boss):
    report = new_report(client, "dataset:companies.companies")
    with connection.cursor() as cur:
        cur.execute("insert into audit.hidden_records (dataset, record) values ('companies.companies', 'a')")
    assert "Acme Bank" not in making.render_html(report, boss)


def test_no_html_or_odd_links_from_collected_pages(world, client, boss):
    headline("javascript:alert(1)", "<script>alert(1)</script> Bad", summary="<b>Bold</b> &amp; <img src='https://evil.example/x.png'> text")
    report = new_report(client, "dataset:news.news_items")
    html = making.render_html(report, boss)
    assert "<script>alert" not in html and "javascript:" not in html and "evil.example" not in html and "<b>Bold" not in html
    assert "Bold &amp; text" in html or "Bold &amp;  text" in html


def test_long_text_is_cut_at_a_word(world):
    assert newsletter.clean_text("word " * 100, 20) == "word word word word…"
    assert newsletter.safe_link("ftp://x") == "" and newsletter.safe_link("https://a.lk/x") == "https://a.lk/x"
    assert newsletter.short_link("https://www.a.lk/") == "a.lk"


def test_a_field_or_dataset_thats_gone_is_said_plainly(world, client, boss):
    report = new_report(client, "dataset:news.news_items")
    Field.objects.filter(dataset=ds("news.news_items"), path="first_seen_at").update(available=False)
    assert "isn&#x27;t available any more" in making.render_html(report, boss)
    Dataset.objects.filter(pk=ds("news.news_items").pk).update(missing=True)
    assert "isn&#x27;t there any more" in making.render_html(report, boss)
    assert report.sections.exists()


def test_empty_newsletter_still_makes_a_pdf(world, client, boss):
    report = new_report(client, "blank", name="Nothing yet")
    assert "no sections yet" in making.render_html(report, boss)
    assert not making.make_report(report, boss).problem


# ── changing it ──────────────────────────────────────────────────────
def section_post(**extra):
    return {"dataset": "news.news_items", "heading": "From the form", "layout": "articles", "title_field": "title", "text_field": "summary", "link_field": "url", "date_field": "first_seen_at", "days": "1",
            "limit": "5", "breakdown_field": "source", "detail_fields": ["source", "nonsense"], "filter_field_0": "group_code", "filter_value_0": "LK", **extra}


def test_adding_changing_moving_removing_sections(world, client, boss):
    report = new_report(client, "blank", name="Mine")
    page = client.get(reverse("report_section_new", args=[report.pk]), HTTP_HOST="localhost")
    assert page.status_code == 200 and "Choose a dataset" in page.content.decode()
    fields = client.get(reverse("report_section_fields", args=[report.pk]), {"dataset": "news.news_items"}, HTTP_HOST="localhost").content.decode()
    assert 'value="summary" selected' in fields and 'name="heading"' in fields
    assert client.post(reverse("report_section_new", args=[report.pk]), section_post(), HTTP_HOST="localhost").status_code == 302
    first = report.sections.get()
    assert (first.heading, first.limit, first.days, first.detail_fields) == ("From the form", 5, 1, ["source"])
    assert first.filters == [{"field": "group_code", "value": ["LK"]}]
    assert client.post(reverse("report_section_new", args=[report.pk]), section_post(heading="Second", dataset="companies.v_companies", layout="table", title_field="name", date_field="", days="0", text_field="", link_field="website", filter_field_0="", filter_value_0="", breakdown_field="", detail_fields=[]), HTTP_HOST="localhost").status_code == 302
    edit = client.get(reverse("report_section", args=[report.pk, first.pk]), HTTP_HOST="localhost").content.decode()
    assert 'value="From the form"' in edit and 'value="source" checked' in edit
    client.post(reverse("report_section", args=[report.pk, first.pk]), section_post(heading="Renamed"), HTTP_HOST="localhost")
    assert [s.heading for s in report.sections.all()] == ["Renamed", "Second"]
    change = reverse("report_section_change", args=[report.pk, first.pk])
    client.post(change, {"action": "down"}, HTTP_HOST="localhost")
    assert [s.heading for s in report.sections.all()] == ["Second", "Renamed"]
    client.post(change, {"action": "remove"}, HTTP_HOST="localhost")
    assert [s.heading for s in report.sections.all()] == ["Second"]
    assert b"Second" in client.get(reverse("report", args=[report.pk]), HTTP_HOST="localhost").content


@pytest.mark.parametrize("bad, words", [
    ({"dataset": "nope.nothing"}, "Choose a dataset"),
    ({"date_field": ""}, "which date"),
    ({"title_field": ""}, "heading"),
])
def test_section_form_says_whats_wrong(world, client, boss, bad, words):
    report = new_report(client, "blank")
    r = client.post(reverse("report_section_new", args=[report.pk]), section_post(**bad), HTTP_HOST="localhost")
    assert words in r.content.decode() and not report.sections.exists()


def test_settings_page_keeps_a_newsletter_a_newsletter(world, client, boss):
    report = new_report(client, "news")
    page = client.get(reverse("report", args=[report.pk]), HTTP_HOST="localhost").content.decode()
    assert "What's in it" in page and "Prints the dashboard" not in page
    client.post(reverse("report", args=[report.pk]), {"name": "Morning paper", "intro": "Good morning.", "schedule": "weekly", "weekday": "2"}, HTTP_HOST="localhost")
    report.refresh_from_db()
    assert (report.name, report.schedule, report.weekday, report.dashboard_id) == ("Morning paper", "weekly", 2, None)
    assert "Good morning." in making.render_html(report, boss)
    preview = client.get(reverse("report_preview", args=[report.pk]), HTTP_HOST="localhost").content.decode()
    assert "Morning paper" in preview and "n-mast" in preview
    assert "Newsletter" in client.get(reverse("reports"), HTTP_HOST="localhost").content.decode()


# ── who may do what ──────────────────────────────────────────────────
def only_news_for(role):
    DatasetRight.objects.all().delete()
    DatasetRight.objects.create(dataset=ds("news.news_items"), group=Group.objects.get(name=role), can_view=True)


def test_only_data_the_person_may_see(world, client):
    only_news_for("Analyst")
    me = make_user("analyst", "Analyst")
    client.force_login(me)
    assert client.post(reverse("report_new_newsletter"), {"start": "dataset:companies.v_companies"}, HTTP_HOST="localhost").status_code == 302
    assert not Report.objects.exists()
    page = client.get(reverse("reports"), HTTP_HOST="localhost").content.decode()
    assert "Daily news" in page and "New companies" not in page and "Companies</option>" not in page
    report = new_report(client, "briefing")          # only the parts it may see
    assert {s.dataset.ref for s in report.sections.all()} == {"news.news_items"}
    r = client.post(reverse("report_section_new", args=[report.pk]), section_post(dataset="companies.v_companies"), HTTP_HOST="localhost")
    assert report.sections.filter(dataset__name="v_companies").count() == 0 and b"Choose a dataset" in r.content


def test_a_shared_newsletter_shows_each_person_only_their_own_data(world, client, boss):
    report = new_report(client, "briefing")
    Report.objects.filter(pk=report.pk).update(shared=True)
    run = making.make_report(report, boss)
    only_news_for("Analyst")
    other = make_user("other", "Analyst")
    client.force_login(other)
    preview = client.get(reverse("report_preview", args=[report.pk]), HTTP_HOST="localhost").content.decode()
    assert "Colombo story 0" in preview and "Acme Bank" not in preview and "Your roles don&#x27;t include" in preview
    assert client.get(reverse("report_download", args=[report.pk, run.pk]), HTTP_HOST="localhost").status_code == 403
    assert "Holds data your roles" in client.get(reverse("report", args=[report.pk]), HTTP_HOST="localhost").content.decode()
    # and it can't be changed by someone who doesn't own it
    assert client.post(reverse("report_section_new", args=[report.pk]), section_post(), HTTP_HOST="localhost").status_code == 403
    assert client.post(reverse("report_section_change", args=[report.pk, report.sections.first().pk]), {"action": "remove"}, HTTP_HOST="localhost").status_code == 403


def test_a_scheduled_issue_uses_its_owners_rights(world, client):
    only_news_for("Analyst")
    me = make_user("analyst", "Analyst")
    client.force_login(me)
    report = new_report(client, "dataset:news.news_items", schedule="daily")
    DatasetRight.objects.all().delete()
    call_command("run_reports", stdout=StringIO())
    assert report.runs.count() == 1
    assert "Colombo story" not in making.render_html(report, me)


def test_dashboard_reports_still_work_beside_them(world, client, boss):
    from webapp.charts.models import Dashboard
    board = Dashboard.objects.create(owner=boss, name="Board")
    r = client.post(reverse("reports"), {"name": "Board report", "dashboard": board.pk, "schedule": ""}, HTTP_HOST="localhost")
    assert r.status_code == 302
    report = Report.objects.get(name="Board report")
    assert not report.is_newsletter and "Board" in making.render_html(report, boss) and not report.sections.exists()
