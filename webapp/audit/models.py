"""The audit log: one row per action that changes something, exports data, or signs in or out.

Rows are only ever added: a database trigger (migration 0002) refuses UPDATE and DELETE on this table, so nobody,
the app included, can change or remove history. Users are never deleted (they're deactivated), so the link to
the user always stays.
"""

from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                             related_name="audit_events")
    username = models.CharField(max_length=150, blank=True, help_text="Kept as typed, also for failed logins")
    action = models.CharField(max_length=40, db_index=True)
    target_type = models.CharField(max_length=40, blank=True)
    target_id = models.CharField(max_length=200, blank=True)
    summary = models.CharField(max_length=300)
    detail = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-at", "-id"]
        indexes = [models.Index(fields=["user", "-at"]), models.Index(fields=["target_type", "target_id"])]

    def __str__(self) -> str:
        return f"{self.at:%Y-%m-%d %H:%M} {self.username} {self.action}: {self.summary}"
