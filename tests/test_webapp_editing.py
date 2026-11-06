"""The web app, phase 4 (docs/user-platform-plan.md): corrections to collected data, their history, rule A in the real
collectors' stores, hiding, the conflict check, rights and the database's column grants.

The stores use Django's own connection here, so the web app and the collectors share the test's transaction."""

import csv
import io
import json

import pytest
from django.contrib.auth.models import Group, User
from django.db import connection
from django.urls import reverse

from warehouse import edits, postgres
from webapp.audit.models import AuditEvent
from webapp.catalog.models import Dataset, DatasetRight, Field
from webapp.catalog.scan import scan

pytestmark = pytest.mark.django_db
COMPANIES, VEHICLES = "companies.v_companies", "vehicles.v_vehicle_ads_latest"


def make_user(name, *roles):
    u = User.objects.create_user(name, password="correct-horse-battery-9")
    u.groups.set(Group.objects.filter(name__in=roles))
    return u


def row(id_="cse:ACME", name="ACME PLC", website="https://acme.lk", country="LK", industry="Retail"):
    from techintel.domains import root_domain
    return {"id": id_, "source": "cse", "source_id": id_.split(":")[1], "name": name, "country": country,
            "website": website, "domain": root_domain(website) if website else None, "industry": industry, "extra": {}}


def ad(url="https://ikman.lk/ad/1", title="Toyota Axio", price=5_000_000, location="Colombo"):
    return {"title": title, "url": url, "price": f"Rs {price}", "price_lkr": price, "location": location}


@pytest.fixture
def stores():
    """The real stores, on the test's connection. Everything is undone at the end of the test."""
    from companies.store import CompanyStore
    from vehicles.store import VehicleStore
    connection.ensure_connection()
    con = connection.connection
    with connection.cursor() as cur:
        cur.execute("create schema if not exists companies; create schema if not exists vehicles; create schema if not exists news")
    postgres.migrate(con, ("companies", "vehicles"))
    company, vehicle = CompanyStore.__new__(CompanyStore), VehicleStore.__new__(VehicleStore)
    company.con = vehicle.con = con
    return company, vehicle


@pytest.fixture
def data(stores):
    company, vehicle = stores
    company.upsert_companies([row(), row("cse:BETA", "BETA PLC", "https://beta.lk"), row("cse:GAMMA", "GAMMA PLC", None)])
    vehicle.save_report({"site": "ikman", "success": True, "ads": [ad(), ad("https://ikman.lk/ad/2", "Honda Vezel", 9_000_000)]},
                        "2026-11-01T00:00:00Z")
    scan()
    return company, vehicle


@pytest.fixture
def editor(client, data):
    client.force_login(make_user("eve", "Editor"))
    return client


def edit(client, record="cse:ACME", field="name", value="Acme PLC", ref=COMPANIES, **extra):
    return client.post(reverse("dataset_edit_cell", args=[ref]), json.dumps({"id": record, "field": field, "value": value, **extra}),
                       content_type="application/json")


def one(sql, *params):
    with connection.cursor() as cur:
        cur.execute(sql, params)
        found = cur.fetchone()
    return found[0] if found and len(found) == 1 else found


def company_value(column="name", record="cse:ACME"):
    return one(f"select {column} from companies.companies where id = %s", record)


def history(record="cse:ACME", table="companies.companies"):
    with connection.cursor() as cur:
        return edits.history_of(cur, table, record)


def correction(field="name", record="cse:ACME", table="companies.companies"):
    with connection.cursor() as cur:
        return edits.corrections_of(cur, table, record).get(field)


# ── a person corrects a value ────────────────────────────────────────────────────────────────────────────────
def test_a_correction_changes_the_main_table_and_is_in_the_history(editor):
    res = edit(editor, seen="ACME PLC")
    assert res.status_code == 200 and res.json()["value"] == "Acme PLC"
    assert company_value() == "Acme PLC"
    assert one("select name from companies.v_companies where id = 'cse:ACME'") == "Acme PLC"     # everything reads the same table
    entry, = history()
    assert (entry["field"], entry["old"], entry["new"], entry["by"], entry["origin"]) == ("name", "ACME PLC", "Acme PLC", "eve", "user")
    assert correction()["source"] == "ACME PLC" and correction()["user"] == "Acme PLC"
    event = AuditEvent.objects.get(action="cell_edited")
    assert event.username == "eve" and event.detail["record"] == "cse:ACME"


