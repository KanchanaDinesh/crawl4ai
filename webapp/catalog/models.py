"""The catalog: what data exists and how to show it, built automatically (catalog/scan.py) from PostgreSQL.

A Dataset is one table or view in a data schema (news, tech, markets, vehicles, companies, scrapers). A Field is a
real column, or a key found inside a JSONB column, so people see "fields" whichever way a collector stored them.
Everything that shows data reads this, so a new collector table or a new JSON key needs no new code.

Scans refresh what comes from the data (type, how full, value lists, row counts); what an administrator set
(labels, descriptions, on/off, rights) is kept. Something that disappears is marked missing, never deleted, so
saved views and rights survive a collector being rebuilt.

DatasetRight says what a role may do with a dataset. Administrators may do everything, and a new dataset nobody
has been given stays with administrators until they grant it. Which datasets and columns can be corrected at all is in
warehouse/edits.py; here an administrator switches them on per field and per role.
"""

from django.contrib.auth.models import Group
from django.db import models

from warehouse import edits


class Dataset(models.Model):
    schema = models.CharField(max_length=63)
    name = models.CharField(max_length=63)
    kind = models.CharField(max_length=10, default="table")      # table or view
    label = models.CharField(max_length=120)
    description = models.CharField(max_length=300, blank=True)
    enabled = models.BooleanField(default=True, help_text="Off: only administrators can open it, from the catalog")
    key_columns = models.JSONField(default=list, blank=True)     # what identifies a row: keeps paging stable
    default_sort_field = models.CharField(max_length=200, blank=True)
    default_sort_dir = models.CharField(max_length=4, default="asc")
    row_count = models.BigIntegerField(null=True, blank=True)
    missing = models.BooleanField(default=False, help_text="Was not there in the last scan")
    first_seen = models.DateTimeField(auto_now_add=True)
    scanned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["schema", "label"]
        constraints = [models.UniqueConstraint(fields=["schema", "name"], name="dataset_once")]

    def __str__(self) -> str:
        return self.ref

    @property
    def ref(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def edit_target(self):
        return edits.target_for(self.ref)

    @property
    def hide_target(self):
        return edits.hide_target_for(self.ref)


class Field(models.Model):
    TEXT, LINK, NUMBER, DATETIME, BOOL, LIST, JSON = "text", "link", "number", "datetime", "bool", "list", "json"
    TYPES = [(t, t) for t in (TEXT, LINK, NUMBER, DATETIME, BOOL, LIST, JSON)]

    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="fields")
    path = models.CharField(max_length=200)          # "country", or "extra.city" for a key inside a JSONB column
    column = models.CharField(max_length=63)
    json_key = models.CharField(max_length=130, null=True, blank=True)
    type = models.CharField(max_length=10, choices=TYPES, default=TEXT)
    is_jsonb = models.BooleanField(default=False)    # the value comes out of a JSONB column or key
    label = models.CharField(max_length=120)
    description = models.CharField(max_length=300, blank=True)
    order = models.PositiveIntegerField(default=0)
    available = models.BooleanField(default=True, help_text="Off: nobody sees this field")
    shown = models.BooleanField(default=True, help_text="Shown without choosing it under Columns")
    fill_pct = models.PositiveSmallIntegerField(default=0)
    values = models.JSONField(default=list, blank=True)   # the choices, for a field with few different values
    missing = models.BooleanField(default=False)
    editable = models.BooleanField(default=False, help_text="People with the right can correct it")
    edit_rule = models.CharField(max_length=1, default="A", help_text="When the source changes a corrected value")

    class Meta:
        ordering = ["order", "id"]
        constraints = [models.UniqueConstraint(fields=["dataset", "path"], name="field_once_per_dataset")]

    def __str__(self) -> str:
        return f"{self.dataset.ref}: {self.path}"


class DatasetRight(models.Model):
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="rights")
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="dataset_rights")
    can_view = models.BooleanField(default=True)
    can_export = models.BooleanField(default=False)
    can_edit = models.BooleanField(default=False)
    can_hide = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["dataset", "group"], name="right_once_per_role")]
