"""Tabulator was chosen (2026-11-06), so the grid test no longer compares two grids."""

from django.db import migrations


def forward(apps, schema_editor):
    apps.get_model("accounts", "Menu").objects.filter(code="grid_test").update(
        description="Try the grid (Tabulator): editing, cell blocks, copy and paste.")


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_menus_and_roles")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