def test_saving_the_same_value_changes_nothing(editor):
    assert edit(editor, value=" ACME PLC ").status_code == 200
    assert history() == [] and correction() is None and not AuditEvent.objects.filter(action="cell_edited").exists()


def test_someone_else_changed_it_first(editor):
    assert edit(editor, value="First", seen="ACME PLC").status_code == 200
    res = edit(editor, value="Second", seen="ACME PLC")                         # still shows the old value
    assert res.status_code == 409 and res.json()["conflict"] and res.json()["current"] == "First" and "First" in res.json()["error"]
    assert company_value() == "First" and len(history()) == 1


@pytest.mark.parametrize("field,value,problem", [
    ("name", "", "can't be empty"), ("name", "x" * 501, "at most 500"), ("country", "Sri Lanka", "two-letter"),
    ("website", "not a site", "web address"), ("website", "ftp://a.lk", "web address"), ("name", ["a"], "kind of value"),
])
def test_values_are_checked(editor, field, value, problem):
    res = edit(editor, field=field, value=value)
    assert res.status_code == 400 and problem in res.json()["error"] and history() == []


def test_values_are_tidied(editor):
    assert edit(editor, field="country", value="gb").json()["value"] == "GB"
    assert edit(editor, field="industry", value="  ").json()["value"] is None and company_value("industry") is None
    assert edit(editor, value="A\x00B").json()["value"] == "AB"
    assert edit(editor, record="cse:NOPE").status_code == 404


def test_a_website_brings_its_domain(editor):
    res = edit(editor, field="website", value="new-acme.com")
    assert res.json()["value"] == "https://new-acme.com" and res.json()["linked"] == {"domain": "new-acme.com"}
    assert company_value("domain") == "new-acme.com"
    assert [e["origin"] for e in history()] == ["linked", "user"]


# ── rule A, in the real stores ───────────────────────────────────────────────────────────────────────────────
def test_the_correction_survives_when_the_source_repeats_itself(editor, data):
    company, _ = data
    edit(editor, value="Acme PLC")
    added, updated = company.upsert_companies([row()], "2026-12-01T00:00:00Z")
    assert (added, updated) == (0, 1) and company_value() == "Acme PLC"
    assert company_value("last_seen") is not None and correction()["user"] == "Acme PLC" and len(history()) == 1
    company.upsert_companies([{**row(), "industry": None}])  # no news about industry either
    assert company_value() == "Acme PLC" and company_value("industry") == "Retail"


def test_rule_a_the_source_really_changed_and_the_correction_can_be_put_back(editor, data):
    company, _ = data
    edit(editor, value="Acme PLC")
    company.upsert_companies([row(name="ACME HOLDINGS PLC")])
    assert company_value() == "ACME HOLDINGS PLC" and correction() is None           # the newest change wins
    latest = history()[0]
    assert (latest["old"], latest["new"], latest["by"], latest["origin"]) == ("Acme PLC", "ACME HOLDINGS PLC", "collector:companies", "collector")
    res = editor.post(reverse("dataset_restore", args=[COMPANIES]), {"record": "cse:ACME", "entry": latest["id"]})
    assert res.status_code == 200 and company_value() == "Acme PLC"
    assert json.loads(res["HX-Trigger"])["cellsChanged"] == {"id": "cse:ACME", "changes": {"name": "Acme PLC"}, "edited": ["name"]}
    assert correction()["source"] == "ACME HOLDINGS PLC"                             # what the source says now
    company.upsert_companies([row(name="ACME HOLDINGS PLC")])
    assert company_value() == "Acme PLC"                                             # and it holds from here on


def test_setting_it_back_to_what_the_source_said_ends_the_correction(editor, data):
    company, _ = data
    edit(editor, value="Acme PLC")
    edit(editor, value="ACME PLC")
    assert correction() is None and len(history()) == 2
    company.upsert_companies([row(name="ACME 2")])
    assert company_value() == "ACME 2"


def test_when_the_source_comes_round_to_the_correction(editor, data):
    company, _ = data
    edit(editor, value="Acme PLC")
    company.upsert_companies([row(name="Acme PLC")])
    assert correction() is None and len(history()) == 1 and company_value() == "Acme PLC"


