"""Edits made in the grid test. They never touch the collected data: they're kept here, shown on top of the real
rows, and each one is in the audit log. Phase 4 brings real editing (with history and rule A)."""

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


class GridTestEdit(models.Model):
    company_id = models.CharField(max_length=200, db_index=True)
    field = models.CharField(max_length=40)
    value = models.JSONField(null=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    at = models.DateTimeField(auto_now_add=True)
    engine = models.CharField(max_length=20, blank=True, help_text="Which grid the edit came from")

    class Meta:
        ordering = ["at", "id"]


class GridTestChoice(models.Model):
    """A choice a user added by typing it in a multiple-choice list. Shared with everyone; "Tag" and "tag" are one."""
    field = models.CharField(max_length=40)
    value = models.CharField(max_length=40)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint("field", Lower("value"), name="gridtest_choice_once_per_field")]
