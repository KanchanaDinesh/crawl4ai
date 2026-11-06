"""Reports: a dashboard as a PDF, on demand or on a schedule. Each one made is kept as a file, with a row here to find it by."""

from django.conf import settings
from django.db import models

from webapp.charts.models import Dashboard


class Report(models.Model):
    NEVER, DAILY, WEEKLY, MONTHLY = "", "daily", "weekly", "monthly"
    SCHEDULES = {NEVER: "Only when asked", DAILY: "Every day", WEEKLY: "Every week", MONTHLY: "Every month"}
    WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reports")
    name = models.CharField(max_length=120)
    intro = models.TextField(blank=True, max_length=3000)
    dashboard = models.ForeignKey(Dashboard, on_delete=models.CASCADE, related_name="reports")
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