def test_a_changed_website_takes_its_domain_along(editor, data):
    company, _ = data
    edit(editor, field="website", value="https://other.com")
    company.upsert_companies([row(website="https://acme.lk/home")])                  # same domain, new address
    assert company_value("website") == "https://acme.lk/home" and company_value("domain") == "acme.lk"
    assert correction("website") is None and correction("domain") is None


def set_rule(rule, ref=COMPANIES, path="name"):
    ds = Dataset.objects.get(schema=ref.split(".")[0], name=ref.split(".")[1])
    Field.objects.filter(dataset=ds, path=path).update(edit_rule=rule)


def test_rule_b_the_person_wins(editor, data):
    company, _ = data
    set_rule("B")
    edit(editor, value="Acme PLC")
    company.upsert_companies([row(name="ACME 2")])
    assert company_value() == "Acme PLC" and correction()["source"] == "ACME 2" and len(history()) == 1


def test_rule_c_waits_for_a_person(editor, data):
    company, _ = data
    set_rule("C")
    edit(editor, value="Acme PLC")
    company.upsert_companies([row(name="ACME 2")])
    company.upsert_companies([row(name="ACME 2")])
    assert company_value() == "Acme PLC" and correction()["pending"] == "ACME 2"
    assert len(history()) == 2                                                        # flagged once, not on every run
    rows = editor.get(reverse("dataset_rows", args=[COMPANIES]), {"q": "Acme PLC"}).json()["rows"]
    assert rows[0]["_edited"] == ["name"] and rows[0]["_pending"] == ["name"]
    panel = editor.get(reverse("dataset_history", args=[COMPANIES]), {"id": "cse:ACME"}).content.decode()
    assert "the source now says something new" in panel and "ACME 2" in panel
    keep = editor.post(reverse("dataset_review", args=[COMPANIES]), {"record": "cse:ACME", "field": "name", "choice": "keep"})
    assert keep.status_code == 200 and company_value() == "Acme PLC" and correction()["pending"] is None
    company.upsert_companies([row(name="ACME 2")])
    assert correction()["pending"] is None                                            # already decided
    company.upsert_companies([row(name="ACME 3")])
    assert correction()["pending"] == "ACME 3"
    accept = editor.post(reverse("dataset_review", args=[COMPANIES]), {"record": "cse:ACME", "field": "name", "choice": "accept"})
    assert accept.status_code == 200 and company_value() == "ACME 3" and correction() is None
    assert editor.post(reverse("dataset_review", args=[COMPANIES]), {"record": "cse:ACME", "field": "name", "choice": "keep"}).status_code == 409


# ── vehicles: one row per run ────────────────────────────────────────────────────────────────────────────────
def test_vehicles_a_correction_is_carried_to_each_new_run(editor, data):
    _, vehicle = data
    url = "https://ikman.lk/ad/1"
    assert edit(editor, record=url, field="title", value="Toyota Axio 2016", ref=VEHICLES).status_code == 200
    assert edit(editor, record=url, field="price_lkr", value="4,800,000", ref=VEHICLES, seen=5_000_000).json()["value"] == 4_800_000
    report = {"site": "ikman", "success": True, "ads": [ad()]}
    vehicle.save_report(report, "2026-11-02T00:00:00Z")
    assert report["ads"][0]["title"] == "Toyota Axio"                                  # the site's own report isn't touched
    latest = lambda c: one(f"select {c} from vehicles.v_vehicle_ads_latest where url = %s", url)  # noqa: E731
    assert (latest("title"), latest("price_lkr"), latest("times_seen")) == ("Toyota Axio 2016", 4_800_000, 2)
    assert one("select count(*) from vehicles.ads where url = %s", url) == 2
    vehicle.save_report({"site": "ikman", "success": True, "ads": [ad(price=4_500_000)]}, "2026-11-03T00:00:00Z")   # a real new price
    assert latest("price_lkr") == 4_500_000 and latest("title") == "Toyota Axio 2016"
    assert correction("price_lkr", url, "vehicles.ads") is None and history(url, "vehicles.ads")[0]["by"] == "collector:vehicles"
    # a correction goes on the latest row only: earlier runs stay as they were
    assert one("select title from vehicles.ads where url = %s order by saved_at limit 1", url) == "Toyota Axio 2016"
    assert one("select title from vehicles.ads where url = %s and saved_at = '2026-11-02T00:00:00Z'", url) == "Toyota Axio 2016"
    assert edit(editor, record=url, field="price_lkr", value="abc", ref=VEHICLES).status_code == 400


