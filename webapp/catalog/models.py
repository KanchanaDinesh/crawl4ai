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

People can also add their own fields, to any dataset whose rows have a key, and their own tables (schema `userdata`,
webapp/userdata/). A field then has a `kind`, and everything that reads fields works with all kinds, because each one
is an SQL expression in the end (explore/query.py).
"""

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models

from warehouse import edits

USERDATA = "userdata"
QUERY_PREFIX = "a_"


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
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="own_tables",
                              help_text="Own tables only: who made it")
    use_in_ai = models.BooleanField(default=True, help_text="The AI assistant may use it (it only ever sees its description, never its rows)")
    query_sources = models.ManyToManyField("self", symmetrical=False, blank=True, related_name="used_by_queries",
                                           help_text="A saved query: the datasets it reads")

    class Meta:
        ordering = ["schema", "label"]
        constraints = [models.UniqueConstraint(fields=["schema", "name"], name="dataset_once")]

    def __str__(self) -> str:
        return self.ref

    @property
    def ref(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def is_own(self) -> bool:
        """A table people make and fill in. (A saved query lives in the same schema, but is worked out, not filled in.)"""
        return self.schema == USERDATA and not self.is_query

    @property
    def is_query(self) -> bool:
        """A live dataset saved from the AI assistant: a view, so its rows are always current."""
        return self.schema == USERDATA and self.name.startswith(QUERY_PREFIX)

    @property
    def edit_target(self):
        """The collected table behind this dataset, whose columns people may correct (warehouse/edits.py); or None."""
        return edits.target_for(self.ref)

    @property
    def hide_target(self):
        if self.is_own:
            return edits.Target(self.ref, "id", (), "")
        return edits.hide_target_for(self.ref)

    @property
    def record_key(self) -> str | None:
        """The column that identifies a row. Without one, a row can't have its own fields, links or history."""
        target = self.edit_target
        if target:
            return target.key
        if self.is_own:
            return "id"
        return self.key_columns[0] if len(self.key_columns) == 1 else None

    @property
    def record_table(self) -> str:
        """The name a row's history and hiding go under: the table when several datasets show the same rows."""
        target = self.edit_target
        return target.table if target else self.ref


class Field(models.Model):
    TEXT, LINK, NUMBER, DATETIME, BOOL, LIST, JSON = "text", "link", "number", "datetime", "bool", "list", "json"
    CHOICE, MULTI, DATE, EMAIL, MONEY = "choice", "multi", "date", "email", "money"     # people's own fields
    TYPES = [(t, t) for t in (TEXT, LINK, NUMBER, DATETIME, BOOL, LIST, JSON, CHOICE, MULTI, DATE, EMAIL, MONEY)]
    # how a type behaves in SQL and filters
    BASE = {CHOICE: TEXT, EMAIL: TEXT, MULTI: LIST, MONEY: NUMBER}

    DATA, OWN, RECORDS, LOOKUP, ROLLUP = "data", "own", "records", "lookup", "rollup"
    KINDS = [(k, k) for k in (DATA, OWN, RECORDS, LOOKUP, ROLLUP)]
    COUNT, SUM, AVG, MIN, MAX = "count", "sum", "avg", "min", "max"
    ROLLUPS = {COUNT: "Count", SUM: "Sum", AVG: "Average", MIN: "Smallest", MAX: "Largest"}

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
    # data: a column or JSON key found by the scan. own: a value people type, kept in userdata. records: links to rows
    # of link_dataset. lookup and rollup: a field of the linked rows, shown or added up, through link_field.
    kind = models.CharField(max_length=10, choices=KINDS, default=DATA)
    link_dataset = models.ForeignKey(Dataset, null=True, blank=True, on_delete=models.CASCADE, related_name="linked_from")
    link_field = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="depends",
                                   help_text="A lookup or rollup: which link it goes through. A link's other side: the link it mirrors")
    target_field = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="looked_up")
    rollup = models.CharField(max_length=5, blank=True)
    link_single = models.BooleanField(default=False, help_text="A link: one linked row only")
    allow_new = models.BooleanField(default=True, help_text="A choice field: people can add a choice by typing it")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["order", "id"]
        constraints = [models.UniqueConstraint(fields=["dataset", "path"], name="field_once_per_dataset")]

    def __str__(self) -> str:
        return f"{self.dataset.ref}: {self.path}"

    @property
    def base(self) -> str:
        return self.BASE.get(self.type, self.type)

    @property
    def is_mine(self) -> bool:
        """Added by people, not found by the scan."""
        return self.kind != self.DATA

    @property
    def is_reverse_link(self) -> bool:
        return self.kind == self.RECORDS and self.link_field_id is not None


class DatasetRight(models.Model):
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="rights")
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="dataset_rights")
    can_view = models.BooleanField(default=True)
    can_export = models.BooleanField(default=False)
    can_edit = models.BooleanField(default=False)
    can_hide = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["dataset", "group"], name="right_once_per_role")]
