"""The web app, phase 5 (docs/user-platform-plan.md): own tables, own fields on collected data, choices, links, lookups,
rollups and imports. Plan's test: a user links vehicle ads to a dealer table they made and sees the ad count per dealer."""

import io
import json

import pytest
from django.contrib.auth.models import Group, User
from django.db import connection
from django.urls import reverse
from openpyxl import Workbook

from warehouse import postgres
from webapp.audit.models import AuditEvent
from webapp.catalog.models import Dataset, DatasetRight, Field
from webapp.catalog.scan import scan
from webapp.userdata import imports

pytestmark = pytest.mark.django_db
ADS, COMPANIES = "vehicles.v_vehicle_ads_latest", "companies.v_companies"
URL1, URL2, URL3 = "https://ikman.lk/ad/1", "https://ikman.lk/ad/2", "https://ikman.lk/ad/3"


def make_user(name, *roles):
    u = User.objects.create_user(name, password="correct-horse-battery-9")
    u.groups.set(Group.objects.filter(name__in=roles))
    return u


def ad(url=URL1, title="Toyota Axio", price=5_000_000):
    return {"title": title, "url": url, "price": f"Rs {price}", "price_lkr": price, "location": "Colombo"}


def company(id_="cse:ACME", name="ACME PLC"):
    return {"id": id_, "source": "cse", "source_id": id_, "name": name, "country": "LK", "website": "https://acme.lk", "domain": "acme.lk", "industry": "Retail", "extra": {}}


@pytest.fixture
def stores(tmp_path, monkeypatch):
    """The real collectors' stores, on the test's connection."""
    import project_paths
    from companies.store import CompanyStore
    from vehicles.store import VehicleStore
    monkeypatch.setattr(project_paths, "LOCAL_DIR", tmp_path)
    connection.ensure_connection()
    con = connection.connection
    with connection.cursor() as cur:
        cur.execute("create schema if not exists companies; create schema if not exists vehicles")
    postgres.migrate(con, ("companies", "vehicles"))
    c, v = CompanyStore.__new__(CompanyStore), VehicleStore.__new__(VehicleStore)
    c.con = v.con = con
    c.upsert_companies([company(), company("cse:BETA", "BETA PLC")])
    v.save_report({"site": "ikman", "success": True, "ads": [ad(), ad(URL2, "Honda Vezel", 9_000_000), ad(URL3, "Suzuki Alto", 2_000_000)]}, "2026-11-01T00:00:00Z")
    scan()
    return c, v


@pytest.fixture
def eve(client, stores):
    client.force_login(make_user("eve", "Editor"))
    return client


def new_table(client, label="Dealers"):
    res = client.post(reverse("my_tables_new"), {"label": label})
    assert res.status_code == 302, res.content
    return Dataset.objects.get(label=label, schema="userdata")


def add_row(client, ds, **values):
    row = client.post(reverse("dataset_add_row", args=[ds.ref]), "{}", content_type="application/json").json()["row"]["_id"]
    for label, value in values.items():
        assert edit(client, ds, label, row, value).status_code == 200
    return row


def field(ds, label):
    return ds.fields.get(label=label, missing=False)


def edit(client, ds, label, record, value, **extra):
    return client.post(reverse("dataset_edit_cell", args=[ds.ref]), json.dumps({"id": record, "field": field(ds, label).path, "value": value, **extra}), content_type="application/json")


def add_field(client, ds, what, label, **extra):
    res = client.post(reverse("dataset_fields", args=[ds.ref]), {"what": what, "label": label, **extra})
    assert res.status_code == 302, res.content.decode()[:500]
    return field(ds, label)


def link(client, ds, label, records, to):
    return client.post(reverse("dataset_links", args=[ds.ref]), json.dumps({"ids": records, "field": field(ds, label).path, "to": to}), content_type="application/json")


def rows(client, ds, *labels, **params):
    wanted = {field(ds, x).path: x for x in labels}
    for k in ("filters", "sorts"):
        if k in params:
            params[k] = json.dumps([{**item, "field": field(ds, item["field"]).path} for item in params[k]])
    res = client.get(reverse("dataset_rows", args=[ds.ref]), {"cols": json.dumps(list(wanted)), **params})
    assert res.status_code == 200, res.content
    return [{wanted[k]: v for k, v in r.items() if k in wanted} for r in res.json()["rows"]], res.json()["total"]