# ── many rows at once ────────────────────────────────────────────────────────────────────────────────────────
def bulk(client, ids, field="industry", value="Banks", ref=COMPANIES):
    return client.post(reverse("dataset_edit_bulk", args=[ref]), json.dumps({"ids": ids, "field": field, "value": value}),
                       content_type="application/json")


def test_bulk_edit(editor):
    res = bulk(editor, ["cse:ACME", "cse:BETA", "cse:GONE", "cse:ACME"])
    assert res.status_code == 200 and res.json()["changed"] == 2 and res.json()["value"] == "Banks"
    assert company_value("industry") == company_value("industry", "cse:BETA") == "Banks"
    assert history()[0]["origin"] == "bulk"
    event = AuditEvent.objects.get(action="rows_edited")
    assert event.detail["count"] == 2 and "2 rows" in event.summary


def test_bulk_edit_is_all_or_nothing(editor):
    res = bulk(editor, ["cse:ACME", "cse:BETA"], field="name", value="")
    assert res.status_code == 400 and company_value() == "ACME PLC" and history() == []
    assert bulk(editor, ["x"] * 2 + [str(i) for i in range(5001)]).status_code == 400
    assert bulk(editor, []).status_code == 400
    assert not AuditEvent.objects.filter(action="rows_edited").exists()


def test_bulk_website_brings_each_domain(editor):
    res = bulk(editor, ["cse:ACME", "cse:GAMMA"], field="website", value="https://shared.lk")
    assert res.json()["linked"] == {"cse:ACME": {"domain": "shared.lk"}, "cse:GAMMA": {"domain": "shared.lk"}}


# ── hiding ───────────────────────────────────────────────────────────────────────────────────────────────────
def names_in_grid(client, ref=COMPANIES, column="name"):
    res = client.get(reverse("dataset_rows", args=[ref]), {"cols": json.dumps([column])})
    assert res.status_code == 200, res.content
    return sorted(r[column] for r in res.json()["rows"]), res.json()["total"]


def test_hidden_rows_stay_hidden_and_can_come_back(editor, data):
    company, vehicle = data
    client = editor
    assert names_in_grid(client)[1] == 3                                                # remembered for 30 seconds, then asked again
    res = client.post(reverse("dataset_hide", args=[COMPANIES]), json.dumps({"ids": ["cse:BETA", "cse:GONE"]}), content_type="application/json")
    assert res.json()["hidden"] == 1
    assert names_in_grid(client) == (["ACME PLC", "GAMMA PLC"], 2)                      # the count follows at once
    assert names_in_grid(client, "companies.companies") == (["ACME PLC", "GAMMA PLC"], 2)   # the plain table's list too
    assert one("select count(*) from companies.companies where id = 'cse:BETA'") == 1   # not deleted
    company.upsert_companies([row("cse:BETA", "BETA PLC", "https://beta.lk")])          # a collector sees it again
    assert names_in_grid(client)[1] == 2
    # and the dashboard's numbers, lists and details leave it out
    from webapp.insights import queries
    con = connection.connection
    assert queries.companies_summary(con)["counts"]["companies"] == 2 and queries.companies_list(con)["total"] == 2
    assert queries.company_detail(con, "cse:BETA") == {} and queries.company_detail(con, "cse:ACME")["company"]["name"] == "ACME PLC"
    changes = client.get(reverse("dataset_changes", args=[COMPANIES])).content.decode()
    assert "BETA PLC" in changes and "Show again" in changes
    back = client.post(reverse("dataset_unhide", args=[COMPANIES]), {"record": "cse:BETA"}, HTTP_HX_REQUEST="true")
    assert back.status_code == 200 and "Nothing is hidden." in back.content.decode()
    assert names_in_grid(client)[1] == 3
    assert list(AuditEvent.objects.filter(action__in=("rows_hidden", "rows_unhidden")).order_by("id").values_list("action", flat=True)) == ["rows_hidden", "rows_unhidden"]


