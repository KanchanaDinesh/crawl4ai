"""Phase 2: the Datasets page is built, and administrators get the Catalog menu."""

from django.db import migrations


def forward(apps, schema_editor):
    Menu = apps.get_model("accounts", "Menu")
    Group = apps.get_model("auth", "Group")
    Menu.objects.filter(code="datasets").update(phase=None)
    menu, _ = Menu.objects.update_or_create(code="admin_catalog", defaults=dict(
        label="Catalog", url_name="catalog", section="admin", order=25, phase=None,
        description="What datasets exist, their names and fields."))
    admin = Group.objects.filter(name="Administrator").first()
    if admin:
        menu.roles.add(admin)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_grid_test_description")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
