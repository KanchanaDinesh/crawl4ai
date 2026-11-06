"""Making people's own tables and fields. A field is one catalog row; adding one changes no table, because values live in
JSONB. So a new field works at once in the grid, filters, sorting, exports, saved views and, later, charts."""

import secrets

from django.db import connection, transaction

from webapp.catalog.models import Dataset, Field
from webapp.catalog.rights import can_view

from . import store

TYPE_NAMES = {Field.TEXT: "Text", Field.NUMBER: "Number", Field.MONEY: "Money", Field.DATE: "Date", Field.BOOL: "Yes / no",
              Field.CHOICE: "One choice", Field.MULTI: "Several choices", Field.LINK: "Web address", Field.EMAIL: "Email"}
KIND_NAMES = {Field.RECORDS: "Link to another table", Field.LOOKUP: "Lookup: a field of the linked rows",
              Field.ROLLUP: "Rollup: count or add up the linked rows"}


class FieldProblem(ValueError):
    """What to tell the person."""


def _label(ds: Dataset, label: str, keep: Field | None = None) -> str:
    label = " ".join(str(label or "").split())[:120]
    if not label:
        raise FieldProblem("Give it a name.")
    taken = ds.fields.filter(missing=False, label__iexact=label)
    if keep is not None:
        taken = taken.exclude(pk=keep.pk)
    if taken.exists():
        raise FieldProblem(f"This table already has a field called “{label}”.")
    return label


def _free_label(ds: Dataset, label: str) -> str:
    """For the other side of a link, which nobody typed: "Vehicle ads", then "Vehicle ads 2"."""
    name, n = label, 1
    while ds.fields.filter(missing=False, label__iexact=name).exists():
        n += 1
        name = f"{label} {n}"
    return name


def _next_order(ds: Dataset) -> int:
    last = ds.fields.order_by("-order").values_list("order", flat=True).first()
    return (last or 0) + 10


def _new(ds: Dataset, label: str, kind: str, type_: str, user, shown: bool = True, **extra) -> Field:
    path = f"f_{secrets.token_hex(4)}"
    column, key = "", None
    if kind == Field.OWN:
        column, key = ("data" if ds.is_own else ""), path
    return Field.objects.create(dataset=ds, path=path, column=column, json_key=key, kind=kind, type=type_, label=label, order=_next_order(ds),
                                is_jsonb=kind == Field.OWN or type_ == Field.LIST, shown=shown, created_by=user, **extra)


def _choices(text) -> list[str]:
    lines = str(text or "").replace(",", "\n").splitlines()
    out = []
    for line in lines:
        name = " ".join(line.split())[:store.MAX_CHOICE]
        if name and name.lower() not in {c.lower() for c in out}:
            out.append(name)
    return out[:store.MAX_CHOICES]


def add_own_field(ds: Dataset, label: str, type_: str, user, choices=None, allow_new: bool = True) -> Field:
    if type_ not in TYPE_NAMES:
        raise FieldProblem("Choose a type.")
    values = _choices(choices) if type_ in (Field.CHOICE, Field.MULTI) else []
    return _new(ds, _label(ds, label), Field.OWN, type_, user, values=values, allow_new=allow_new)


def add_link(ds: Dataset, label: str, target: Dataset, user, single: bool = False) -> Field:
    """A link field, and its other side on the target: link an ad to a dealer, and the dealer shows the ad."""
    if target.record_key is None:
        raise FieldProblem(f"{target.label} can't be linked to, because its rows have no key.")
    if ds.record_key is None:
        raise FieldProblem(f"{ds.label} can't have links, because its rows have no key.")
    if not can_view(user, target):
        raise FieldProblem("You can only link to a table you can see.")
    name = _label(ds, label)
    with transaction.atomic():
        link = _new(ds, name, Field.RECORDS, Field.LIST, user, link_dataset=target, link_single=single)
        if target.pk != ds.pk:
            # on a collected dataset it starts under Columns, so one person's table doesn't crowd everyone's list
            _new(target, _free_label(target, ds.label), Field.RECORDS, Field.LIST, user, shown=target.is_own, link_dataset=ds, link_field=link)
    return link


