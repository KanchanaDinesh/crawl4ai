"""People's own data: values of their own fields, rows of their own tables, and links between rows. Everything here runs
inside the caller's transaction, on a cursor. Values are checked before they're saved, and every change is in the history
(audit.field_history), the same as a correction of collected data."""

import json
import re
from datetime import date, datetime

from warehouse import edits
from warehouse.postgres import clean_text
from webapp.catalog.models import Dataset, Field
from webapp.catalog.sqlnames import relation
from webapp.explore.query import key_text, label_field, value_expr

MAX_TEXT = 5000
MAX_CHOICE = 60
MAX_CHOICES = 300
MAX_LINKS = 200
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class StoreProblem(ValueError):
    """Something the person should read. `current`: someone else changed the value first."""
    NOT_SET = object()

    def __init__(self, message: str, status: int = 400, current=NOT_SET):
        super().__init__(message)
        self.status, self.current = status, current


def _rel(ds: Dataset) -> str:
    return relation(ds.schema, ds.name)


# ── checking a value ─────────────────────────────────────────────────────────────────────────────────────────
def _as_number(f: Field, value):
    text = str(value).replace(",", "").strip()
    if f.type == Field.MONEY:
        text = re.sub(r"^(rs\.?|lkr|usd|\$)\s*", "", text, flags=re.I)
    try:
        number = float(text)
    except ValueError:
        raise StoreProblem(f"{f.label} must be a number.") from None
    if number != number or abs(number) > 1e15:
        raise StoreProblem(f"{f.label} must be a number.")
    return int(number) if number == int(number) else number


def _as_bool(f: Field, value):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("yes", "y", "true", "1", "on"):
        return True
    if text in ("no", "n", "false", "0", "off"):
        return False
    raise StoreProblem(f"{f.label} must be yes or no.")


