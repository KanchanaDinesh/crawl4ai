"""Who may see and export which dataset. Administrators may do everything; everyone else gets it through a role, and
rights add up over all their roles. A dataset that's switched off is for administrators only."""

from webapp.accounts.rights import ADMINISTRATOR

from .models import Dataset, DatasetRight


def is_admin(user) -> bool:
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    cached = getattr(user, "_is_administrator", None)
    if cached is None:
        cached = user._is_administrator = user.groups.filter(name=ADMINISTRATOR).exists()
    return cached


def _rights(user) -> dict[int, dict]:
    cached = getattr(user, "_dataset_rights", None)
    if cached is not None:
        return cached
    rights: dict[int, dict] = {}
    for r in DatasetRight.objects.filter(group__user=user).values("dataset_id", "can_view", "can_export"):
        e = rights.setdefault(r["dataset_id"], {"view": False, "export": False})
        e["view"] |= r["can_view"]
        e["export"] |= r["can_view"] and r["can_export"]
    user._dataset_rights = rights
    return rights


def can_view(user, ds: Dataset) -> bool:
    if is_admin(user):
        return True
    return ds.enabled and not ds.missing and _rights(user).get(ds.pk, {}).get("view", False)


def can_export(user, ds: Dataset) -> bool:
    if is_admin(user):
        return True
    return ds.enabled and not ds.missing and _rights(user).get(ds.pk, {}).get("export", False)


def visible_datasets(user):
    """What this user sees in the dataset list (administrators also reach the switched-off ones from the catalog)."""
    found = Dataset.objects.filter(enabled=True, missing=False)
    if is_admin(user):
        return found
    return found.filter(pk__in=[pk for pk, r in _rights(user).items() if r["view"]])