def test_hidden_ads_stay_out_of_the_latest_list_and_price_changes(editor, data):
    _, vehicle = data
    url = "https://ikman.lk/ad/1"
    vehicle.save_report({"site": "ikman", "success": True, "ads": [ad(price=4_000_000)]}, "2026-11-02T00:00:00Z")
    assert one("select count(*) from vehicles.v_vehicle_price_changes") == 1
    editor.post(reverse("dataset_hide", args=[VEHICLES]), json.dumps({"ids": [url]}), content_type="application/json")
    vehicle.save_report({"site": "ikman", "success": True, "ads": [ad(price=3_000_000)]}, "2026-11-03T00:00:00Z")
    assert one("select count(*) from vehicles.v_vehicle_ads_latest where url = %s", url) == 0
    assert one("select count(*) from vehicles.v_vehicle_price_changes") == 0
    assert one("select count(*) from vehicles.ads where url = %s", url) == 3             # still collected
    hist, total = names_in_grid(editor, "vehicles.ads", "title")
    assert total == 1 and hist == ["Honda Vezel"]                                         # the run-by-run list leaves it out too


def test_exports_leave_hidden_rows_out_and_show_corrections(editor):
    edit(editor, value="Acme PLC")
    editor.post(reverse("dataset_hide", args=[COMPANIES]), json.dumps({"ids": ["cse:BETA"]}), content_type="application/json")
    res = editor.get(reverse("dataset_export", args=[COMPANIES, "csv"]), {"cols": json.dumps(["name"])})
    text = b"".join(res.streaming_content).decode("utf-8-sig") if res.streaming else res.content.decode("utf-8-sig")
    names = [r[0] for r in csv.reader(io.StringIO(text))][1:]
    assert sorted(names) == ["Acme PLC", "GAMMA PLC"]


# ── rights ───────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("role", ["Viewer", "Analyst"])
def test_roles_without_the_right_cant_change_anything(client, data, role):
    client.force_login(make_user("vic", role))
    assert edit(client).status_code == 403
    assert bulk(client, ["cse:ACME"]).status_code == 403
    assert client.post(reverse("dataset_hide", args=[COMPANIES]), json.dumps({"ids": ["cse:ACME"]}), content_type="application/json").status_code == 403
    assert client.post(reverse("dataset_unhide", args=[COMPANIES]), {"record": "cse:ACME"}).status_code == 403
    assert client.post(reverse("dataset_restore", args=[COMPANIES]), {"record": "cse:ACME", "entry": 1}).status_code == 403
    assert client.post(reverse("dataset_review", args=[COMPANIES]), {"record": "cse:ACME", "field": "name", "choice": "keep"}).status_code == 403
    assert company_value() == "ACME PLC"
    page = client.get(reverse("dataset", args=[COMPANIES]))                             # they can still read it, and its history
    config = page.context["config"]
    assert config["canEdit"] is False and config["canHide"] is False and not any(c.get("editable") for c in config["columns"])
    assert client.get(reverse("dataset_history", args=[COMPANIES]), {"id": "cse:ACME"}).status_code == 200


def test_the_page_offers_what_the_catalog_allows(editor):
    config = editor.get(reverse("dataset", args=[COMPANIES])).context["config"]
    assert config["canEdit"] and config["canHide"] and config["hasRecords"]
    assert sorted(c["field"] for c in config["columns"] if c.get("editable")) == ["country", "industry", "name", "website"]
    ds = Dataset.objects.get(schema="companies", name="v_companies")
    Field.objects.filter(dataset=ds, path="industry").update(editable=False)
    assert edit(editor, field="industry", value="x").status_code == 403


def test_columns_outside_the_list_cant_be_edited_even_if_ticked(editor):
    ds = Dataset.objects.get(schema="companies", name="v_companies")
    Field.objects.filter(dataset=ds, path="source").update(editable=True)
    assert edit(editor, field="source", value="x", seen="cse").status_code == 403
    assert edit(editor, field="nonsense", value="x").status_code == 403
    assert company_value("source") == "cse"


def test_other_datasets_cant_be_edited(client, data):
    client.force_login(make_user("ada", "Administrator"))
    assert client.post(reverse("dataset_edit_cell", args=["companies.sites"]), "{}", content_type="application/json").status_code == 403
    assert client.get(reverse("dataset_history", args=["companies.sites"]), {"id": "x"}).status_code == 404
    assert client.get(reverse("dataset_changes", args=["companies.sites"])).status_code == 404
    config = client.get(reverse("dataset", args=["vehicles.ads"])).context["config"]    # a list of the same ads, read-only
    assert config["canEdit"] is False and config["hasRecords"] is False