def _as_date(f: Field, value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise StoreProblem(f"{f.label} must be a date like 2026-11-06.") from None


def _as_link(f: Field, text: str):
    url = text if "://" in text else f"https://{text}"
    if not url.lower().startswith(("http://", "https://")) or "." not in url or " " in url:
        raise StoreProblem(f"{f.label} must be a web address, like https://example.com.")
    return url


def _as_email(f: Field, text: str):
    if not EMAIL_RE.fullmatch(text):
        raise StoreProblem(f"{f.label} must be an email address.")
    return text


def _choice_named(f: Field, text: str, added: set | None = None) -> str:
    """A choice, in the spelling already in the list ("Tag" and "tag" are one). A new one joins the list if the field allows it."""
    text = " ".join(text.split())
    for known in f.values:
        if known.lower() == text.lower():
            return known
    if not f.allow_new:
        raise StoreProblem(f"{text} isn't one of the choices for {f.label}.")
    if len(text) > MAX_CHOICE:
        raise StoreProblem(f"A choice can be at most {MAX_CHOICE} characters.")
    if len(f.values) >= MAX_CHOICES:
        raise StoreProblem(f"{f.label} has {MAX_CHOICES} choices already, which is the most it can have.")
    f.values = [*f.values, text]
    if added is not None:
        added.add(f.pk)
    else:
        f.save(update_fields=["values"])
    return text


def clean_value(f: Field, value, added: set | None = None):
    """What's saved for a value someone typed, pasted or imported; None when it's empty. `added`: during a big import, new
    choices are saved once at the end, by the caller."""
    if isinstance(value, dict):
        raise StoreProblem(f"{f.label} can't take that kind of value.")
    if f.type == Field.MULTI:
        items = value if isinstance(value, list) else str(value or "").split(",")
        out = []
        for item in items:
            text = clean_text(str(item or "")).strip()
            if text and "," in text:
                raise StoreProblem("A choice can't have a comma in it.")
            if text:
                name = _choice_named(f, text, added)
                if name not in out:
                    out.append(name)
        return out or None
    if isinstance(value, list):
        raise StoreProblem(f"{f.label} takes one value.")
    if isinstance(value, bool) and f.type != Field.BOOL:
        value = "yes" if value else "no"
    if value is None or (isinstance(value, str) and not clean_text(value).strip()):
        return None
    if f.base == Field.NUMBER:
        return _as_number(f, value)
    if f.type == Field.BOOL:
        return _as_bool(f, value)
    if f.type == Field.DATE:
        return _as_date(f, value)
    text = clean_text(str(value)).strip()
    if len(text) > MAX_TEXT:
        raise StoreProblem(f"{f.label} can be at most {MAX_TEXT:,} characters.")
    if f.type == Field.LINK:
        return _as_link(f, text)
    if f.type == Field.EMAIL:
        return _as_email(f, text)
    if f.type == Field.CHOICE:
        return _choice_named(f, text, added)
    return text


# ── values of own fields ─────────────────────────────────────────────────────────────────────────────────────
def _load(text):
    return None if text is None else json.loads(text)


def current_value(cur, ds: Dataset, f: Field, record: str, lock: bool = False):
    """Returns (the row exists, its value). Locked, so two people can't save over each other unseen."""
    if ds.is_own:
        if not record.isdigit():
            return False, None
        cur.execute(f"""select (r.data -> %s)::text from userdata.records r where r.id = %s and r.dataset_id = %s
                        and exists (select 1 from {_rel(ds)} d where d.id = r.id){' for update' if lock else ''}""",
                    [f.json_key, int(record), ds.pk])
        row = cur.fetchone()
        return (True, _load(row[0])) if row else (False, None)
    cur.execute(f"select 1 from {_rel(ds)} d where {key_text(ds)} = %s limit 1", [record])
    if not cur.fetchone():
        return False, None
    cur.execute(f"select (data -> %s)::text from userdata.extras where dataset_id = %s and record = %s{' for update' if lock else ''}", [f.json_key, ds.pk, record])
    row = cur.fetchone()
    return True, _load(row[0]) if row else None


def _store(cur, ds: Dataset, f: Field, record: str, value, who: str) -> None:
    if ds.is_own:
        if value is None:
            cur.execute("update userdata.records set data = data - %s, updated_at = now(), updated_by = %s where id = %s", [f.json_key, who, int(record)])
        else:
            cur.execute("""update userdata.records set data = jsonb_set(data, array[%s], %s::jsonb), updated_at = now(), updated_by = %s
                           where id = %s""", [f.json_key, json.dumps(value, ensure_ascii=False), who, int(record)])
        return
    new = {} if value is None else {f.json_key: value}
    cur.execute("""insert into userdata.extras (dataset_id, record, data, updated_at, updated_by) values (%s, %s, %s::jsonb, now(), %s)
                   on conflict (dataset_id, record) do update set data = (userdata.extras.data - %s) || excluded.data,
                       updated_at = now(), updated_by = excluded.updated_by""",
                [ds.pk, record, json.dumps(new, ensure_ascii=False), who, f.json_key])


def _refresh_labels(cur, ds: Dataset, f: Field, record: str, value) -> None:
    """The name of a row changed: links to it show the new name. (Only own tables: their rows change by a person's hand.)"""
    if not ds.is_own or label_field(ds) != f:
        return
    text = None if value is None else str(value)
    cur.execute(f"""update userdata.links l set to_label = %s from {Field._meta.db_table} k
                   where k.id = l.field_id and k.link_dataset_id = %s and k.link_field_id is null and l.to_record = %s""", [text, ds.pk, record])
    cur.execute(f"""update userdata.links l set from_label = %s from {Field._meta.db_table} k
                   where k.id = l.field_id and k.dataset_id = %s and k.link_field_id is null and l.from_record = %s""", [text, ds.pk, record])


def save_value(cur, ds: Dataset, f: Field, record: str, value, who: str, *, seen=StoreProblem.NOT_SET, origin="user", note="") -> dict:
    found, before = current_value(cur, ds, f, record, lock=True)
    if not found:
        raise StoreProblem("That row isn't there any more.", 404)
    new = clean_value(f, value)
    if seen is not StoreProblem.NOT_SET and not edits.same(before, seen):
        shown = before if before not in (None, "") else "(empty)"
        raise StoreProblem(f"Someone changed {f.label} while you were editing it. It now says: {shown}. Your change wasn't saved.", 409, current=before)
    if edits.same(before, new):
        return {"value": before, "changed": False, "linked": {}}
    _store(cur, ds, f, record, new, who)
    edits.add_history(cur, ds.record_table, record, f.path, before, new, who, origin, note)
    _refresh_labels(cur, ds, f, record, new)
    return {"value": new, "changed": True, "linked": {}}


# ── rows of own tables ───────────────────────────────────────────────────────────────────────────────────────
def add_row(cur, ds: Dataset, who: str, data: dict | None = None) -> int:
    cur.execute("insert into userdata.records (dataset_id, data, created_by) values (%s, %s::jsonb, %s) returning id",
                [ds.pk, json.dumps(data or {}, ensure_ascii=False), who])
    return cur.fetchone()[0]


def add_rows(cur, ds: Dataset, who: str, rows: list[dict]) -> None:
    cur.executemany("insert into userdata.records (dataset_id, data, created_by) values (%s, %s::jsonb, %s)",
                    [(ds.pk, json.dumps(r, ensure_ascii=False), who) for r in rows])


def count_rows(cur, ds: Dataset) -> int:
    cur.execute(f"select count(*) from {_rel(ds)}")
    return cur.fetchone()[0]


def create_table_view(cur, ds: Dataset) -> None:
    """A name for the table's rows, so everything that reads a dataset reads this one too. Hidden rows aren't in it."""
    cur.execute(f"""create view {_rel(ds)} as
        select r.id, r.created_at, r.created_by, r.updated_at, r.updated_by, r.data from userdata.records r
        where r.dataset_id = {int(ds.pk)}
          and not exists (select 1 from {edits.HIDDEN} h where h.dataset = '{ds.ref}' and h.record = r.id::text)""")


# ── names of rows ────────────────────────────────────────────────────────────────────────────────────────────
def _name_expr(ds: Dataset) -> str:
    f = label_field(ds)
    return f"({value_expr(f)})::text" if f else key_text(ds)


def row_names(cur, ds: Dataset, records: list[str]) -> dict[str, str]:
    """Rows that exist and aren't hidden, with their names."""
    if not records:
        return {}
    from webapp.explore.query import not_hidden
    where = not_hidden(ds, f" where {key_text(ds)} = any(%s)")
    cur.execute(f"select {key_text(ds)}, {_name_expr(ds)} from {_rel(ds)} d{where}", [records])
    return {key: (name or key) for key, name in cur.fetchall()}


def search_rows(cur, ds: Dataset, text: str, limit: int = 50) -> list[dict]:
    """What a link's picker offers. Searching by name is what people do; the key helps when two rows have the same name."""
    from webapp.explore.query import _like, not_hidden
    name = _name_expr(ds)
    where, params = "", []
    if text.strip():
        where, params = f" where lower({name}) like %s", [_like(text.strip())]
    where = not_hidden(ds, where)
    cur.execute(f"select {key_text(ds)}, {name} from {_rel(ds)} d{where} order by {name} nulls last, 1 limit %s", [*params, limit])
    return [{"id": key, "label": label or key} for key, label in cur.fetchall()]


# ── links ────────────────────────────────────────────────────────────────────────────────────────────────────
def base_link(f: Field) -> Field:
    return f.link_field if f.is_reverse_link else f


def linked_rows(cur, f: Field, record: str) -> list[dict]:
    if f.is_reverse_link:
        cur.execute("select from_record, from_label from userdata.links where field_id = %s and to_record = %s order by id", [f.link_field_id, record])
    else:
        cur.execute("select to_record, to_label from userdata.links where field_id = %s and from_record = %s order by id", [f.pk, record])
    return [{"id": key, "label": label or key} for key, label in cur.fetchall()]


def set_links(cur, ds: Dataset, f: Field, record: str, wanted: list[str], who: str, *, row_name: str | None = None) -> dict:
    """Makes `record`'s links through this field exactly `wanted`. From a link's other side too, and when a link allows one
    row only, choosing a row here takes it away from where it was."""
    link = base_link(f)
    other = f.link_dataset
    wanted = list(dict.fromkeys(str(w) for w in wanted))
    if len(wanted) > MAX_LINKS:
        raise StoreProblem(f"That's {len(wanted)} rows; a cell can link to at most {MAX_LINKS}.")
    if f.kind == Field.RECORDS and not f.is_reverse_link and f.link_single and len(wanted) > 1:
        raise StoreProblem(f"{f.label} links to one row only.")
    names = row_names(cur, other, wanted)
    missing = [w for w in wanted if w not in names]
    if missing:
        raise StoreProblem(f"{len(missing)} of those rows aren't there any more.", 404)
    me = row_name if row_name is not None else row_names(cur, ds, [record]).get(record)
    if me is None:
        raise StoreProblem("That row isn't there any more.", 404)
    before = linked_rows(cur, f, record)
    had = {r["id"] for r in before}
    gone, added = had - set(wanted), [w for w in wanted if w not in had]
    if not gone and not added:
        return {"changed": False, "labels": [r["label"] for r in before]}
    if f.is_reverse_link:
        if gone:
            cur.execute("delete from userdata.links where field_id = %s and to_record = %s and from_record = any(%s)", [link.pk, record, list(gone)])
        for key in added:
            if link.link_single:
                cur.execute("delete from userdata.links where field_id = %s and from_record = %s", [link.pk, key])
            cur.execute("""insert into userdata.links (field_id, from_record, to_record, from_label, to_label, created_by)
                           values (%s, %s, %s, %s, %s, %s) on conflict do nothing""", [link.pk, key, record, names[key], me, who])
    else:
        if gone:
            cur.execute("delete from userdata.links where field_id = %s and from_record = %s and to_record = any(%s)", [link.pk, record, list(gone)])
        for key in added:
            cur.execute("""insert into userdata.links (field_id, from_record, to_record, from_label, to_label, created_by)
                           values (%s, %s, %s, %s, %s, %s) on conflict do nothing""", [link.pk, record, key, me, names[key], who])
    after = [r["label"] for r in linked_rows(cur, f, record)]
    edits.add_history(cur, ds.record_table, record, f.path, [r["label"] for r in before] or None, after or None, who, "link")
    return {"changed": True, "labels": after}
