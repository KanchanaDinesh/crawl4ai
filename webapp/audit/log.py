"""record(): write one audit event. Call it in the same transaction as the change it describes."""

from .models import AuditEvent

ACTIONS = {
    "login": "Signed in", "login_failed": "Failed sign-in", "logout": "Signed out", "locked_out": "Locked out",
    "user_created": "User created", "user_updated": "User changed", "user_deactivated": "User deactivated",
    "user_activated": "User activated", "password_set": "Password set", "password_changed": "Own password changed",
    "role_created": "Role created", "role_updated": "Role changed", "role_deleted": "Role deleted",
    "rights_changed": "Menu rights changed",
    "catalog_scanned": "Catalog scanned", "dataset_updated": "Dataset changed in the catalog",
    "dataset_rights_changed": "Dataset rights changed", "export": "Data exported",
    "view_saved": "View saved", "view_deleted": "View deleted",
    "cell_edited": "Value corrected", "rows_edited": "Rows corrected together", "edit_restored": "Earlier value put back",
    "ai_question": "AI assistant asked", "ai_saved": "AI answer saved as a dataset",
    "chart_saved": "Chart saved", "chart_deleted": "Chart deleted", "dashboard_saved": "Dashboard changed", "dashboard_deleted": "Dashboard deleted",
    "report_template_saved": "Report template saved", "report_template_deleted": "Report template deleted", "report_saved": "Report saved", "report_deleted": "Report deleted", "report_made": "Report made", "report_failed": "Report failed",
    "table_created": "Own table made", "table_deleted": "Own table deleted", "table_shared": "Own table shared", "field_added": "Field added",
    "field_changed": "Field changed", "field_removed": "Field removed", "rows_imported": "Rows imported", "row_added": "Row added",
    "links_changed": "Links changed", "choice_added": "Choice added",
    "edit_reviewed": "Flagged value decided", "rows_hidden": "Rows hidden", "rows_unhidden": "Rows shown again",
    "gridtest_edit": "Grid test: cell edited", "gridtest_bulk_edit": "Grid test: rows edited together",
    "gridtest_choice_added": "Grid test: new choice added",
}


def client_ip(request) -> str | None:
    return request.META.get("REMOTE_ADDR") if request is not None else None


def record(request, action: str, summary: str, *, target_type: str = "", target_id: str = "", detail=None,
           user=None, username: str | None = None) -> AuditEvent:
    who = user if user is not None else (request.user if request is not None and request.user.is_authenticated
                                         else None)
    return AuditEvent.objects.create(
        user=who, username=username if username is not None else (who.get_username() if who else ""),
        action=action, target_type=target_type, target_id=str(target_id), summary=summary[:300],
        detail=detail or {}, ip=client_ip(request))
