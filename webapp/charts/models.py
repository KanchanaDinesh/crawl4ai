"""Charts people build, and dashboards that arrange them. A chart stores what was chosen (field paths from the catalog),
never SQL or numbers: it's worked out when it's shown, for the person looking, with their rights."""

from django.conf import settings
from django.db import models

from webapp.catalog.models import Dataset


class Chart(models.Model):
    COLUMN, BAR, LINE, AREA, PIE, NUMBER, TABLE = "column", "bar", "line", "area", "pie", "number", "table"
    KINDS = {COLUMN: "Columns", BAR: "Bars", LINE: "Line", AREA: "Stacked area", PIE: "Doughnut", NUMBER: "One number", TABLE: "Table"}
    COUNT, SUM, AVG, MIN, MAX = "count", "sum", "avg", "min", "max"
    MEASURES = {COUNT: "Count of rows", SUM: "Sum", AVG: "Average", MIN: "Smallest", MAX: "Largest"}
    TIME_UNITS = {"day": "Day", "week": "Week", "month": "Month", "year": "Year"}

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="charts")
    name = models.CharField(max_length=120)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="charts")
    kind = models.CharField(max_length=10, default=COLUMN)
    group_field = models.CharField(max_length=200, blank=True)
    time_unit = models.CharField(max_length=5, blank=True)
    split_field = models.CharField(max_length=200, blank=True)
    measure = models.CharField(max_length=5, default=COUNT)
    measure_field = models.CharField(max_length=200, blank=True)
    filters = models.JSONField(default=list, blank=True)
    limit = models.PositiveSmallIntegerField(default=12)
    shared = models.BooleanField(default=False, help_text="Everyone with the Charts menu can open it, if they may see its data")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return self.name


class Dashboard(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="dashboards")
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=300, blank=True)
    shared = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return self.name


class DashboardItem(models.Model):
    dashboard = models.ForeignKey(Dashboard, on_delete=models.CASCADE, related_name="items")
    chart = models.ForeignKey(Chart, on_delete=models.CASCADE, related_name="placements")
    order = models.PositiveIntegerField(default=0)
    wide = models.BooleanField(default=False, help_text="Takes the whole width of the dashboard")

    class Meta:
        ordering = ["order", "id"]
