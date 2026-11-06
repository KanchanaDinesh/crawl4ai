"""The web app, phase 1 (docs/user-platform-plan.md): sign-in, menu rights, users and roles screens, the audit log,
and the grid test. Runs on the throwaway PostgreSQL (pytest-django's test database)."""

import json

import pytest
from django.contrib.auth.models import Group, User
from django.db import connection, transaction
from django.db import Error as DatabaseError
from django.urls import reverse

from webapp.accounts.models import Menu
from webapp.audit.models import AuditEvent

pytestmark = pytest.mark.django_db
PW = "correct-horse-battery-9"


def make_user(name, *roles, superuser=False):
    u = User.objects.create_user(name, password=PW, is_superuser=superuser, is_staff=superuser)
    u.groups.set(Group.objects.filter(name__in=roles))
    return u


@pytest.fixture
def admin_client(client):
    client.force_login(make_user("ada", "Administrator"))
    return client


@pytest.fixture
def viewer_client(client):
    client.force_login(make_user("vic", "Viewer"))
    return client


# ── sign in ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_pages_need_a_sign_in(client):
    for name in ("home", "users", "grid_test", "audit"):
        res = client.get(reverse(name))
        assert res.status_code == 302 and res.url.startswith(reverse("login"))


def test_sign_in_and_failures_are_in_the_audit_log(client):
    make_user("sam", "Viewer")
    assert client.post(reverse("login"), {"username": "sam", "password": "wrong-password"}).status_code == 200
    res = client.post(reverse("login"), {"username": "sam", "password": PW})
    assert res.status_code == 302 and res.url == reverse("home")
    actions = list(AuditEvent.objects.order_by("id").values_list("action", "username"))
    assert actions == [("login_failed", "sam"), ("login", "sam")]


def test_five_wrong_passwords_lock_the_account(client):
    make_user("sam", "Viewer")
    for _ in range(5):
        client.post(reverse("login"), {"username": "sam", "password": "wrong-password"}, REMOTE_ADDR="10.0.0.5")
    res = client.post(reverse("login"), {"username": "sam", "password": PW}, REMOTE_ADDR="10.0.0.5")
    assert res.status_code == 429 and b"Too many tries" in res.content
    assert AuditEvent.objects.filter(action="locked_out", username="sam").exists()


# ── menu rights ──────────────────────────────────────────────────────────────────────────────────────────────
def test_the_menu_shows_only_what_the_roles_allow(viewer_client):
    page = viewer_client.get(reverse("home")).content.decode()
    assert "Datasets" in page and "Dashboard" in page
    assert "Users" not in page and "Audit log" not in page and "Grid test" not in page
    assert viewer_client.get(reverse("users")).status_code == 403          # the page itself checks too
    assert viewer_client.get(reverse("grid_test")).status_code == 403


def test_rights_add_up_over_roles(client):
    client.force_login(make_user("max", "Viewer", "Analyst"))
    assert client.get(reverse("charts")).status_code == 200               # from Analyst
    assert client.get(reverse("dashboard")).status_code == 200            # from Viewer
    assert client.get(reverse("users")).status_code == 403


def test_admin_changes_menu_rights_and_keeps_the_admin_menus(admin_client):
    viewer, admin = Group.objects.get(name="Viewer"), Group.objects.get(name="Administrator")
    grid, users = Menu.objects.get(code="grid_test"), Menu.objects.get(code="admin_users")
    current = {f"r_{m.pk}_{g.pk}": "on" for m in Menu.objects.all() for g in m.roles.all()}
    current[f"r_{grid.pk}_{viewer.pk}"] = "on"                              # give Viewer the grid test
    current.pop(f"r_{users.pk}_{admin.pk}")                                 # try to take Users from Administrator
    assert admin_client.post(reverse("rights_save"), current).status_code == 302
    assert viewer in grid.roles.all() and admin in users.roles.all()
    event = AuditEvent.objects.get(action="rights_changed")
    assert "grid_test +Viewer" in event.summary


# ── users and roles ──────────────────────────────────────────────────────────────────────────────────────────
def test_admin_adds_a_user_with_roles(admin_client):
    short = admin_client.post(reverse("user_new"), {"username": "new", "password": "short", "is_active": "on"})
    assert short.status_code == 200 and not User.objects.filter(username="new").exists()
    editor = Group.objects.get(name="Editor")
    res = admin_client.post(reverse("user_new"), {"username": "new", "first_name": "Nimal", "email": "n@example.lk",
                                                  "password": PW, "is_active": "on", "roles": [editor.pk]})
    assert res.status_code == 302
    u = User.objects.get(username="new")
    assert u.check_password(PW) and list(u.groups.values_list("name", flat=True)) == ["Editor"]
    assert u.password.startswith("argon2")
    assert AuditEvent.objects.filter(action="user_created", target_id=str(u.pk)).exists()


