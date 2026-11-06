"""Correcting collected data: checks a value, changes the main table, keeps the history. warehouse/edits.py has the part
collectors share. A request names a field by its catalog path and a row by its key; only fields the catalog marks as
editable pass, and PostgreSQL itself lets the web app change no other column."""

from warehouse import edits
from warehouse.postgres import clean_text
from webapp.catalog.models import Dataset, Field
from webapp.catalog.sqlnames import q, relation

from .query import plain

MAX_TEXT = 500
NOT_SEEN = object()      # bulk edits and restores: no "value I saw" to compare with


class EditProblem(ValueError):
    """Something the person should read. `current` is set when someone else changed the value first."""
    def __init__(self, message: str, status: int = 400, current=NOT_SEEN):
        super().__init__(message)
        self.status, self.current = status, current


def _rel(table: str) -> str:
    schema, name = table.split(".")
    return relation(schema, name)


def editable_field(ds: Dataset, path: str) -> Field:
    target = ds.edit_target
    f = ds.fields.filter(path=path, available=True, missing=False, editable=True, json_key=None).first()
    if target is None or f is None or f.column not in target.columns:
        raise EditProblem("That field can't be edited.", 403)
    return f


# ── checking a value ─────────────────────────────────────────────────────────────────────────────────────────
def _number(f: Field, value):
    text = str(value).replace(",", "").strip()
    try:
        number = float(text)
    except ValueError:
        raise EditProblem(f"{f.label} must be a number.") from None
    if number != number or number in (float("inf"), float("-inf")) or number < 0 or number > 9e15 or number != int(number):
        raise EditProblem(f"{f.label} must be a whole number, 0 or more.")
    return int(number)


def _country(f: Field, text: str) -> str:
    from companies.countries import country_names
    code = text.upper()
    if code not in country_names():
        raise EditProblem(f"{f.label} must be a two-letter country code, like LK or GB.")
    return code


def _website(f: Field, text: str) -> str:
    from urllib.parse import urlsplit
    url = text if "://" in text else f"https://{text}"
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or "." not in parts.netloc or " " in url:
        raise EditProblem(f"{f.label} must be a web address, like https://example.com.")
    return url


SPECIAL = {("companies.companies", "country"): _country, ("companies.companies", "website"): _website}
WHOLE_NUMBERS = {("vehicles.ads", "price_lkr")}


def clean_value(target: edits.Target, f: Field, value):
    if isinstance(value, (list, dict, bool)):
        raise EditProblem(f"{f.label} can't take that kind of value.")
    text = "" if value is None else clean_text(str(value)).strip()
    if not text:
        if f.column in target.required:
            raise EditProblem(f"{f.label} can't be empty.")
        return None
    if (target.table, f.column) in WHOLE_NUMBERS:
        return _number(f, text)
    if len(text) > MAX_TEXT:
        raise EditProblem(f"{f.label} can be at most {MAX_TEXT} characters.")
    check = SPECIAL.get((target.table, f.column))
    return check(f, text) if check else text


# ── saving ───────────────────────────────────────────────────────────────────────────────────────────────────
def _current(cur, target: edits.Target, column: str, record: str):
    """The value now, locked until the end of the transaction, so two people can't save over each other unseen.
    Returns (found, value)."""
    order = f" order by {q(target.latest_by)} desc limit 1" if target.latest_by else ""
    cur.execute(f"select {q(column)} from {_rel(target.table)} where {q(target.key)} = %s{order} for update", [record])
    row = cur.fetchone()
    return (True, plain(row[0])) if row else (False, None)


def _current_plain(cur, target: edits.Target, column: str, record: str):
    """The same without locking, for showing."""
    order = f" order by {q(target.latest_by)} desc limit 1" if target.latest_by else ""
    cur.execute(f"select {q(column)} from {_rel(target.table)} where {q(target.key)} = %s{order}", [record])
    row = cur.fetchone()
    return plain(row[0]) if row else None


def _write(cur, target: edits.Target, column: str, record: str, value) -> None:
    where = f"{q(target.key)} = %s"
    params = [value, record]
    if target.latest_by:      # one row per run: the correction goes on the latest, and rule A carries it to the next
        where += f" and {q(target.latest_by)} = (select max({q(target.latest_by)}) from {_rel(target.table)} where {q(target.key)} = %s)"
        params.append(record)
    cur.execute(f"update {_rel(target.table)} set {q(column)} = %s where {where}", params)


def save_cell(cur, ds: Dataset, f: Field, record: str, value, who: str, *, seen=NOT_SEEN, origin="user", note="") -> dict:
    """Call inside a transaction. Returns {"value", "changed", "linked"}; raises EditProblem."""
    target = ds.edit_target
    found, before = _current(cur, target, f.column, record)
    if not found:
        raise EditProblem("That record isn't there any more.", 404)
    new = clean_value(target, f, value)
    if seen is not NOT_SEEN and not edits.same(before, seen):
        raise EditProblem(f"Someone changed {f.label} while you were editing it. It now says: {before if before not in (None, '') else '(empty)'}. "
                          "Your change wasn't saved.", 409, current=before)
    if edits.same(before, new):
        return {"value": before, "changed": False, "linked": {}}
    _write(cur, target, f.column, record, new)
    edits.note_user_edit(cur, target.table, record, f.column, before, new, who, f.edit_rule, origin, note)
    linked = {}
    follower = target.linked.get(f.column)
    if follower:
        _, was = _current(cur, target, follower, record)
        now = edits.linked_value(target, f.column, new)
        if not edits.same(was, now):
            _write(cur, target, follower, record, now)
            edits.note_user_edit(cur, target.table, record, follower, was, now, who, f.edit_rule, "linked", f"Follows {f.label}")
            linked[follower] = now
    return {"value": new, "changed": True, "linked": linked}


def require_ready(cur) -> None:
    if not edits.available(cur):
        raise EditProblem("Editing isn't set up on this database yet. Run: uv run python -m warehouse migrate", 503)


def hide_rows(cur, ds: Dataset, records: list[str], who: str) -> int:
    target = ds.hide_target
    cur.execute(f"select {q(target.key)}, {q(target.label)} from {_rel(target.table)} where {q(target.key)} = any(%s)", [records])
    labels = dict(cur.fetchall())
    return sum(edits.hide(cur, target.table, r, str(labels.get(r) or r)[:200], who) for r in records if r in labels)


def bump_counts(ds: Dataset) -> None:
    """Row counts are remembered for 30 seconds; a change can move rows in and out of a filter, so start again."""
    from django.core.cache import cache
    try:
        cache.incr(f"dsgen:{ds.pk}")
    except ValueError:
        cache.set(f"dsgen:{ds.pk}", 1, None)