def dealer_setup(client):
    ads = Dataset.objects.get(schema="vehicles", name="v_vehicle_ads_latest")
    dealers = new_table(client)
    auto, kandy = add_row(client, dealers, Name="Auto Lanka"), add_row(client, dealers, Name="Kandy Motors")
    add_field(client, ads, "records", "Dealer", target=dealers.pk, single="on")
    return ads, dealers, auto, kandy


# ── the plan's test ──────────────────────────────────────────────────────────────────────────────────────────
def test_link_ads_to_a_dealer_table_and_see_the_ad_count_per_dealer(eve, stores):
    client = eve
    _, vehicle = stores
    ads, dealers, auto, kandy = dealer_setup(client)
    assert field(dealers, "Vehicle ads").link_field == field(ads, "Dealer")               # the other side appeared by itself
    add_field(client, dealers, "rollup", "Ad count", link=field(dealers, "Vehicle ads").pk, how="count")
    add_field(client, dealers, "rollup", "Total asking", link=field(dealers, "Vehicle ads").pk, how="sum", target_field=field(ads, "Price LKR").pk)
    add_field(client, dealers, "lookup", "Asking prices", link=field(dealers, "Vehicle ads").pk, target_field=field(ads, "Price LKR").pk)
    assert link(client, ads, "Dealer", [URL1, URL2], [auto]).json()["changed"] == 2
    assert link(client, ads, "Dealer", [URL3], [kandy]).status_code == 200
    found, total = rows(client, dealers, "Name", "Vehicle ads", "Ad count", "Total asking", "Asking prices")
    assert total == 2
    assert found[0] == {"Name": "Auto Lanka", "Vehicle ads": ["Toyota Axio", "Honda Vezel"], "Ad count": 2, "Total asking": 14_000_000, "Asking prices": [5_000_000, 9_000_000]}
    assert found[1]["Ad count"] == 1 and found[1]["Total asking"] == 2_000_000
    assert rows(client, ads, "Dealer", filters=[{"field": "Dealer", "value": "auto"}])[1] == 2
    # and it holds when the collector runs again and a price changes
    vehicle.save_report({"site": "ikman", "success": True, "ads": [ad(price=4_000_000)]}, "2026-11-02T00:00:00Z")
    assert rows(client, dealers, "Name", "Ad count", "Total asking")[0][0] == {"Name": "Auto Lanka", "Ad count": 2, "Total asking": 13_000_000}


def test_rollups_can_be_filtered_and_sorted(eve):
    ads, dealers, auto, kandy = dealer_setup(eve)
    add_field(eve, dealers, "rollup", "Ad count", link=field(dealers, "Vehicle ads").pk, how="count")
    add_row(eve, dealers, Name="No Ads")
    link(eve, ads, "Dealer", [URL1, URL2], [kandy])
    link(eve, ads, "Dealer", [URL3], [auto])
    found, total = rows(eve, dealers, "Name", filters=[{"field": "Ad count", "value": ">0"}], sorts=[{"field": "Ad count", "dir": "desc"}])
    assert total == 2 and [r["Name"] for r in found] == ["Kandy Motors", "Auto Lanka"]


def test_a_link_can_change_from_either_side(eve):
    ads, dealers, auto, kandy = dealer_setup(eve)
    reverse_side = "Vehicle ads"
    assert link(eve, dealers, reverse_side, [auto], [URL1, URL2]).json()["labels"] == ["Toyota Axio", "Honda Vezel"]
    assert rows(eve, ads, "Title", "Dealer", filters=[{"field": "Title", "value": "Axio"}])[0][0]["Dealer"] == ["Auto Lanka"]
    assert link(eve, dealers, reverse_side, [kandy], [URL1]).status_code == 200            # one dealer per ad: it moves
    assert rows(eve, dealers, "Name", "Vehicle ads")[0][0]["Vehicle ads"] == ["Honda Vezel"]
    assert link(eve, ads, "Dealer", [URL2], [auto, kandy]).status_code == 400              # one row only, from this side too
    assert link(eve, ads, "Dealer", [URL2], ["999"]).status_code == 404
    assert link(eve, ads, "Dealer", [URL2], []).json()["changed"] == 1


def test_the_picker_searches_and_shows_whats_chosen(eve):
    ads, dealers, auto, _ = dealer_setup(eve)
    f = field(ads, "Dealer")
    options = eve.get(reverse("dataset_link_options", args=[ads.ref]), {"field": f.path, "q": "kandy"}).json()["options"]
    assert [o["label"] for o in options] == ["Kandy Motors"]
    other_way = eve.get(reverse("dataset_link_options", args=[dealers.ref]), {"field": field(dealers, "Vehicle ads").path, "q": "AXIO"}).json()["options"]
    assert other_way == [{"id": URL1, "label": "Toyota Axio"}]
    link(eve, ads, "Dealer", [URL1], [auto])
    now = eve.get(reverse("dataset_link_values", args=[ads.ref]), {"field": f.path, "id": URL1}).json()["rows"]
    assert now == [{"id": auto, "label": "Auto Lanka"}]


