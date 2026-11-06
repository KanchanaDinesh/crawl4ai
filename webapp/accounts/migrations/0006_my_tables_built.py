"""Phase 5: the My tables page is built."""

from django.db import migrations


def forward(apps, schema_editor):
    apps.get_model("accounts", "Menu").objects.filter(code="my_tables").update(phase=None)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0005_dashboard_built")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
