"""Phase 3: the Dashboard page is built (the five tabs of the old dashboard, in Django)."""

from django.db import migrations


def forward(apps, schema_editor):
    apps.get_model("accounts", "Menu").objects.filter(code="dashboard").update(
        phase=None, description="World News, Tech Intel, Markets, Vehicles and Companies at a glance.")


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_catalog_menus")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
