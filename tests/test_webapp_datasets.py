"""The web app, phase 2 (docs/user-platform-plan.md): the automatic catalog, dataset rights, the generic grid
endpoint, saved views and exports. Runs on the throwaway PostgreSQL with small made-up tables."""

import csv
import io
import json

import pytest
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.db import connection
from django.urls import reverse

from webapp.audit.models import AuditEvent
from webapp.catalog.models import Dataset, DatasetRight, Field
from webapp.catalog.scan import humanize, scan
from webapp.explore.export import safe_text
from webapp.explore.models import SavedView

pytestmark = pytest.mark.django_db
PW = "correct-horse-battery-9"


def make_user(name, *roles):
    u = User.objects.create_user(name, password=PW)
    u.groups.set(Group.objects.filter(name__in=roles))
    return u


@pytest.fixture
def data():
    """Two datasets we know (companies.v_companies is known; vehicles.ads too) and one nobody has heard of."""
    cache.clear()
    with connection.cursor() as cur:
        cur.execute("""
            create schema if not exists companies; create schema if not exists vehicles; create schema if not exists news;
            create table companies.companies (id text primary key, name text, country text, website text,
                employees int, revenue double precision, public boolean, first_seen timestamptz, topics text[], extra jsonb);
            insert into companies.companies values
              ('a', 'Acme Bank', 'LK', 'https://acme.lk', 50, 1.5, true, '2026-11-01 10:00+05:30', '{bank,lk}',
               '{"city": "Colombo", "founded": 1990, "tags": ["red", "blue"], "listed": true, "opened": "2020-01-02", "site": "https://a.lk/x", "nested": {"a": 1}}'),
              ('b', 'Beta Tea', 'LK', 'https://beta.lk', 5, null, false, '2026-11-03 10:00+05:30', '{tea}',
               '{"city": "Kandy", "founded": "unknown", "tags": ["red"], "listed": false}'),
              ('c', 'Gamma = Inc', 'US', null, 500, 99.25, null, '2026-11-05 10:00+05:30', null, '{"city": "Boston", "tags": []}'),
              ('d', '50% off_shop', null, null, null, 'NaN', null, null, null, null);
            create view companies.v_companies as select id, name, country, website, employees from companies.companies;
            create table vehicles.brand_new_thing (code text, note text);
            insert into vehicles.brand_new_thing values ('x', 'hello');
        """)
    scan()
    yield
    cache.clear()


@pytest.fixture
def admin(client):
    client.force_login(make_user("ada", "Administrator"))
    return client


def rows(client, ref="companies.companies", **params):
    for k in ("cols", "filters", "sorts"):
        if k in params:
            params[k] = json.dumps(params[k])
    return client.get(reverse("dataset_rows", args=[ref]), params)


def names(res):
    assert res.status_code == 200, res.content
    return [r["name"] for r in res.json()["rows"]]


# ── the scan ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_humanize():
    assert humanize("first_seen") == "First seen" and humanize("price_lkr") == "Price LKR"
    assert humanize("marketCap") == "Market cap" and humanize("id") == "ID"


def test_scan_finds_columns_and_keys_inside_json(data):
    ds = Dataset.objects.get(schema="companies", name="companies")
    assert ds.row_count == 4 and ds.key_columns == ["id"] and ds.label == "Company records"
    f = {x.path: x for x in ds.fields.all()}
    assert f["website"].type == "link" and f["employees"].type == "number" and f["public"].type == "bool"
    assert f["first_seen"].type == "datetime" and f["topics"].type == "list" and f["topics"].values == ["bank", "lk", "tea"]
    assert f["country"].values == ["LK", "US"] and f["country"].fill_pct == 75
    assert f["extra"].type == "json" and not f["extra"].shown
    assert f["extra.city"].type == "text" and f["extra.tags"].type == "list" and f["extra.tags"].is_jsonb
    assert f["extra.tags"].values == ["blue", "red"]
    assert f["extra.listed"].type == "bool" and f["extra.opened"].type == "datetime" and f["extra.site"].type == "link"
    assert f["extra.nested"].type == "json"
    assert f["extra.founded"].type == "text"             # a number in one row and text in another: treated as text


