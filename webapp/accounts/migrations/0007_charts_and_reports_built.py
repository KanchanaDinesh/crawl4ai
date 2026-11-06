"""Phase 6: the Charts and Reports pages are built."""

from django.db import migrations


def forward(apps, schema_editor):
    apps.get_model("accounts", "Menu").objects.filter(code__in=["charts", "reports"]).update(phase=None)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0006_my_tables_built")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
