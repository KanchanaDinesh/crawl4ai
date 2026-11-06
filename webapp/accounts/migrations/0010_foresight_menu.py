"""Menu Foresight: administrators only at first (the plan's choice); after that it's set on Roles and rights like any other.
Its raw tables may already have been scanned into the catalog, so they're tidied here: new ones get this from catalog/defaults.py."""

from django.db import migrations


def forward(apps, schema_editor):
    Menu, Group, Dataset = apps.get_model("accounts", "Menu"), apps.get_model("auth", "Group"), apps.get_model("catalog", "Dataset")
    menu, _ = Menu.objects.update_or_create(code="foresight", defaults=dict(
        label="Foresight", url_name="foresight", section="work", order=35, phase=None,
        description="What could happen next, with odds and reasons, and how right it has been."))
    menu.roles.set(Group.objects.filter(name="Administrator"))
    Dataset.objects.filter(schema="foresight", name__in=("forecasts", "outcomes", "signals", "runs")).update(enabled=False)
    Dataset.objects.filter(schema="foresight", name="backtests").update(
        label="Forecast tests on the past", description="How each method would have done on past data. Kept apart from the live record, because a test can flatter.")
    Dataset.objects.filter(schema="foresight", name="v_track_record").update(
        label="Forecast track record", description="Live forecasts only: how they scored against the simple guess. Skill above 0 beats it.")


def backward(apps, schema_editor):
    apps.get_model("accounts", "Menu").objects.filter(code="foresight").delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0009_report_templates_menu"), ("catalog", "0004_saved_queries")]
    operations = [migrations.RunPython(forward, backward)]
