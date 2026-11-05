"""The starting menus and the four built-in roles with their menu rights (docs/user-platform-plan.md).
Administrators change rights afterwards on the Roles page; this only runs once."""

from django.db import migrations

MENUS = [  # code, label, url name, section, order, phase that builds it (None = there now), description
    ("home", "Home", "home", "work", 10, None, "Start page"),
    ("datasets", "Datasets", "datasets", "work", 20, 2, "Every dataset in a grid: filter, sort, search, export."),
    ("dashboard", "Dashboard", "dashboard", "work", 30, 3, "World news, tech, markets, vehicles and companies."),
    ("my_tables", "My tables", "my_tables", "work", 40, 5, "Your own tables, lists, tags and links between records."),
    ("charts", "Charts", "charts", "work", 50, 6, "Charts and dashboards you build from any dataset."),
    ("reports", "Reports", "reports", "work", 60, 6, "PDF reports, on demand or on a schedule."),
    ("assistant", "AI assistant", "assistant", "work", 70, 7, "Ask for a dataset in plain words."),
    ("grid_test", "Grid test", "grid_test", "work", 80, None, "AG Grid Community and Tabulator side by side."),
    ("admin_users", "Users", "users", "admin", 10, None, "Add users, set roles, deactivate."),
    ("admin_roles", "Roles and rights", "roles", "admin", 20, None, "Roles and which menus each may open."),
    ("admin_audit", "Audit log", "audit", "admin", 30, None, "Who did what, and when."),
]
ROLES = {  # name: (description, menu codes)
    "Administrator": ("Everything, including users, roles and the audit log", [m[0] for m in MENUS]),
    "Editor": ("Views, edits, own tables, charts, reports, AI assistant",
               ["home", "datasets", "dashboard", "my_tables", "charts", "reports", "assistant", "grid_test"]),
    "Analyst": ("Views, charts, reports, AI assistant; doesn't edit collected data",
                ["home", "datasets", "dashboard", "charts", "reports", "assistant", "grid_test"]),
    "Viewer": ("Views and filters", ["home", "datasets", "dashboard"]),
}


def forward(apps, schema_editor):
    Menu = apps.get_model("accounts", "Menu")
    RoleProfile = apps.get_model("accounts", "RoleProfile")
    Group = apps.get_model("auth", "Group")
    menus = {}
    for code, label, url_name, section, order, phase, desc in MENUS:
        menus[code], _ = Menu.objects.update_or_create(code=code, defaults=dict(
            label=label, url_name=url_name, section=section, order=order, phase=phase, description=desc))
    for name, (desc, codes) in ROLES.items():
        group, _ = Group.objects.get_or_create(name=name)
        RoleProfile.objects.update_or_create(group=group, defaults={"description": desc, "builtin": True})
        for code in codes:
            menus[code].roles.add(group)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial"), ("auth", "0012_alter_user_first_name_max_length")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
