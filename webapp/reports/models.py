"""Reports, on demand or on a schedule: a dashboard as a PDF, or a newsletter made of sections that each take the latest rows of a dataset.
Each one made is kept as a file, with a row here to find it by."""

from django.conf import settings
from django.db import models

from webapp.catalog.models import Dataset
from webapp.charts.models import Dashboard


class Report(models.Model):
    DASHBOARD, NEWSLETTER = "dashboard", "newsletter"
    NEVER, DAILY, WEEKLY, MONTHLY = "", "daily", "weekly", "monthly"
    SCHEDULES = {NEVER: "Only when asked", DAILY: "Every day", WEEKLY: "Every week", MONTHLY: "Every month"}
    WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reports")
    name = models.CharField(max_length=120)
    intro = models.TextField(blank=True, max_length=3000)
    kind = models.CharField(max_length=10, default=DASHBOARD)
    dashboard = models.ForeignKey(Dashboard, null=True, blank=True, on_delete=models.CASCADE, related_name="reports")
    include_tables = models.BooleanField(default=True, help_text="Print the numbers under each chart")
    schedule = models.CharField(max_length=7, blank=True, default=NEVER)
    weekday = models.PositiveSmallIntegerField(default=0)
    shared = models.BooleanField(default=False)
    last_scheduled = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return self.name

    def get_weekday_name(self) -> str:
        return self.WEEKDAYS[self.weekday]

    @property
    def is_newsletter(self) -> bool:
        return self.kind == self.NEWSLETTER


class ReportSection(models.Model):
    """One part of a newsletter. Stores what was chosen (field paths from the catalog), never rows: it's worked out when the report is made, with the rights of whoever it's for."""

    ARTICLES, TABLE = "articles", "table"
    LAYOUTS = {ARTICLES: "Articles: a heading and a few lines each", TABLE: "Table: one line each"}
    PERIODS = {0: "Everything", 1: "The last 24 hours", 2: "The last 2 days", 7: "The last 7 days", 30: "The last 30 days"}
    MAX_ITEMS = 100

    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="sections")
    order = models.PositiveSmallIntegerField(default=0)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="report_sections")
    heading = models.CharField(max_length=120)
    layout = models.CharField(max_length=10, default=ARTICLES)
    title_field = models.CharField(max_length=200, blank=True)
    text_field = models.CharField(max_length=200, blank=True)
    link_field = models.CharField(max_length=200, blank=True)
    detail_fields = models.JSONField(default=list, blank=True)
    date_field = models.CharField(max_length=200, blank=True)
    days = models.PositiveSmallIntegerField(default=0, help_text="Only rows this recent, by date_field. 0: all of them")
    sort_field = models.CharField(max_length=200, blank=True)
    sort_desc = models.BooleanField(default=True)
    limit = models.PositiveSmallIntegerField(default=10, help_text="0: only the count and the breakdown")
    breakdown_field = models.CharField(max_length=200, blank=True)
    filters = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return self.heading

    def period_label(self) -> str:
        return self.PERIODS.get(self.days, f"The last {self.days} days")


class ReportRun(models.Model):
    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="runs")
    made_at = models.DateTimeField(auto_now_add=True)
    made_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    scheduled = models.BooleanField(default=False)
    filename = models.CharField(max_length=200, blank=True)
    size = models.PositiveIntegerField(default=0)
    seconds = models.FloatField(default=0)
    problem = models.TextField(blank=True)

    class Meta:
        ordering = ["-made_at", "-id"]
