"""Every question asked of the AI assistant: who, what it wrote, what it cost. Rows of the answers aren't kept: they're worked out when shown."""

from django.conf import settings
from django.db import models

from webapp.catalog.models import Dataset


class Question(models.Model):
    ANSWERED, CLARIFY, REFUSED, FAILED = "answered", "clarify", "refused", "failed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ai_questions")
    text = models.TextField(max_length=1000)
    at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, default=FAILED)
    title = models.CharField(max_length=120, blank=True)
    explanation = models.TextField(blank=True)
    clarification = models.TextField(blank=True)
    problem = models.TextField(blank=True)
    sql = models.TextField(blank=True)
    sources = models.JSONField(default=list, blank=True)     # the datasets it reads
    rows = models.BigIntegerField(null=True, blank=True)
    calls = models.PositiveSmallIntegerField(default=0)
    model = models.CharField(max_length=40, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cache_read_tokens = models.PositiveIntegerField(default=0)
    cache_write_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=9, decimal_places=5, default=0)
    seconds = models.FloatField(default=0)
    saved_as = models.ForeignKey(Dataset, null=True, blank=True, on_delete=models.SET_NULL, related_name="questions")

    class Meta:
        ordering = ["-at", "-id"]