def test_renaming_a_row_updates_links_to_it(eve):
    ads, dealers, auto, _ = dealer_setup(eve)
    link(eve, ads, "Dealer", [URL1], [auto])
    edit(eve, dealers, "Name", auto, "Auto Lanka (Pvt) Ltd")
    assert rows(eve, ads, "Dealer", filters=[{"field": "Title", "value": "Axio"}])[0][0]["Dealer"] == ["Auto Lanka (Pvt) Ltd"]


def test_hidden_or_removed_rows_dont_count(eve):
    ads, dealers, auto, kandy = dealer_setup(eve)
    add_field(eve, dealers, "rollup", "Ad count", link=field(dealers, "Vehicle ads").pk, how="count")
    link(eve, ads, "Dealer", [URL1, URL2], [auto])
    eve.post(reverse("dataset_hide", args=[ads.ref]), json.dumps({"ids": [URL1]}), content_type="application/json")
    assert rows(eve, dealers, "Ad count")[0][0]["Ad count"] == 1
    assert eve.post(reverse("dataset_hide", args=[dealers.ref]), json.dumps({"ids": [auto]}), content_type="application/json").json()["hidden"] == 1
    assert rows(eve, dealers, "Name") == ([{"Name": "Kandy Motors"}], 1)
    assert rows(eve, ads, "Dealer", filters=[{"field": "Title", "value": "Vezel"}])[0][0]["Dealer"] is None
    assert "Auto Lanka" in eve.get(reverse("dataset_changes", args=[dealers.ref])).content.decode()
    eve.post(reverse("dataset_unhide", args=[dealers.ref]), {"record": auto})
    assert rows(eve, dealers, "Name")[1] == 2


# ── own fields beside collected data ─────────────────────────────────────────────────────────────────────────
def test_own_fields_on_collected_data_survive_the_collector(eve, stores):
    client = eve
    company_store, _ = stores
    ds = Dataset.objects.get(schema="companies", name="v_companies")
    add_field(client, ds, "choice", "Status", choices="New, Called", choices_sent="1", allow_new="on")
    add_field(client, ds, "multi", "Tags", choices="bank", choices_sent="1", allow_new="on")
    add_field(client, ds, "number", "Score")
    assert edit(client, ds, "Status", "cse:ACME", "called").json()["value"] == "Called"       # the list's spelling
    assert edit(client, ds, "Tags", "cse:ACME", ["bank", "Sri Lanka"]).json()["value"] == ["bank", "Sri Lanka"]
    assert edit(client, ds, "Score", "cse:ACME", "7").json()["value"] == 7
    company_store.upsert_companies([company(name="ACME HOLDINGS")])                            # the source renames it
    found, _ = rows(client, ds, "Name", "Status", "Tags", "Score", filters=[{"field": "Status", "value": ["Called"]}])
    assert found == [{"Name": "ACME HOLDINGS", "Status": "Called", "Tags": ["bank", "Sri Lanka"], "Score": 7}]
    assert rows(client, ds, "Name", filters=[{"field": "Tags", "value": ["bank"]}])[1] == 1
    assert rows(client, ds, "Name", filters=[{"field": "Status", "value": ["(empty)"]}])[1] == 1
    assert rows(client, ds, "Name", filters=[{"field": "Score", "value": ">5"}])[1] == 1
    assert [r["Name"] for r in rows(client, ds, "Name", sorts=[{"field": "Score", "dir": "desc"}])[0]][0] == "ACME HOLDINGS"
    assert edit(client, ds, "Status", "cse:NOPE", "New").status_code == 404                    # no such company
    # a new scan leaves them alone
    scan()
    assert not field(ds, "Status").missing and Dataset.objects.get(pk=ds.pk).missing is False


def test_scan_leaves_own_tables_alone(eve):
    ds = new_table(eve)
    scan()
    ds.refresh_from_db()
    assert not ds.missing and not field(ds, "Name").missing


def test_a_dataset_without_a_key_cant_have_fields(client, stores):
    client.force_login(make_user("ada", "Administrator"))
    assert client.get(reverse("dataset_fields", args=["vehicles.ads"])).status_code == 404


