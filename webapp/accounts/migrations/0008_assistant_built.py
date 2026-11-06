"""Phase 7: the AI assistant page is built."""

from django.db import migrations


def forward(apps, schema_editor):
    apps.get_model("accounts", "Menu").objects.filter(code="assistant").update(phase=None)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0007_charts_and_reports_built")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