def test_an_admin_cant_lock_themselves_out(admin_client):
    me = User.objects.get(username="ada")
    res = admin_client.post(reverse("user_edit", args=[me.pk]), {"username": "ada", "roles": []})
    assert res.status_code == 200 and b"deactivate your own account" in res.content
    res = admin_client.post(reverse("user_edit", args=[me.pk]), {"username": "ada", "is_active": "on", "roles": []})
    assert b"remove your own Administrator role" in res.content
    assert me.groups.filter(name="Administrator").exists()


def test_deactivating_is_recorded_and_stops_sign_in(admin_client, client):
    bob = make_user("bob", "Viewer")
    admin_client.post(reverse("user_edit", args=[bob.pk]), {"username": "bob", "roles": []})
    bob.refresh_from_db()
    assert not bob.is_active and AuditEvent.objects.filter(action="user_deactivated").exists()


def test_roles_builtin_and_with_members_cant_be_deleted(admin_client):
    viewer = Group.objects.get(name="Viewer")
    admin_client.post(reverse("role_delete", args=[viewer.pk]))
    assert Group.objects.filter(name="Viewer").exists()
    admin_client.post(reverse("role_new"), {"name": "Vehicles team", "description": "Car ads only"})
    team = Group.objects.get(name="Vehicles team")
    make_user("cara", "Vehicles team")
    admin_client.post(reverse("role_delete", args=[team.pk]))
    assert Group.objects.filter(name="Vehicles team").exists()
    team.user_set.clear()
    admin_client.post(reverse("role_delete", args=[team.pk]))
    assert not Group.objects.filter(name="Vehicles team").exists()
    assert {"role_created", "role_deleted"} <= set(AuditEvent.objects.values_list("action", flat=True))


def test_the_audit_log_page_and_it_cant_be_changed(admin_client):
    admin_client.post(reverse("role_new"), {"name": "Temp", "description": ""})
    page = admin_client.get(reverse("audit"), {"action": "role_created"}).content.decode()
    assert "Role Temp created" in page
    event = AuditEvent.objects.get(action="role_created")
    with pytest.raises(DatabaseError, match="only grows"), transaction.atomic():
        AuditEvent.objects.filter(pk=event.pk).update(summary="changed")
    with pytest.raises(DatabaseError, match="only grows"), transaction.atomic():
        AuditEvent.objects.filter(pk=event.pk).delete()


def test_createadmin_command(monkeypatch):
    from django.core.management import call_command
    answers = iter([PW, PW])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    call_command("createadmin", username="root")
    u = User.objects.get(username="root")
    assert u.is_superuser and u.groups.filter(name="Administrator").exists()


# ── grid test ────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def companies():
    """The companies schema (warehouse/schema/companies.sql) with a few rows, in the test database."""
    from pathlib import Path
    folder = Path(__file__).resolve().parent.parent / "warehouse" / "schema"
    sql = (folder / "audit.sql").read_text() + (folder / "companies.sql").read_text()     # its view looks at audit's table
    with connection.cursor() as cur:
        cur.execute("create schema if not exists companies")
        cur.execute("set local search_path = app, public")      # pg_trgm's operators live in public
        cur.execute(sql)
        cur.execute("""insert into companies.companies (id, source, name, country, website, domain, industry, first_seen)
            values ('cse:JKH', 'cse', 'John Keells Holdings', 'LK', 'https://keells.com', 'keells.com', 'Capital Goods', now()),
                   ('wikidata:Q1', 'wikidata', 'Acme Bank', 'US', 'https://acme.com', 'acme.com', 'banking', now()),
                   ('gleif:L1', 'gleif', 'Bank of Ceylon', 'LK', null, null, null, now())""")


def test_grid_rows_filter_sort_and_page(admin_client, companies):
    url = reverse("grid_test_rows")
    data = admin_client.get(url, {"country": "LK", "sort": "name", "dir": "desc"}).json()
    assert data["total"] == 2 and [r["name"] for r in data["rows"]] == ["John Keells Holdings", "Bank of Ceylon"]
    data = admin_client.get(url, {"q": "bank", "start": 1, "limit": 1, "sort": "name"}).json()
    assert data["total"] == 2 and [r["name"] for r in data["rows"]] == ["Bank of Ceylon"]
    assert admin_client.get(url, {"sort": "name; drop table x"}).status_code == 200   # unknown sort: default