# ── values ───────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("what,value,saved", [
    ("text", " a  b ", "a  b"), ("number", "1,234.5", 1234.5), ("money", "Rs 1,500", 1500), ("date", "2026-11-06", "2026-11-06"),
    ("bool", "yes", True), ("bool", False, False), ("link", "acme.lk", "https://acme.lk"), ("email", "a@b.lk", "a@b.lk"), ("multi", "a, b, A", ["a", "b"]),
    ("text", "", None), ("number", "", None), ("multi", [], None),
])
def test_values_are_tidied(eve, what, value, saved):
    ds = new_table(eve)
    row = add_row(eve, ds)
    add_field(eve, ds, what, "F", **({"choices_sent": "1", "allow_new": "on"} if what == "multi" else {}))
    res = edit(eve, ds, "F", row, value)
    assert res.status_code == 200 and res.json()["value"] == saved


@pytest.mark.parametrize("what,value,problem", [
    ("number", "abc", "a number"), ("money", "x", "a number"), ("date", "tomorrow", "a date"), ("bool", "maybe", "yes or no"),
    ("link", "javascript:alert(1)", "web address"), ("email", "nope", "email"), ("text", "x" * 5001, "at most"), ("text", {"a": 1}, "kind of value"), ("text", ["a"], "one value"),
])
def test_bad_values_are_refused(eve, what, value, problem):
    ds = new_table(eve)
    row = add_row(eve, ds)
    add_field(eve, ds, what, "F")
    res = edit(eve, ds, "F", row, value)
    assert res.status_code == 400 and problem in res.json()["error"]


def test_choices_people_can_add_and_ones_they_cant(eve):
    ds = new_table(eve)
    row = add_row(eve, ds)
    open_f = add_field(eve, ds, "choice", "Open", choices="A, B", choices_sent="1", allow_new="on")
    add_field(eve, ds, "choice", "Closed", choices="X, Y", choices_sent="1")
    assert edit(eve, ds, "Open", row, "brand new").json()["value"] == "brand new"
    open_f.refresh_from_db()
    assert open_f.values == ["A", "B", "brand new"]
    assert edit(eve, ds, "Closed", row, "Z").status_code == 400
    res = eve.post(reverse("dataset_choice", args=[ds.ref]), json.dumps({"field": open_f.path, "value": "NEW  one"}), content_type="application/json")
    assert res.json()["choices"] == ["A", "B", "brand new", "NEW one"]
    again = eve.post(reverse("dataset_choice", args=[ds.ref]), json.dumps({"field": open_f.path, "value": "new ONE"}), content_type="application/json")
    assert again.json()["value"] == "NEW one" and len(again.json()["choices"]) == 4
    assert eve.post(reverse("dataset_choice", args=[ds.ref]), json.dumps({"field": open_f.path, "value": "a,b"}), content_type="application/json").status_code == 400


def test_conflict_check_history_and_put_back(eve):
    ds = new_table(eve)
    row = add_row(eve, ds, Name="First")
    res = edit(eve, ds, "Name", row, "Other", seen="Not what it says")
    assert res.status_code == 409 and res.json()["current"] == "First"
    edit(eve, ds, "Name", row, "Second", seen="First")
    panel = eve.get(reverse("dataset_history", args=[ds.ref]), {"id": row}).content.decode()
    assert "Second" in panel and "Put back" in panel and "Name" in panel
    with connection.cursor() as cur:
        from warehouse import edits
        entry = edits.history_of(cur, ds.ref, row)[0]["id"]
    back = eve.post(reverse("dataset_restore", args=[ds.ref]), {"record": row, "entry": entry})
    assert back.status_code == 200 and rows(eve, ds, "Name")[0][0]["Name"] == "First"
    assert json.loads(back["HX-Trigger"])["cellsChanged"]["changes"] == {field(ds, "Name").path: "First"}


def test_bulk_edit_on_own_fields(eve):
    ds = new_table(eve)
    a, b = add_row(eve, ds), add_row(eve, ds)
    add_field(eve, ds, "choice", "Stage", choices="New", choices_sent="1", allow_new="on")
    res = eve.post(reverse("dataset_edit_bulk", args=[ds.ref]), json.dumps({"ids": [a, b], "field": field(ds, "Stage").path, "value": "New"}), content_type="application/json")
    assert res.json()["changed"] == 2 and rows(eve, ds, "Stage", filters=[{"field": "Stage", "value": ["New"]}])[1] == 2


