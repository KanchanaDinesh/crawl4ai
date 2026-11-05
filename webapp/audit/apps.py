from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = "webapp.audit"
    label = "audit"
    verbose_name = "Audit log"

    def ready(self) -> None:
        from . import signals  # noqa: F401  (logins, logouts, failed logins)
