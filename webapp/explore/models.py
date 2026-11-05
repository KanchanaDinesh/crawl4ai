"""A saved view: what someone arranged in a dataset's grid (columns, order, sorting, filters), under a name. Private,
or shared with everyone who may see the dataset. Rights are checked again when it opens, so sharing a view never
gives anyone a dataset they couldn't see."""

from django.conf import settings
from django.db import models

from webapp.catalog.models import Dataset


class SavedView(models.Model):
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="saved_views")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="saved_views")
    name = models.CharField(max_length=80)
    shared = models.BooleanField(default=False)
    state = models.JSONField(default=dict)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [models.UniqueConstraint(fields=["dataset", "owner", "name"], name="view_name_once_per_owner")]

    def __str__(self) -> str:
        return f"{self.dataset.ref}: {self.name}"