def test_odd_names_cant_change_the_sql(eve):
    ds = new_table(eve, "Robert'); drop table x;--")
    add_field(eve, ds, "text", "%s \" ' ; --")
    row = add_row(eve, ds, Name="'; delete from userdata.records; --")
    edit(eve, ds, "%s \" ' ; --", row, "%(x)s ' \"")
    found, total = rows(eve, ds, "Name", "%s \" ' ; --", q="delete")
    assert total == 1 and found[0]["%s \" ' ; --"] == "%(x)s ' \""


# ── fields ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_field_names_must_differ_and_removing_takes_dependents(eve):
    ads, dealers, auto, _ = dealer_setup(eve)
    res = eve.post(reverse("dataset_fields", args=[dealers.ref]), {"what": "text", "label": "name"})
    assert res.status_code == 200 and "already has a field" in res.content.decode()
    count = add_field(eve, dealers, "rollup", "Ad count", link=field(dealers, "Vehicle ads").pk, how="count")
    with_price = add_field(eve, dealers, "lookup", "Prices", link=field(dealers, "Vehicle ads").pk, target_field=field(ads, "Price LKR").pk)
    link(eve, ads, "Dealer", [URL1], [auto])
    removing = reverse("dataset_field_change", args=[ads.ref, field(ads, "Dealer").pk])
    assert "Remove the field" in eve.post(removing, {"action": "remove"}).content.decode()   # asks first
    assert eve.post(removing, {"action": "remove", "confirmed": "yes"}).status_code == 302
    for f in (count, with_price, Field.objects.get(dataset=dealers, label="Vehicle ads")):
        f.refresh_from_db()
        assert f.missing
    assert rows(eve, dealers, "Name")[1] == 2
    assert "Dealer" not in [c["title"] for c in eve.get(reverse("dataset", args=[ads.ref])).context["config"]["columns"]]


def test_rollups_and_lookups_need_the_right_parts(eve):
    ads, dealers, _, _ = dealer_setup(eve)
    via = field(dealers, "Vehicle ads")
    for extra in ({"how": "sum", "link": via.pk, "target_field": field(ads, "Title").pk}, {"how": "nonsense", "link": via.pk}, {"how": "count", "link": field(dealers, "Name").pk}):
        res = eve.post(reverse("dataset_fields", args=[dealers.ref]), {"what": "rollup", "label": "R", **extra})
        assert res.status_code == 200 and not dealers.fields.filter(label="R").exists()
    options = eve.get(reverse("dataset_field_options", args=[dealers.ref]), {"what": "rollup", "how": "sum", "link": via.pk}).content.decode()
    assert "Price LKR" in options and ">Title<" not in options
    assert "Add a link field first" in eve.get(reverse("dataset_field_options", args=[new_table(eve, 'Plain').ref]), {"what": "lookup"}).content.decode()


def test_rename_change_choices_delete_table(eve):
    ds = new_table(eve, "Temp")
    stage = add_field(eve, ds, "choice", "Stage", choices="A", choices_sent="1", allow_new="on")
    eve.post(reverse("dataset_field_change", args=[ds.ref, stage.pk]), {"label": "Step", "choices": "A, B"})
    stage.refresh_from_db()
    assert stage.label == "Step" and stage.values == ["A", "B"] and stage.allow_new is False
    assert eve.post(reverse("dataset_delete_table", args=[ds.ref]), {"confirm": "wrong"}).status_code == 302
    ds.refresh_from_db()
    assert not ds.missing
    eve.post(reverse("dataset_delete_table", args=[ds.ref]), {"confirm": "temp"})
    ds.refresh_from_db()
    assert ds.missing
    assert ds not in eve.get(reverse("my_tables")).context["mine"]


# ── imports ──────────────────────────────────────────────────────────────────────────────────────────────────
CSV = ("Dealer,Phone,Rating,Since,Trusted,Site,Mail,Stage\n"
       + "\n".join(f"Dealer {i},011234567{i},{i}.5,2026-01-0{i + 1},yes,https://d{i}.lk,d{i}@x.lk,{'New' if i % 2 else 'Old'}" for i in range(9)) + "\n,,,,,,,\nLast,0112,oops,2026-02-01,no,https://l.lk,l@x.lk,New\n")


def upload(client, name, raw, url=None):
    return client.post(url or reverse("my_tables_upload"), {"file": _file(name, raw)})


def _file(name, raw):
    from django.core.files.uploadedfile import SimpleUploadedFile
    return SimpleUploadedFile(name, raw)