def _link_of(ds: Dataset, pk) -> Field:
    link = ds.fields.filter(pk=pk, kind=Field.RECORDS, missing=False, available=True).select_related("link_dataset").first() if str(pk or "").isdigit() else None
    if link is None:
        raise FieldProblem("Choose which link it goes through.")
    return link


def lookup_targets(link: Field):
    """Fields of the linked rows a lookup or rollup can use. Not other lookups: that could go round in circles."""
    return link.link_dataset.fields.filter(available=True, missing=False, kind__in=[Field.DATA, Field.OWN]).exclude(type=Field.JSON).order_by("order", "id")


def add_lookup(ds: Dataset, label: str, link_id, target_id, user) -> Field:
    link = _link_of(ds, link_id)
    target = lookup_targets(link).filter(pk=target_id).first() if str(target_id or "").isdigit() else None
    if target is None:
        raise FieldProblem("Choose which field of the linked rows to show.")
    return _new(ds, _label(ds, label), Field.LOOKUP, Field.LIST, user, link_dataset=link.link_dataset, link_field=link, target_field=target)


def add_rollup(ds: Dataset, label: str, link_id, how: str, target_id, user) -> Field:
    link = _link_of(ds, link_id)
    if how not in Field.ROLLUPS:
        raise FieldProblem("Choose how to add them up.")
    target = None
    if how != Field.COUNT:
        target = lookup_targets(link).filter(pk=target_id).first() if str(target_id or "").isdigit() else None
        if target is None or target.base != Field.NUMBER:
            raise FieldProblem("Choose a number field of the linked rows to add up.")
    return _new(ds, _label(ds, label), Field.ROLLUP, Field.NUMBER, user, link_dataset=link.link_dataset, link_field=link, target_field=target, rollup=how)


def remove_field(f: Field) -> list[str]:
    """Taken out of use, not erased: its values stay in the database and its history stays readable. What depends on it goes with it.
    Returns the labels of everything that went."""
    gone = []

    def drop(field):
        if field.missing:
            return
        Field.objects.filter(pk=field.pk).update(missing=True, available=False)
        field.missing = True
        gone.append(field.label)
        for other in Field.objects.filter(link_field=field, missing=False):
            drop(other)
        for other in Field.objects.filter(target_field=field, missing=False):
            drop(other)
    with transaction.atomic():
        drop(f)
        if f.is_reverse_link:
            drop(f.link_field)
    return gone


def _standard_fields(ds: Dataset) -> None:
    """The columns of every own table's view. Tucked away under Columns."""
    for order, (path, type_, label) in enumerate((("id", Field.NUMBER, "Row number"), ("created_at", Field.DATETIME, "Added"), ("created_by", Field.TEXT, "Added by"),
                                                  ("updated_at", Field.DATETIME, "Changed"), ("updated_by", Field.TEXT, "Changed by"))):
        Field.objects.create(dataset=ds, path=path, column=path, type=type_, label=label, order=order + 1, shown=False, fill_pct=100)


def create_table(label: str, user, description: str = "", first_field: str | None = "Name") -> Dataset:
    label = " ".join(str(label or "").split())[:120]
    if not label:
        raise FieldProblem("Give the table a name.")
    if Dataset.objects.filter(owner=user, missing=False, label__iexact=label).exists():
        raise FieldProblem(f"You already have a table called “{label}”.")
    with transaction.atomic(), connection.cursor() as cur:
        from warehouse import edits
        if not edits.available(cur):
            raise FieldProblem("Own tables aren't set up on this database yet. Run: uv run python -m warehouse migrate")
        ds = Dataset.objects.create(schema="userdata", name=f"t_{secrets.token_hex(5)}", kind="view", label=label, description=str(description or "").strip()[:300],
                                    owner=user, key_columns=["id"], default_sort_field="id", default_sort_dir="asc", row_count=0)
        store.create_table_view(cur, ds)
        _standard_fields(ds)
        if first_field:
            _new(ds, first_field, Field.OWN, Field.TEXT, user)
    return ds