def test_a_new_table_and_new_json_key_appear_on_the_next_scan_and_admin_edits_survive(data):
    ds = Dataset.objects.get(schema="companies", name="companies")
    Dataset.objects.filter(pk=ds.pk).update(label="My name")
    Field.objects.filter(dataset=ds, path="name").update(label="Company", shown=False)
    with connection.cursor() as cur:
        cur.execute("""update companies.companies set extra = extra || '{"ceo": "Nimal"}' where id = 'a';
                       create table news.another (a int)""")
    summary = scan()
    assert summary["new"] == ["news.another"] and summary["new_fields"] >= 2
    ds.refresh_from_db()
    assert ds.label == "My name"
    name = ds.fields.get(path="name")
    assert name.label == "Company" and not name.shown
    assert ds.fields.filter(path="extra.ceo").exists()


def test_a_dropped_table_is_marked_gone_not_deleted(data):
    with connection.cursor() as cur:
        cur.execute("drop table vehicles.brand_new_thing")
    assert scan()["missing"] == ["vehicles.brand_new_thing"]
    assert Dataset.objects.get(name="brand_new_thing").missing


def test_the_scan_command_and_scan_button(data, admin, capsys):
    from django.core.management import call_command
    call_command("catalog_scan")
    assert "datasets scanned" in capsys.readouterr().out
    assert admin.post(reverse("catalog_scan")).status_code == 302
    assert AuditEvent.objects.filter(action="catalog_scanned").exists()


def test_an_empty_table_still_has_columns_to_show(data, admin):
    with connection.cursor() as cur:
        cur.execute("create table vehicles.empty_one (a text, b int)")
    scan()
    res = rows(admin, "vehicles.empty_one")
    assert res.status_code == 200 and res.json()["total"] == 0
    assert Dataset.objects.get(name="empty_one").fields.filter(shown=True).count() == 2


# ── rights ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_known_datasets_start_with_roles_unknown_ones_with_administrators_only(data, client):
    known = Dataset.objects.get(schema="companies", name="v_companies")
    assert set(known.rights.values_list("group__name", flat=True)) == {"Editor", "Analyst", "Viewer"}
    assert not Dataset.objects.get(name="brand_new_thing").rights.exists()
    client.force_login(make_user("vic", "Viewer"))
    page = client.get(reverse("datasets")).content.decode()
    assert "Companies" in page and "Brand new thing" not in page
    assert client.get(reverse("dataset", args=["vehicles.brand_new_thing"])).status_code == 403
    assert rows(client, "vehicles.brand_new_thing").status_code == 403
    assert client.get(reverse("dataset", args=["companies.v_companies"])).status_code == 200
    assert client.get(reverse("dataset", args=["no.such"])).status_code == 404


def test_datasets_need_the_menu_right_and_a_switched_off_one_is_for_administrators(data, client, admin):
    ds = Dataset.objects.get(schema="companies", name="v_companies")
    Dataset.objects.filter(pk=ds.pk).update(enabled=False)
    assert admin.get(reverse("dataset", args=[ds.ref])).status_code == 200
    client.force_login(make_user("vic", "Viewer"))
    assert client.get(reverse("dataset", args=[ds.ref])).status_code == 403
    client.force_login(make_user("nobody"))
    assert client.get(reverse("datasets")).status_code == 403


def test_admin_sets_dataset_rights(data, admin):
    new, viewer = Dataset.objects.get(name="brand_new_thing"), Group.objects.get(name="Viewer")
    posted = {f"v_{r.dataset_id}_{r.group_id}": "on" for r in DatasetRight.objects.filter(can_view=True)}
    posted.update({f"e_{r.dataset_id}_{r.group_id}": "on" for r in DatasetRight.objects.filter(can_export=True)})
    posted[f"v_{new.pk}_{viewer.pk}"] = "on"
    known = Dataset.objects.get(schema="companies", name="v_companies")
    posted.pop(f"v_{known.pk}_{viewer.pk}")
    assert admin.post(reverse("dataset_rights"), posted).status_code == 302
    assert new.rights.get(group=viewer).can_view and not known.rights.filter(group=viewer).exists()
    assert "Viewer vehicles.brand_new_thing view" in AuditEvent.objects.get(action="dataset_rights_changed").summary