def test_a_new_scan_gives_editors_the_right_and_nobody_else(data):
    ds = Dataset.objects.get(schema="companies", name="v_companies")
    rights = {r.group.name: (r.can_view, r.can_edit, r.can_hide) for r in ds.rights.select_related("group")}
    assert rights == {"Editor": (True, True, True), "Analyst": (True, False, False), "Viewer": (True, False, False)}
    read_only = Dataset.objects.get(schema="companies", name="sites")
    assert not read_only.rights.filter(can_edit=True).exists() and not read_only.fields.filter(editable=True).exists()
    assert ds.fields.get(path="name").editable and not ds.fields.get(path="source").editable


def test_the_catalog_sets_editable_and_the_rule(client, data):
    admin = make_user("ada", "Administrator")
    client.force_login(admin)
    edit_client = client
    assert edit(edit_client, value="Acme PLC").status_code == 200
    ds = Dataset.objects.get(schema="companies", name="v_companies")
    fields = list(ds.fields.all())
    form = {"label": ds.label, "enabled": "on"}
    for f in fields:
        form[f"label_{f.pk}"] = f.label
        if f.available:
            form[f"available_{f.pk}"] = "on"
        if f.path == "name":
            form[f"editable_{f.pk}"] = "on"
            form[f"rule_{f.pk}"] = "C"
        if f.path == "source":                                    # not correctable: ignored
            form[f"editable_{f.pk}"] = "on"
    page = client.get(reverse("catalog_dataset", args=[ds.pk])).content.decode()
    assert "If the source changes it" in page and "Flag for review" in page
    assert client.post(reverse("catalog_dataset", args=[ds.pk]), form).status_code == 302
    assert ds.fields.get(path="name").edit_rule == "C" and ds.fields.get(path="name").editable
    assert not ds.fields.get(path="industry").editable and not ds.fields.get(path="source").editable
    assert correction()["rule"] == "C"                                                    # corrections already made follow
    assert Field.objects.get(dataset__schema="companies", dataset__name="companies", path="name").edit_rule == "C"


def test_the_rights_page_sets_edit_and_hide(client, data):
    client.force_login(make_user("ada", "Administrator"))
    viewer = Group.objects.get(name="Viewer")
    ds = Dataset.objects.get(schema="companies", name="v_companies")
    assert ">Edit<" in client.get(reverse("dataset_rights")).content.decode()
    form = {f"v_{r.dataset_id}_{r.group_id}": "on" for r in DatasetRight.objects.filter(can_view=True)}
    form[f"c_{ds.pk}_{viewer.pk}"] = "on"
    other = Dataset.objects.get(schema="companies", name="sites")
    form[f"c_{other.pk}_{viewer.pk}"] = "on"                                               # can't be corrected at all: ignored
    assert client.post(reverse("dataset_rights"), form).status_code == 302
    right = DatasetRight.objects.get(dataset=ds, group=viewer)
    assert right.can_edit and not right.can_hide and not DatasetRight.objects.get(dataset=other, group=viewer).can_edit
    assert not DatasetRight.objects.get(dataset=ds, group=Group.objects.get(name="Editor")).can_edit   # not ticked this time


# ── the grid and the panel ───────────────────────────────────────────────────────────────────────────────────
def test_rows_carry_the_key_and_which_cells_were_corrected(editor):
    edit(editor, value="Acme PLC")
    rows = editor.get(reverse("dataset_rows", args=[COMPANIES]), {"cols": json.dumps(["name"])}).json()["rows"]
    by_id = {r["_id"]: r for r in rows}
    assert by_id["cse:ACME"]["_edited"] == ["name"] and by_id["cse:ACME"]["_pending"] == [] and "_edited" not in by_id["cse:BETA"]
    assert "id" not in by_id["cse:ACME"]                                                    # the key travels without being a column


def test_history_panel_and_changes_page(editor):
    edit(editor, value="<b>Acme</b>")
    panel = editor.get(reverse("dataset_history", args=[COMPANIES]), {"id": "cse:ACME"}).content.decode()
    assert "&lt;b&gt;Acme&lt;/b&gt;" in panel and "<b>Acme</b>" not in panel and "Put back" in panel and "ACME PLC" in panel
    assert "as it was collected" in editor.get(reverse("dataset_history", args=[COMPANIES]), {"id": "cse:BETA"}).content.decode()
    changes = editor.get(reverse("dataset_changes", args=[COMPANIES])).content.decode()
    assert "cse:ACME" in changes and "eve" in changes