def test_a_csv_becomes_a_table(eve):
    res = upload(eve, "dealers.csv", CSV.encode())
    assert res.status_code == 302
    preview = eve.get(res.url)
    guessed = {c["heading"]: c["type"] for c in preview.context["columns"]}
    assert guessed["Phone"] == "text"                                                  # a leading zero isn't a number
    assert guessed["Since"] == "date" and guessed["Trusted"] == "bool" and guessed["Site"] == "link" and guessed["Mail"] == "email" and guessed["Stage"] == "choice"
    assert guessed["Rating"] == "text"                                                 # one value isn't a number: text loses nothing
    assert preview.context["rows_count"] == 10                                         # the empty line isn't a row
    form = {"label": "From file"}
    for c in preview.context["columns"]:
        form.update({f"use_{c['index']}": "on", f"name_{c['index']}": c["heading"], f"type_{c['index']}": c["type"]})
    form["type_2"] = "number"                                                           # "oops" won't fit
    form.pop("use_6")                                                                   # leave out Mail
    done = eve.post(res.url, form)
    ds = Dataset.objects.get(label="From file")
    assert done.status_code == 302 and done.url == reverse("dataset", args=[ds.ref])
    found, total = rows(eve, ds, "Dealer", "Phone", "Rating", "Since", "Trusted", "Stage")
    assert total == 10 and found[1] == {"Dealer": "Dealer 1", "Phone": "0112345671", "Rating": 1.5, "Since": "2026-01-02", "Trusted": True, "Stage": "New"}
    assert found[9]["Rating"] is None and found[9]["Dealer"] == "Last"
    assert sorted(field(ds, "Stage").values) == ["New", "Old"]
    assert not ds.fields.filter(label="Mail").exists() and Dataset.objects.get(pk=ds.pk).row_count == 10
    event = AuditEvent.objects.get(action="rows_imported")
    assert event.detail["rows"] == 10 and event.detail["bad"] == 1
    assert eve.get(res.url).status_code == 302                                         # the kept file is gone


def test_an_excel_file_and_adding_to_an_existing_table(eve):
    book = Workbook()
    sheet = book.active
    sheet.append(["Name", "City", "Ignored"])
    sheet.append(["Auto Lanka", "Colombo", "x"])
    sheet.append(["Kandy Motors", "Kandy", "y"])
    raw = io.BytesIO()
    book.save(raw)
    ds = new_table(eve)
    add_field(eve, ds, "text", "City")
    add_row(eve, ds, Name="Already here")
    res = upload(eve, "more.xlsx", raw.getvalue(), reverse("dataset_add_from_file", args=[ds.ref]))
    preview = eve.get(res.url)
    assert [c["match"] for c in preview.context["columns"]] == [field(ds, "Name").pk, field(ds, "City").pk, None]
    eve.post(res.url, {"into_0": field(ds, "Name").pk, "into_1": field(ds, "City").pk, "into_2": ""})
    found, total = rows(eve, ds, "Name", "City")
    assert total == 3 and found[2] == {"Name": "Kandy Motors", "City": "Kandy"}


@pytest.mark.parametrize("name,raw,problem", [
    ("a.pdf", b"%PDF", "Choose a .csv"), ("a.csv", b"only,headings\n", "no rows"), ("a.xlsx", b"not a workbook", "can't be read"),
])
def test_files_that_cant_be_read_say_why(eve, name, raw, problem):
    res = upload(eve, name, raw)
    assert res.status_code == 302 and res.url == reverse("my_tables")
    assert problem in eve.get(res.url).content.decode().replace("&#x27;", "'")


def test_a_kept_file_is_its_uploaders(eve, client):
    res = upload(eve, "dealers.csv", CSV.encode())
    other = make_user("otto", "Editor")
    eve.force_login(other)
    assert eve.get(res.url).url == reverse("my_tables")
    assert eve.post(reverse("my_tables_import", args=["no-such-file"])).status_code == 302


def test_reading_files(tmp_path):
    headings, found = imports.read_file("x.csv", "a;b\n1;é\n".encode("cp1252"))
    assert headings == ["a", "b"] and found == [["1", "é"]]
    assert imports.read_file("x.csv", b"a,a,\n1,2,3\n")[0] == ["a", "a 2", "Column 3"]
    assert imports.guess_type(["x"] * 3) == "text" and imports.guess_type(["New", "Old"] * 6) == "choice" and imports.guess_type([1, "2", None]) == "number"
    with pytest.raises(imports.ImportProblem, match="at most"):
        imports.read_file("x.csv", ("a\n" + "1\n" * (imports.MAX_ROWS + 1)).encode())


