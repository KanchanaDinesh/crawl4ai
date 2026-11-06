"""Report templates get their own menu item. Whoever has Reports gets it too; after that it's set on Roles and rights like any other."""

from django.db import migrations


def forward(apps, schema_editor):
    Menu = apps.get_model("accounts", "Menu")
    menu, _ = Menu.objects.update_or_create(code="report_templates", defaults=dict(
        label="Report templates", url_name="report_templates", section="work", order=65, phase=None,
        description="How reports look: colours, fonts, logo and page layout."))
    reports = Menu.objects.filter(code="reports").first()
    if reports:
        menu.roles.set(reports.roles.all())


def backward(apps, schema_editor):
    apps.get_model("accounts", "Menu").objects.filter(code="report_templates").delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0008_assistant_built")]
    operations = [migrations.RunPython(forward, backward)]
