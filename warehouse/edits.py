"""People's corrections to collected data, and how collectors respect them (docs/user-platform-plan.md, "rule A").

Corrections are made in the main tables, so everything that reads the data sees them. Without more, the next run of
a collector would put the old value straight back. So audit.field_source remembers, per corrected field, the last value
the source gave, and when a collector brings a value:

    the source says what it said before   the correction stays (the source only repeats itself)
    the source says something new         rule A: the new value is saved, the correction stays in the history (default)
                                          rule B: the person wins, the correction stays
                                          rule C: the correction stays and the new value waits for a person to look at
    the source says nothing               the correction stays

No Django here, because collectors use it too. Functions take a DB-API cursor: psycopg's or Django's.
"""

import json
from dataclasses import dataclass, field

HISTORY, SOURCE, HIDDEN = "audit.field_history", "audit.field_source", "audit.hidden_records"
RULES = {"A": "Newest change wins", "B": "The person's value wins", "C": "Flag for review"}
DEFAULT_RULE = "A"


@dataclass(frozen=True)
class Target:
    """A table people may correct. `columns` is the ceiling: PostgreSQL grants the web app these columns and no others,
    and the catalog can only offer these."""
    table: str
    key: str
    columns: tuple[str, ...]
    label: str                                     # the column that names a record, in lists of hidden records
    linked: dict = field(default_factory=dict)     # column -> the column that follows it (a website's domain)
    required: tuple[str, ...] = ()
    latest_by: str | None = None                   # one row per run: a correction goes on the latest row


TARGETS = {t.table: t for t in (
    Target("companies.companies", "id", ("name", "country", "website", "industry"), "name",
           linked={"website": "domain"}, required=("name",)),
    Target("vehicles.ads", "url", ("title", "price_lkr", "location", "year", "mileage"), "title", latest_by="saved_at"),
)}
EDITABLE_DATASETS = {"companies.companies": "companies.companies", "companies.v_companies": "companies.companies",
                     "vehicles.v_vehicle_ads_latest": "vehicles.ads"}
# Read-only lists of the same records: what's hidden must not show there either
HIDE_ONLY_DATASETS = {"vehicles.ads": "vehicles.ads"}


def target_for(ref: str) -> Target | None:
    table = EDITABLE_DATASETS.get(ref)
    return TARGETS[table] if table else None


def hide_target_for(ref: str) -> Target | None:
    table = EDITABLE_DATASETS.get(ref) or HIDE_ONLY_DATASETS.get(ref)
    return TARGETS[table] if table else None


def protected_columns(target: Target) -> tuple[str, ...]:
    return target.columns + tuple(target.linked.values())


def linked_value(target: Target, column: str, value):
    """The value of the column that follows `column`: a website's root domain (so `companies update` reads the right site)."""
    if target.linked.get(column) == "domain":
        from techintel.domains import root_domain
        return root_domain(value) if value else None
    return value


def available(cur) -> bool:
    """Whether audit's tables exist yet (a database that hasn't run `warehouse migrate` since this was added)."""
    cur.execute("select to_regclass('audit.field_source') is not null and has_table_privilege(to_regclass('audit.field_source'), 'select')")
    return bool(cur.fetchone()[0])