def test_grid_edits_are_kept_apart_and_audited(admin_client, companies):
    edit, bulk = reverse("grid_test_edit"), reverse("grid_test_bulk")
    post = lambda url, body: admin_client.post(url, json.dumps(body), content_type="application/json")
    assert post(edit, {"id": "cse:JKH", "field": "tags", "value": ["Nope"]}).status_code == 400
    assert post(edit, {"id": "cse:JKH", "field": "name", "value": "X"}).status_code == 400   # not editable
    assert post(edit, {"id": "cse:JKH", "field": "industry", "value": "Conglomerate", "engine": "tabulator"}).json()["ok"]
    res = post(bulk, {"ids": ["cse:JKH", "gleif:L1"], "field": "tags", "value": ["Priority", "Partner"]}).json()
    assert res["count"] == 2 and res["value"] == ["Partner", "Priority"]      # in the list's order
    rows = {r["id"]: r for r in admin_client.get(reverse("grid_test_rows"), {"country": "LK"}).json()["rows"]}
    assert rows["cse:JKH"]["industry"] == "Conglomerate" and rows["gleif:L1"]["tags"] == ["Partner", "Priority"]
    with connection.cursor() as cur:                                         # the collected data is untouched
        cur.execute("select industry from companies.companies where id = 'cse:JKH'")
        assert cur.fetchone()[0] == "Capital Goods"
    assert set(AuditEvent.objects.values_list("action", flat=True)) >= {"gridtest_edit", "gridtest_bulk_edit"}


def test_grid_endpoints_check_the_menu_right(viewer_client, companies):
    assert viewer_client.get(reverse("grid_test_rows")).status_code == 403
    res = viewer_client.post(reverse("grid_test_edit"), json.dumps({"id": "cse:JKH", "field": "notes", "value": "x"}),
                             content_type="application/json")
    assert res.status_code == 403


def test_grid_page_uses_tabulator_with_a_long_tag_list(admin_client, companies):
    page = admin_client.get(reverse("grid_test")).content.decode()
    assert "tabulator.min.js" in page and "datagrid.js" in page and "ag-grid" not in page
    config = json.loads(page.split('id="grid-config" type="application/json">')[1].split("</script>")[0])
    assert next(c for c in config["columns"] if c["field"] == "country")["choices"] == ["LK", "US"]
    tags = next(c for c in config["columns"] if c["field"] == "tags")
    assert tags["type"] == "multi" and tags["noun"] == "tag" and tags["allow_new"] and len(tags["choices"]) > 20


def test_users_can_add_a_new_tag_and_everyone_can_use_it(admin_client, client, companies):
    url = reverse("grid_test_choice")
    post = lambda c, target, body: c.post(target, json.dumps(body), content_type="application/json")
    res = post(admin_client, url, {"field": "tags", "value": "  Sri   Lankan brand "}).json()
    assert res["value"] == "Sri Lankan brand" and "Sri Lankan brand" in res["choices"]
    assert res["choices"] == sorted(res["choices"], key=str.casefold)
    event = AuditEvent.objects.get(action="gridtest_choice_added")
    assert "Sri Lankan brand" in event.summary and event.user.username == "ada"

    again = post(admin_client, url, {"field": "tags", "value": "sri lankan BRAND"}).json()       # one, in any capitals
    assert again["value"] == "Sri Lankan brand" and again["choices"] == res["choices"]
    assert post(admin_client, url, {"field": "tags", "value": "priority"}).json()["value"] == "Priority"
    assert AuditEvent.objects.filter(action="gridtest_choice_added").count() == 1

    saved = post(admin_client, reverse("grid_test_edit"),
                 {"id": "cse:JKH", "field": "tags", "value": ["Sri Lankan brand", "Priority"]}).json()
    assert saved["value"] == ["Priority", "Sri Lankan brand"]
    assert "Sri Lankan brand" in admin_client.get(reverse("grid_test")).content.decode()

    other = make_user("eve", "Analyst")                                 # another user sees it and can use it
    client.force_login(other)
    assert post(client, reverse("grid_test_edit"), {"id": "gleif:L1", "field": "tags", "value": ["Sri Lankan brand"]}).json()["ok"]


def test_a_new_tag_must_be_sensible(admin_client):
    url = reverse("grid_test_choice")
    post = lambda body: admin_client.post(url, json.dumps(body), content_type="application/json")
    for bad in ("", "   ", "a, b", "x" * 41):
        assert post({"field": "tags", "value": bad}).status_code == 400
    assert post({"field": "industry", "value": "Mining"}).status_code == 400      # that list takes no new choices
    assert post({"field": "nope", "value": "x"}).status_code == 400
    res = admin_client.post(reverse("grid_test_edit"), json.dumps({"id": "x", "field": "tags", "value": ["Not added"]}),
                            content_type="application/json")
    assert res.status_code == 400 and "add it there first" in res.json()["error"]


def test_adding_a_tag_needs_the_menu_right(viewer_client):
    res = viewer_client.post(reverse("grid_test_choice"), json.dumps({"field": "tags", "value": "x"}),
                             content_type="application/json")
    assert res.status_code == 403