def test_save_what_is_on_screen_as_a_table(eve):
    ads = Dataset.objects.get(schema="vehicles", name="v_vehicle_ads_latest")
    cols = [field(ads, x).path for x in ("Title", "Price LKR", "First seen")]
    url = reverse("dataset_save_table", args=[ads.ref]) + "?" + "&".join([f"cols={json.dumps(cols)}", "sorts=" + json.dumps([{"field": cols[1], "dir": "desc"}]),
                                                                          "filters=" + json.dumps([{"field": cols[1], "value": ">3000000"}])])
    res = eve.post(url, json.dumps({"name": "Dear ads"}), content_type="application/json")
    assert res.status_code == 200 and res.json()["ok"]
    copy = Dataset.objects.get(label="Dear ads")
    found, total = rows(eve, copy, "Title", "Price LKR")
    assert total == 2 and found[0] == {"Title": "Honda Vezel", "Price LKR": 9_000_000}
    assert eve.post(url, json.dumps({"name": "Dear ads"}), content_type="application/json").status_code == 400     # the name is taken


# ── rights ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_table_is_private_until_shared(eve, client):
    ds = new_table(eve)
    row = add_row(eve, ds, Name="Secret")
    eve.logout()
    client.force_login(make_user("vic", "Editor"))
    assert client.get(reverse("dataset", args=[ds.ref])).status_code == 403
    assert client.get(reverse("dataset_rows", args=[ds.ref])).status_code == 403
    page = client.get(reverse("my_tables")).context
    assert ds not in page["mine"] and ds not in page["others"] and "Dealers" not in client.get(reverse("datasets")).content.decode()
    assert edit(client, ds, "Name", row, "x").status_code == 403
    assert client.post(reverse("dataset_share", args=[ds.ref]), {}).status_code == 403
    assert client.post(reverse("dataset_delete_table", args=[ds.ref]), {"confirm": "Dealers"}).status_code == 403
    editor = Group.objects.get(name="Editor")
    DatasetRight.objects.create(dataset=ds, group=editor, can_view=True, can_edit=False)
    client.force_login(User.objects.get(username="vic"))
    assert client.get(reverse("dataset", args=[ds.ref])).context["config"]["canEdit"] is False
    assert edit(client, ds, "Name", row, "x").status_code == 403
    assert "Shared with you" in client.get(reverse("my_tables")).content.decode()


def test_the_owner_shares_it_with_a_role(eve, client):
    ds = new_table(eve)
    viewer, editor = Group.objects.get(name="Viewer"), Group.objects.get(name="Editor")
    page = eve.get(reverse("dataset_fields", args=[ds.ref])).content.decode()
    assert "Who can use it" in page
    eve.post(reverse("dataset_share", args=[ds.ref]), {f"view_{viewer.pk}": "on", f"view_{editor.pk}": "on", f"edit_{editor.pk}": "on"})
    assert DatasetRight.objects.get(dataset=ds, group=viewer).can_edit is False and DatasetRight.objects.get(dataset=ds, group=editor).can_hide is True
    assert AuditEvent.objects.filter(action="table_shared").exists()
    eve.post(reverse("dataset_share", args=[ds.ref]), {})
    assert not DatasetRight.objects.filter(dataset=ds).exists()


def test_a_link_into_a_private_table_stays_hidden_from_others(eve, client):
    ads, dealers, auto, _ = dealer_setup(eve)
    link(eve, ads, "Dealer", [URL1], [auto])
    f = field(ads, "Dealer")
    eve.logout()
    client.force_login(make_user("vic", "Viewer"))
    page = client.get(reverse("dataset", args=[ads.ref]))
    assert page.status_code == 200 and "Dealer" not in [c["title"] for c in page.context["config"]["columns"]]
    assert client.get(reverse("dataset_rows", args=[ads.ref]), {"cols": json.dumps([f.path])}).status_code == 400
    assert client.get(reverse("dataset_link_options", args=[ads.ref]), {"field": f.path}).status_code == 403
    assert client.get(reverse("dataset_link_values", args=[ads.ref]), {"field": f.path, "id": URL1}).status_code == 403