def test_admin_renames_and_hides_a_field_and_it_stops_being_served(data, admin):
    ds = Dataset.objects.get(schema="companies", name="companies")
    posted = {"label": "Firms", "description": "All firms", "enabled": "on"}
    for f in ds.fields.all():
        posted[f"label_{f.pk}"] = "Firm name" if f.path == "name" else f.label
        if f.path != "website":
            posted[f"available_{f.pk}"] = "on"
    assert admin.post(reverse("catalog_dataset", args=[ds.pk]), posted).status_code == 302
    ds.refresh_from_db()
    assert ds.label == "Firms" and ds.fields.get(path="name").label == "Firm name"
    found = rows(admin, cols=["name", "website"]).json()["rows"][0]
    assert "website" not in found and "name" in found
    assert rows(admin, filters=[{"field": "website", "value": "x"}]).status_code == 400


# ── rows: sorting, filters, safety ───────────────────────────────────────────────────────────────────────────
def test_lists_of_numbers_scan_and_filter(data, admin):
    """annualreports.people has pages int[]: the scan once stopped on it (sorting numbers as text), and a filter on it was an SQL error."""
    with connection.cursor() as cur:
        cur.execute("""create table vehicles.people (name text primary key, pages integer[], box double precision[]);
                       insert into vehicles.people values ('Ana', '{2,10}', '{1.5,2}'), ('Ben', '{3}', null)""")
    scan()
    f = {x.path: x for x in Dataset.objects.get(schema="vehicles", name="people").fields.all()}
    assert f["pages"].type == "list" and f["pages"].values == ["10", "2", "3"] and f["box"].type == "list"
    got = names(rows(admin, "vehicles.people", cols=["name", "pages"], filters=[{"field": "pages", "value": ["10"]}]))
    assert got == ["Ana"]
    assert rows(admin, "vehicles.people", cols=["name", "pages"]).json()["rows"][0]["pages"] == ["2", "10"]


def test_rows_sort_page_and_count(data, admin):
    res = rows(admin, cols=["name", "employees"], sorts=[{"field": "employees", "dir": "desc"}], limit=2, start=1)
    data_ = res.json()
    assert data_["total"] == 4 and [r["name"] for r in data_["rows"]] == ["Acme Bank", "Beta Tea"]
    assert set(data_["rows"][0]) == {"name", "employees", "_row", "_id"} and data_["rows"][0]["_row"] == 1


def test_filters_of_every_kind(data, admin):
    f = lambda field, value: names(rows(admin, cols=["name"], filters=[{"field": field, "value": value}],
                                         sorts=[{"field": "name"}]))
    assert f("name", "bank") == ["Acme Bank"]
    assert f("name", "50%") == ["50% off_shop"] and f("name", "_") == ["50% off_shop"]     # searched for, not wildcards
    assert f("employees", ">10") == ["Acme Bank", "Gamma = Inc"]
    assert f("employees", "5..50") == ["Acme Bank", "Beta Tea"] and f("employees", "=5") == ["Beta Tea"]
    assert f("first_seen", "2026-11-03") == ["Beta Tea"]
    assert f("first_seen", ">=2026-11-03") == ["Beta Tea", "Gamma = Inc"]
    assert f("first_seen", "2026-11-01..2026-11-03") == ["Acme Bank", "Beta Tea"]
    assert f("country", ["LK"]) == ["Acme Bank", "Beta Tea"]
    assert f("country", ["US", "(empty)"]) == ["50% off_shop", "Gamma = Inc"]
    assert f("public", ["no"]) == ["Beta Tea"] and f("public", ["(empty)"]) == ["50% off_shop", "Gamma = Inc"]
    assert f("topics", ["tea", "bank"]) == ["Acme Bank", "Beta Tea"]                       # any of them
    assert f("extra.tags", ["blue"]) == ["Acme Bank"]
    assert f("extra.tags", ["(empty)"]) == ["50% off_shop", "Gamma = Inc"]
    assert f("extra.city", "kan") == ["Beta Tea"] and f("extra.listed", ["yes"]) == ["Acme Bank"]
    assert f("extra.opened", "2020-01-02") == ["Acme Bank"]