def _canon(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    return str(value).strip()


def same(a, b) -> bool:
    """Equal as far as a person can tell: 9100000 and 9100000.0; "" and nothing."""
    a, b = _canon(a), _canon(b)
    return (None if a == "" else a) == (None if b == "" else b)


def _json(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False).replace("\\u0000", "")


def _load(text):
    return None if text is None else json.loads(text)


def add_history(cur, dataset, record, field_name, old, new, who, origin, note="") -> None:
    cur.execute(f"""insert into {HISTORY} (dataset, record, field, old_value, new_value, changed_by, origin, note)
                    values (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)""",
                [dataset, record, field_name, _json(old), _json(new), who, origin, note or None])


# ── a person changes a field (the web app) ───────────────────────────────────────────────────────────────────
def note_user_edit(cur, dataset, record, field_name, old, new, who, rule=DEFAULT_RULE, origin="user", note="") -> None:
    """Call in the transaction that changes the value. The value before the first correction is what the source gave.
    Setting a field back to that means there's no correction any more, so nothing is left to protect."""
    add_history(cur, dataset, record, field_name, old, new, who, origin, note)
    cur.execute(f"""select source_value::text, pending_value::text from {SOURCE}
                    where dataset = %s and record = %s and field = %s for update""", [dataset, record, field_name])
    row = cur.fetchone()
    source = _load(row[0]) if row else old
    if row and row[1] is not None:                 # they've decided, having seen what the source says now
        source = _load(row[1])
    if same(new, source):
        cur.execute(f"delete from {SOURCE} where dataset = %s and record = %s and field = %s", [dataset, record, field_name])
        return
    cur.execute(f"""insert into {SOURCE} (dataset, record, field, source_value, user_value, rule, pending_value, edited_by, edited_at)
                    values (%s, %s, %s, %s::jsonb, %s::jsonb, %s, null, %s, now())
                    on conflict (dataset, record, field) do update set source_value = excluded.source_value,
                        user_value = excluded.user_value, rule = excluded.rule, pending_value = null,
                        edited_by = excluded.edited_by, edited_at = excluded.edited_at""",
                [dataset, record, field_name, _json(source), _json(new), rule, who])


def clear_pending(cur, dataset, record, field_name, source_value) -> None:
    """Rule C: a person chose to keep their value: what the source says now is what it said before, from here on."""
    cur.execute(f"""update {SOURCE} set source_value = %s::jsonb, pending_value = null
                    where dataset = %s and record = %s and field = %s""", [_json(source_value), dataset, record, field_name])


# ── a collector brings values ────────────────────────────────────────────────────────────────────────────────
def decide(rule: str, source_value, user_value, incoming) -> str:
    """keep: the correction stays. take: save what the source says. agree: the source now says what the person said."""
    if incoming is None or same(incoming, source_value):
        return "keep"
    if same(incoming, user_value):
        return "agree"
    return {"B": "keep_newer", "C": "flag"}.get(rule, "take")


def _corrections(cur, table: str, records: list[str]) -> dict[str, dict]:
    if not records:
        return {}
    cur.execute(f"""select record, field, source_value::text, user_value::text, rule, pending_value::text from {SOURCE}
                    where dataset = %s and record = any(%s)""", [table, records])
    found: dict[str, dict] = {}
    for record, name, source, user, rule, pending in cur.fetchall():
        found.setdefault(record, {})[name] = {"source": _load(source), "user": _load(user), "rule": rule, "pending": _load(pending)}
    return found


def _resolve(cur, target: Target, record: str, corrections: dict, incoming: dict, who: str) -> dict:
    """What to save for this record's corrected fields, and the bookkeeping that goes with it. A column and the one that
    follows it go together: when the source really changes a website, its domain goes with it."""
    table = target.table
    decisions = {name: decide(c["rule"], c["source"], c["user"], incoming.get(name)) for name, c in corrections.items()}
    for column, follower in target.linked.items():
        if follower in decisions and decisions.get(column) == "take":
            decisions[follower] = "take"
    values = {}
    for name, c in corrections.items():
        new, what = incoming.get(name), decisions[name]
        where = [table, record, name]
        if what == "keep":
            values[name] = c["user"]
        elif what == "keep_newer":
            values[name] = c["user"]
            cur.execute(f"update {SOURCE} set source_value = %s::jsonb where dataset = %s and record = %s and field = %s", [_json(new), *where])
        elif what == "flag":
            values[name] = c["user"]
            if not same(new, c["pending"]):
                cur.execute(f"update {SOURCE} set pending_value = %s::jsonb where dataset = %s and record = %s and field = %s", [_json(new), *where])
                add_history(cur, table, record, name, c["user"], c["user"], who, "collector", f"The source now says {new}; waiting for review")
        else:
            cur.execute(f"delete from {SOURCE} where dataset = %s and record = %s and field = %s", where)
            if what == "take":
                add_history(cur, table, record, name, c["user"], new, who, "collector", "The source changed, so its new value replaced the correction (rule A)")
    return values


def protect_rows(cur, table: str, rows: list[dict], who: str) -> int:
    """For collectors that add rows: put corrections into `rows` (dicts, changed in place) before they're saved.
    Returns how many fields a correction was kept in."""
    target = TARGETS[table]
    keys = [str(r[target.key]) for r in rows if r.get(target.key)]
    found = _corrections(cur, table, keys)
    kept = 0
    for r in rows:
        record = str(r.get(target.key) or "")
        if record in found:
            values = _resolve(cur, target, record, found[record], r, who)
            r.update(values)
            kept += len(values)
    return kept


def protect_staged(cur, table: str, staged: str, who: str) -> int:
    """For collectors that load a batch into a temporary table first: the same, in that table. `staged` is our own
    name, and corrections are few, so a batch of 27,000 rows costs one query."""
    target = TARGETS[table]
    columns = protected_columns(target)
    cur.execute(f"""select s.{target.key}, {', '.join('s.' + c for c in columns)} from {staged} s
                    where exists (select 1 from {SOURCE} f where f.dataset = %s and f.record = s.{target.key})""", [table])
    incoming = {row[0]: dict(zip(columns, row[1:])) for row in cur.fetchall()}
    found = _corrections(cur, table, list(incoming))
    kept = 0
    for record, corrections in found.items():
        values = _resolve(cur, target, record, corrections, incoming[record], who)
        for name, value in values.items():
            cur.execute(f"update {staged} set {name} = %s where {target.key} = %s", [value, record])
        kept += len(values)
    return kept


# ── reading ──────────────────────────────────────────────────────────────────────────────────────────────────
def marks(cur, table: str, records: list[str]) -> dict[str, dict]:
    """Which fields of these records are corrected, and which wait for review: {record: {"edited": [...], "pending": [...]}}."""
    out: dict[str, dict] = {}
    if not records:
        return out
    cur.execute(f"select record, field, pending_value is not null from {SOURCE} where dataset = %s and record = any(%s)", [table, records])
    for record, name, pending in cur.fetchall():
        entry = out.setdefault(record, {"edited": [], "pending": []})
        entry["edited"].append(name)
        if pending:
            entry["pending"].append(name)
    return out


def corrections_of(cur, table: str, record: str) -> dict[str, dict]:
    return _corrections(cur, table, [record]).get(record, {})


def history_of(cur, table: str, record: str | None = None, limit: int = 200) -> list[dict]:
    where, params = "dataset = %s", [table]
    if record is not None:
        where += " and record = %s"
        params.append(record)
    cur.execute(f"""select id, record, field, old_value::text, new_value::text, changed_by, changed_at, origin, note
                    from {HISTORY} where {where} order by id desc limit %s""", [*params, limit])
    return [{"id": i, "record": rec, "field": name, "old": _load(old), "new": _load(new), "by": by, "at": at, "origin": origin, "note": note}
            for i, rec, name, old, new, by, at, origin, note in cur.fetchall()]


def history_entry(cur, table: str, record: str, entry_id: int) -> dict | None:
    cur.execute(f"""select field, old_value::text, new_value::text from {HISTORY}
                    where id = %s and dataset = %s and record = %s""", [entry_id, table, record])
    row = cur.fetchone()
    return {"field": row[0], "old": _load(row[1]), "new": _load(row[2])} if row else None


def set_rule(cur, table: str, field_name: str, rule: str) -> None:
    """The catalog changed a field's rule: corrections already made follow it."""
    cur.execute(f"update {SOURCE} set rule = %s where dataset = %s and field = %s", [rule, table, field_name])


# ── hiding ───────────────────────────────────────────────────────────────────────────────────────────────────
def hide(cur, table: str, record: str, label: str, who: str) -> bool:
    cur.execute(f"""insert into {HIDDEN} (dataset, record, label, hidden_by) values (%s, %s, %s, %s)
                    on conflict (dataset, record) do nothing""", [table, record, label, who])
    return cur.rowcount == 1


def unhide(cur, table: str, record: str) -> bool:
    cur.execute(f"delete from {HIDDEN} where dataset = %s and record = %s", [table, record])
    return cur.rowcount == 1


def hidden_list(cur, table: str, limit: int = 500) -> list[dict]:
    cur.execute(f"select record, label, hidden_by, hidden_at from {HIDDEN} where dataset = %s order by hidden_at desc limit %s", [table, limit])
    return [{"record": r, "label": label, "by": by, "at": at} for r, label, by, at in cur.fetchall()]