def test_viewers_and_analysts_cant_add_or_make(client, stores):
    ads = Dataset.objects.get(schema="vehicles", name="v_vehicle_ads_latest")
    client.force_login(make_user("vic", "Viewer"))
    assert client.get(reverse("my_tables")).status_code == 403
    assert client.post(reverse("my_tables_new"), {"label": "X"}).status_code == 403
    assert client.post(reverse("dataset_fields", args=[ads.ref]), {"what": "text", "label": "X"}).status_code == 403
    assert client.post(reverse("dataset_links", args=[ads.ref]), "{}", content_type="application/json").status_code == 403
    assert client.get(reverse("dataset_fields", args=[ads.ref])).status_code == 200      # reading is fine
    client.force_login(make_user("ana", "Analyst"))
    assert client.get(reverse("my_tables")).status_code == 403
    config = client.get(reverse("dataset", args=[ads.ref])).context["config"]
    assert config["canMakeTables"] is False and config["canEdit"] is False


def test_linking_needs_sight_of_the_target(eve, client):
    ds = new_table(eve, "Private one")
    client.force_login(make_user("otto", "Editor"))
    other = new_table(client, "Mine")
    res = client.post(reverse("dataset_fields", args=[other.ref]), {"what": "records", "label": "L", "target": ds.pk})
    assert res.status_code == 200 and "only link to a table you can see" in res.content.decode() and not other.fields.filter(label="L").exists()


# ── the grid ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_list_values_come_as_lists(client, stores):
    """On the web app's connection jsonb arrives as text. As text, the grid couldn't draw it and showed no rows at all."""
    with connection.cursor() as cur:
        cur.execute("""insert into companies.sites (domain, status, emails) values ('acme.lk', 'ok', '["a@acme.lk", "b@acme.lk"]')""")
    scan()
    client.force_login(make_user("ada", "Administrator"))
    res = client.get(reverse("dataset_rows", args=["companies.sites"]), {"cols": json.dumps(["emails"])})
    assert res.json()["rows"][0]["emails"] == ["a@acme.lk", "b@acme.lk"]
    csv_text = client.get(reverse("dataset_export", args=["companies.sites", "csv"]), {"cols": json.dumps(["emails"])})
    body = b"".join(csv_text.streaming_content).decode("utf-8-sig")
    assert '"a@acme.lk, b@acme.lk"' in body


def test_the_page_describes_own_columns(eve):
    ads, dealers, _, _ = dealer_setup(eve)
    add_field(eve, dealers, "choice", "Stage", choices="New", choices_sent="1", allow_new="on")
    add_field(eve, dealers, "rollup", "Ad count", link=field(dealers, "Vehicle ads").pk, how="count")
    columns = {c["title"]: c for c in eve.get(reverse("dataset", args=[dealers.ref])).context["config"]["columns"]}
    assert columns["Stage"]["type"] == "choice" and columns["Stage"]["editable"] and columns["Stage"]["choices"] == ["New"] and columns["Stage"]["filter_choices"] == ["(empty)", "New"]
    assert columns["Vehicle ads"]["type"] == "records" and columns["Vehicle ads"]["can_link"] and not columns["Vehicle ads"].get("editable")
    assert columns["Ad count"]["type"] == "number" and not columns["Ad count"].get("editable") and columns["Ad count"]["filter"] == "number"
    assert columns["Added"]["shown"] is False and columns["Name"]["editable"]
    config = eve.get(reverse("dataset", args=[dealers.ref])).context["config"]
    assert config["isOwn"] and config["canEdit"] and config["canHide"] and config["hasRecords"]
    reverse_ = Field.objects.get(dataset=ads, label="Dealer")
    assert reverse_.link_single and Field.objects.get(dataset=dealers, label="Vehicle ads").is_reverse_link


# ── setup ────────────────────────────────────────────────────────────────────────────────────────────────────
def test_setup_gives_the_web_app_its_schema(pg_env):
    import psycopg
    postgres.setup()
    postgres.migrate(schemas=("vehicles",))
    with psycopg.connect(pg_env["app"], autocommit=True) as app:
        app.execute("create table userdata.t (id int)")
        app.execute("create view userdata.v as select id from userdata.t")
        app.execute("select count(*) from audit.hidden_records")
    with psycopg.connect(pg_env["reader"], autocommit=True) as reader:
        reader.execute("select * from userdata.v")                                     # read
        with pytest.raises(psycopg.Error):
            reader.execute("insert into userdata.t values (1)")                        # not change
    with psycopg.connect(pg_env["writer"], autocommit=True) as writer:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):                      # collectors have no business here
            writer.execute("select * from userdata.t")


def test_table_making_says_what_to_do_without_the_audit_tables(client, db):
    client.force_login(make_user("eve", "Editor"))
    res = client.post(reverse("my_tables_new"), {"label": "X"}, follow=True)
    assert "warehouse migrate" in res.content.decode()
