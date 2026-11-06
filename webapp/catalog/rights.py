"""Who may see and export which dataset. Administrators may do everything; everyone else gets it through a role, and
rights add up over all their roles. A dataset that's switched off is for administrators only."""

from django.db.models import Q

from webapp.accounts.rights import ADMINISTRATOR

from .models import QUERY_PREFIX, USERDATA, Dataset, DatasetRight


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
    for r in DatasetRight.objects.filter(group__user=user).values("dataset_id", "can_view", "can_export", "can_edit", "can_hide"):
        e = rights.setdefault(r["dataset_id"], {"view": False, "export": False, "edit": False, "hide": False})
        e["view"] |= r["can_view"]
        for right, column in (("export", "can_export"), ("edit", "can_edit"), ("hide", "can_hide")):
            e[right] |= r["can_view"] and r[column]
    user._dataset_rights = rights
    return rights


def _owns(user, ds: Dataset) -> bool:
    return ds.owner_id is not None and ds.owner_id == user.pk and not ds.missing


def can_view(user, ds: Dataset) -> bool:
    if is_admin(user):
        return True
    # a saved query shows rows of what it reads, so sharing it gives nobody a dataset they couldn't see
    if ds.is_query and not all(can_view(user, source) for source in ds.query_sources.all()):
        return False
    if _owns(user, ds):
        return True
    return ds.enabled and not ds.missing and _rights(user).get(ds.pk, {}).get("view", False)


def can_export(user, ds: Dataset) -> bool:
    if is_admin(user):
        return True
    if not can_view(user, ds):
        return False
    if _owns(user, ds):
        return True
    return ds.enabled and not ds.missing and _rights(user).get(ds.pk, {}).get("export", False)


def can_edit(user, ds: Dataset) -> bool:
    """Correct its columns (where warehouse/edits.py allows) and use its own fields. Needs rows with a key."""
    if ds.record_key is None or ds.missing:
        return False
    return is_admin(user) or _owns(user, ds) or (ds.enabled and _rights(user).get(ds.pk, {}).get("edit", False))


def can_hide(user, ds: Dataset) -> bool:
    if (ds.edit_target is None and not ds.is_own) or ds.missing:
        return False
    return is_admin(user) or _owns(user, ds) or (ds.enabled and _rights(user).get(ds.pk, {}).get("hide", False))


def can_share(user, ds: Dataset) -> bool:
    return (ds.is_own or ds.is_query) and (is_admin(user) or _owns(user, ds))


def visible_datasets(user):
    """What this user sees in the dataset list (administrators also reach the switched-off ones from the catalog)."""
    found = Dataset.objects.filter(enabled=True, missing=False)
    if is_admin(user):
        return found
    found = found.filter(Q(pk__in=[pk for pk, r in _rights(user).items() if r["view"]]) | Q(owner=user))
    sources = found.filter(name__startswith=QUERY_PREFIX, schema=USERDATA).prefetch_related("query_sources")
    closed = [ds.pk for ds in sources if not can_view(user, ds)]
    return found.exclude(pk__in=closed) if closed else found