def test_search_looks_in_the_shown_text_columns(data, admin):
    assert names(rows(admin, cols=["name", "extra.city"], q="BOSTON")) == ["Gamma = Inc"]
    assert names(rows(admin, cols=["name"], q="boston")) == []


def test_bad_filters_say_what_to_type(data, admin):
    res = rows(admin, filters=[{"field": "employees", "value": "lots"}])
    assert res.status_code == 400 and "type a number" in res.json()["error"]
    res = rows(admin, filters=[{"field": "first_seen", "value": "yesterday"}])
    assert res.status_code == 400 and "type a date" in res.json()["error"]


def test_a_request_cant_reach_other_columns_or_inject_sql(data, admin):
    evil = 'name"; drop table companies.companies; --'
    assert rows(admin, filters=[{"field": evil, "value": "x"}]).status_code == 400
    assert rows(admin, filters=[{"field": "password", "value": "x"}]).status_code == 400
    found = rows(admin, cols=["name", evil, "id; select 1"]).json()["rows"][0]
    assert set(found) == {"name", "_row", "_id"}             # _id: the row's key, for editing and history
    assert names(rows(admin, cols=["name"], sorts=[{"field": evil}], limit=1))     # unknown sort: ignored
    res = rows(admin, cols=["name"], filters=[{"field": "name", "value": "'; drop table companies.companies; --"}])
    assert res.json()["total"] == 0
    assert rows(admin, cols=[evil]).status_code == 400                               # nothing left to show
    with connection.cursor() as cur:
        cur.execute("select count(*) from companies.companies")
        assert cur.fetchone()[0] == 4


def test_nan_and_json_values_are_valid_json(data, admin):
    res = rows(admin, cols=["name", "revenue", "extra.nested", "first_seen", "topics"], sorts=[{"field": "name"}])
    by = {r["name"]: r for r in json.loads(res.content)["rows"]}
    assert by["50% off_shop"]["revenue"] is None
    assert by["Acme Bank"]["extra.nested"] == '{"a": 1}' and by["Acme Bank"]["topics"] == ["bank", "lk"]
    assert by["Acme Bank"]["first_seen"] == "2026-11-01 10:00"                       # the local time zone


def test_the_page_describes_the_columns(data, admin):
    page = admin.get(reverse("dataset", args=["companies.companies"])).content.decode()
    config = json.loads(page.split('id="dataset-config" type="application/json">')[1].split("</script>")[0])
    cols = {c["field"]: c for c in config["columns"]}
    assert cols["country"]["filter"] == "choice" and cols["country"]["choices"] == ["(empty)", "LK", "US"]
    assert cols["employees"]["filter"] == "number" and cols["first_seen"]["filter"] == "date"
    assert cols["public"]["choices"] == ["(empty)", "yes", "no"] and cols["website"]["filter"] == "text"
    assert config["canExport"] and config["defaultSort"] == {"field": "name", "dir": "asc"}


# ── saved views ──────────────────────────────────────────────────────────────────────────────────────────────
def save(client, name="LK", shared=False, ref="companies.v_companies", state=None):
    body = {"name": name, "shared": shared, "state": state or {
        "columns": [{"field": "name", "visible": True, "width": 300}, {"field": "nope", "visible": True}],
        "sorts": [{"field": "name", "dir": "desc"}], "filters": [{"field": "country", "value": ["LK"]}], "q": "bank"}}
    return client.post(reverse("dataset_view_save", args=[ref]), json.dumps(body), content_type="application/json")