def test_put_back_only_works_on_this_records_own_history(editor):
    edit(editor, value="Acme PLC")
    entry = history()[0]["id"]
    assert editor.post(reverse("dataset_restore", args=[COMPANIES]), {"record": "cse:BETA", "entry": entry}).status_code == 404
    assert editor.post(reverse("dataset_restore", args=[COMPANIES]), {"record": "cse:ACME", "entry": "x"}).status_code == 404
    assert company_value("name", "cse:BETA") == "BETA PLC"


# ── the database's own limits ────────────────────────────────────────────────────────────────────────────────
def test_the_web_apps_login_can_only_change_the_allowed_columns(pg_env):
    """Real logins, not the tests' superuser: a bug in the app can't damage anything else."""
    import psycopg
    postgres.setup()
    postgres.migrate(schemas=("companies", "vehicles", "news"))
    with psycopg.connect(pg_env["writer"], autocommit=True) as writer:
        writer.execute("""insert into companies.companies (id, source, name) values ('a', 'cse', 'A')""")
    with psycopg.connect(pg_env["app"], autocommit=True) as app:
        app.execute("update companies.companies set name = 'B', website = 'x', domain = 'x' where id = 'a'")
        app.execute("update vehicles.ads set title = 'x', price_lkr = 1 where url = 'none'")
        edits.add_history(app.cursor(), "companies.companies", "a", "name", "A", "B", "eve", "user")
        edits.hide(app.cursor(), "companies.companies", "a", "A", "eve")
        for sql in ("update companies.companies set source = 'x'", "update vehicles.ads set site = 'x'", "delete from companies.companies",
                    "insert into companies.companies (id) values ('n')", "delete from vehicles.ads", "update companies.sites set status = 'x'",
                    "update news.news_items set title = 'x'", "delete from audit.field_history", "update audit.field_history set note = 'x'",
                    "truncate audit.field_history"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute(sql)
    with psycopg.connect(pg_env["reader"], autocommit=True) as reader:
        for table in ("audit.field_history", "audit.field_source", "audit.hidden_records"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                reader.execute(f"select * from {table}")
        assert reader.execute("select count(*) from companies.v_companies").fetchone()[0] == 0   # its view can still leave hidden rows out
    with psycopg.connect(pg_env["writer"], autocommit=True) as writer:                       # not even its owner rewrites the history
        for sql in ("delete from audit.field_history", "update audit.field_history set note = 'x'", "truncate audit.field_history"):
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                writer.execute(sql)


def test_collectors_create_the_audit_tables_themselves(pg_env):
    """Nobody has to remember a step: the first collector run after an update makes them."""
    from companies.store import CompanyStore
    postgres.setup()
    s = CompanyStore()
    try:
        assert s.upsert_companies([row()]) == (1, 0)
        assert s.con.execute("select to_regclass('audit.field_source')").fetchone()[0]
    finally:
        s.close()


def test_without_the_audit_tables_it_says_what_to_do(client, db):
    with connection.cursor() as cur:
        cur.execute("create schema if not exists companies")
        cur.execute("create table companies.companies (id text primary key, name text, source text)")
        cur.execute("insert into companies.companies values ('a', 'A', 's')")
    scan()
    client.force_login(make_user("ada", "Administrator"))
    res = edit(client, record="a", value="B", ref="companies.companies")
    assert res.status_code == 503 and "warehouse migrate" in res.json()["error"]
    assert names_in_grid(client, "companies.companies") == (["A"], 1)                       # reading goes on


def test_the_rules_on_their_own():
    assert edits.decide("A", "x", "y", "x") == "keep" and edits.decide("A", "x", "y", None) == "keep"
    assert edits.decide("A", "x", "y", "z") == "take" and edits.decide("A", "x", "y", "y") == "agree"
    assert edits.decide("B", "x", "y", "z") == "keep_newer" and edits.decide("C", "x", "y", "z") == "flag"
    assert edits.same(5, 5.0) and edits.same("", None) and edits.same(" a ", "a") and not edits.same(0, None) and not edits.same("a", "b")
    assert edits.target_for("news.news_items") is None and edits.hide_target_for("vehicles.ads").key == "url"
