"""Menu Articles, for whoever has Datasets: those roles can already see the dataset Tech articles there. After that it's set on Roles and rights like any other."""

from django.db import migrations


def forward(apps, schema_editor):
    Menu = apps.get_model("accounts", "Menu")
    menu, _ = Menu.objects.update_or_create(code="articles", defaults=dict(
        label="Articles", url_name="articles", section="work", order=33, phase=None,
        description="Read and filter articles and blogs from the most valuable companies, tagged by service."))
    datasets = Menu.objects.filter(code="datasets").first()
    if datasets:
        menu.roles.set(datasets.roles.all())


def backward(apps, schema_editor):
    apps.get_model("accounts", "Menu").objects.filter(code="articles").delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0010_foresight_menu")]
    operations = [migrations.RunPython(forward, backward)]
