"""Menu Annual reports, for whoever has Datasets: those roles can already see the dataset Annual reports there. After that it's set on Roles and rights like any other."""

from django.db import migrations


def forward(apps, schema_editor):
    Menu = apps.get_model("accounts", "Menu")
    menu, _ = Menu.objects.update_or_create(code="annualreports", defaults=dict(
        label="Annual reports", url_name="annualreports", section="work", order=34, phase=None,
        description="Read the annual reports in the Anual reports folder: key figures, people, sections, every page and table."))
    datasets = Menu.objects.filter(code="datasets").first()
    if datasets:
        menu.roles.set(datasets.roles.all())


def backward(apps, schema_editor):
    apps.get_model("accounts", "Menu").objects.filter(code="annualreports").delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0011_articles_menu")]
    operations = [migrations.RunPython(forward, backward)]