def test_saved_views_private_shared_replaced_and_deleted(data, client):
    me, other = make_user("ann", "Editor"), make_user("bob", "Viewer")
    client.force_login(me)
    res = save(client).json()
    view = SavedView.objects.get(pk=res["id"])
    assert res["url"].endswith(f"?view={view.pk}") and view.owner == me
    assert [c["field"] for c in view.state["columns"]] == ["name"] and view.state["columns"][0]["width"] == 300
    assert view.state["filters"] == [{"field": "country", "value": ["LK"]}] and view.state["q"] == "bank"
    assert save(client).json()["id"] == view.pk                                      # the same name replaces it
    assert save(client, name=" ").status_code == 400

    url = reverse("dataset", args=["companies.v_companies"])
    client.force_login(other)
    assert '"LK"' not in client.get(url).content.decode().split('"views":')[1].split('"currentView"')[0]
    assert client.post(reverse("dataset_view_delete", args=["companies.v_companies", view.pk])).status_code == 403
    client.force_login(me)
    save(client, shared=True)
    client.force_login(other)
    page = client.get(url, {"view": view.pk}).content.decode()
    assert f'"currentView": {view.pk}' in page and '"owner": "ann"' in page
    client.force_login(me)
    assert client.post(reverse("dataset_view_delete", args=["companies.v_companies", view.pk])).json()["ok"]
    assert not SavedView.objects.filter(pk=view.pk).exists()
    assert {"view_saved", "view_deleted"} <= set(AuditEvent.objects.values_list("action", flat=True))


def test_sharing_a_view_gives_no_right_to_the_dataset(data, admin, client):
    assert save(admin, ref="vehicles.brand_new_thing", shared=True, state={"columns": []}).status_code == 200
    client.force_login(make_user("vic", "Viewer"))
    view = SavedView.objects.get()
    assert client.get(reverse("dataset", args=["vehicles.brand_new_thing"]), {"view": view.pk}).status_code == 403


# ── exports ──────────────────────────────────────────────────────────────────────────────────────────────────
def get_export(client, fmt, ref="companies.companies", **params):
    for k in ("cols", "filters", "sorts"):
        if k in params:
            params[k] = json.dumps(params[k])
    return client.get(reverse("dataset_export", args=[ref, fmt]), params)


def body(res):
    return b"".join(res.streaming_content)


def test_csv_follows_the_screen_and_is_audited(data, admin):
    res = get_export(admin, "csv", cols=["name", "country", "topics"], filters=[{"field": "country", "value": ["LK"]}],
                     sorts=[{"field": "name", "dir": "desc"}])
    assert res.status_code == 200 and "company-records-" in res["Content-Disposition"]
    table = list(csv.reader(io.StringIO(body(res).decode("utf-8-sig"))))
    assert table == [["Name", "Country", "Topics"], ["Beta Tea", "LK", "tea"], ["Acme Bank", "LK", "bank, lk"]]
    event = AuditEvent.objects.get(action="export")
    assert "2 rows of Company records as CSV" in event.summary and event.detail["columns"] == ["name", "country", "topics"]


def test_excel_export(data, admin):
    from openpyxl import load_workbook
    res = get_export(admin, "xlsx", cols=["name", "employees", "first_seen"], sorts=[{"field": "name"}])
    book = load_workbook(io.BytesIO(body(res)))
    rows_ = list(book.active.values)
    assert rows_[0] == ("Name", "Employees", "First seen") and len(rows_) == 5
    assert ("Acme Bank", 50, "2026-11-01 10:00") in rows_


def test_export_needs_the_right_and_is_safe_for_excel(data, client):
    client.force_login(make_user("vic", "Viewer"))
    assert get_export(client, "csv", "companies.v_companies").status_code == 403     # Viewer sees but doesn't export
    client.force_login(make_user("edd", "Editor"))
    assert get_export(client, "csv", "companies.v_companies").status_code == 200
    assert get_export(client, "pdf", "companies.v_companies").status_code == 404
    assert safe_text("=1+1") == "'=1+1" and safe_text("@x") == "'@x" and safe_text("-5") == "'-5"
    assert safe_text("Acme\x01") == "Acme" and safe_text(5) == 5
