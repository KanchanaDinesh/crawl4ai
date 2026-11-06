"""The automatic catalog: look at every table and view in the data schemas and write down what's there.

Run after collectors (run_all step `catalog`), from the catalog page, or: python -m webapp catalog_scan

For each dataset: real columns from PostgreSQL, keys inside JSONB columns (a collector can add one any day), how
often each is filled, and a value list for fields with few different values (those get a tick-list filter). All
of this looks at no more than SAMPLE_ROWS rows, so a scan stays quick however big a table grows; a value that only
appears later in a huge table can be missing from a list until it's in the sample.
"""

import logging
import re

from django.contrib.auth.models import Group
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from warehouse.postgres import SCHEMAS

from .defaults import KNOWN, STARTING_ROLES_EXPORT, STARTING_ROLES_VIEW, STARTING_ROLES_EDIT
from .models import Dataset, DatasetRight, Field
from .sqlnames import lit, q, relation

logger = logging.getLogger("webapp.catalog")

SAMPLE_ROWS = 100_000
MAX_VALUES = 300            # more different values than this: no tick list (searching the text works anyway)
MAX_VALUE_LENGTH = 60
MAX_JSON_KEYS = 60          # more keys than this in one column: it's data (e.g. errors per source), not fields
SHOWN_BY_DEFAULT = 12       # columns on screen until someone chooses others
SCAN_TIMEOUT = "60s"

NUMBER_TYPES = {"smallint", "integer", "bigint", "numeric", "real", "double precision"}
WORDS = {"id": "ID", "url": "URL", "lkr": "LKR", "pct": "%", "http": "HTTP", "lei": "LEI", "dy": "DY", "pbv": "PBV",
         "per": "PER", "ath": "ATH"}


def humanize(name: str) -> str:
    """first_seen -> First seen; price_lkr -> Price LKR; marketCap -> Market cap."""
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name.replace("_", " ")).strip()
    words = [WORDS.get(w.lower(), w.lower()) for w in name.split()]
    if not words:
        return name
    first = words[0]
    words[0] = first if first != first.lower() else first.capitalize()
    return " ".join(words)


def _rows(cur, sql: str, params=None) -> list[tuple]:
    cur.execute(sql, params or [])
    return cur.fetchall()


def _relations(cur) -> list[tuple[str, str, str]]:
    return _rows(cur, """select table_schema, table_name, case table_type when 'VIEW' then 'view' else 'table' end
        from information_schema.tables where table_schema = any(%s) order by 1, 2""", [list(SCHEMAS)])


def _columns(cur, schema: str, name: str) -> list[tuple[str, str]]:
    return _rows(cur, """select column_name, data_type from information_schema.columns
        where table_schema = %s and table_name = %s order by ordinal_position""", [schema, name])


def _primary_key(cur, schema: str, name: str) -> list[str]:
    return [r[0] for r in _rows(cur, """select a.attname from pg_index i
        join pg_class c on c.oid = i.indrelid join pg_namespace n on n.oid = c.relnamespace
        join pg_attribute a on a.attrelid = c.oid and a.attnum = any(i.indkey)
        where i.indisprimary and n.nspname = %s and c.relname = %s order by array_position(i.indkey, a.attnum)""",
                                    [schema, name])]


def _base_type(data_type: str) -> str:
    if data_type in NUMBER_TYPES:
        return Field.NUMBER
    if data_type.startswith("timestamp") or data_type == "date":
        return Field.DATETIME
    if data_type == "boolean":
        return Field.BOOL
    if data_type == "ARRAY":
        return Field.LIST
    if data_type in ("json", "jsonb"):
        return Field.JSON
    return Field.TEXT


def _text_details(cur, sample: str, expr: str) -> tuple[str, list[str]]:
    """Is a text field a link, and does it have few enough different values for a tick list?"""
    links, filled, longest = _rows(cur, f"""select count(*) filter (where v ~* '^https?://'), count(*),
        coalesce(max(length(v)), 0) from (select {expr} as v from {sample} s) t
        where v is not null and v <> ''""")[0]
    if filled and links >= filled * 0.8:
        return Field.LINK, []
    if not filled or longest > MAX_VALUE_LENGTH:
        return Field.TEXT, []
    found = _rows(cur, f"""select v from (select {expr} as v from {sample} s) t where v is not null and v <> ''
        group by 1 order by count(*) desc, 1 limit {MAX_VALUES + 1}""")
    distinct = len(found)
    if distinct > MAX_VALUES or distinct > max(12, filled * 0.5):
        return Field.TEXT, []
    return Field.TEXT, sorted((r[0] for r in found), key=str.casefold)


def _list_values(cur, sample: str, expr: str, is_jsonb: bool) -> list[str]:
    each = f"jsonb_array_elements_text({expr})" if is_jsonb else f"unnest({expr})"
    guard = f"where jsonb_typeof({expr}) = 'array'" if is_jsonb else ""
    found = _rows(cur, f"""select v from (select {each} as v from {sample} s {guard}) t where v is not null
        group by 1 order by count(*) desc, 1 limit {MAX_VALUES + 1}""")
    return [] if len(found) > MAX_VALUES else sorted((r[0] for r in found), key=str.casefold)


def _json_array_of_text(cur, sample: str, expr: str) -> bool:
    arrays, others = _rows(cur, f"""select count(*) filter (where jsonb_typeof(v) = 'array'),
        count(*) filter (where jsonb_typeof(v) not in ('array', 'null')) from (select {expr} as v from {sample} s) t
        where v is not null""")[0]
    if not arrays or others:
        return False
    bad = _rows(cur, f"""select count(*) from (select {expr} as v from {sample} s) t,
        jsonb_array_elements(case when jsonb_typeof(v) = 'array' then v else '[]'::jsonb end) e
        where jsonb_typeof(e) <> 'string'""")[0][0]
    return bad == 0


def _json_keys(cur, sample: str, col: str, total: int) -> list[dict]:
    """The keys found inside a JSONB column, with their type, most filled first."""
    c = q(col)
    found = _rows(cur, f"""select k, jsonb_typeof({c} -> k), count(*) from {sample} s,
        jsonb_object_keys(case when jsonb_typeof({c}) = 'object' then {c} else '{{}}'::jsonb end) k
        where jsonb_typeof({c} -> k) <> 'null' group by 1, 2""")
    keys: dict[str, dict] = {}
    for key, typ, n in found:
        e = keys.setdefault(key, {"key": key, "n": 0, "types": set()})
        e["n"] += n
        e["types"].add(typ)
    return sorted(keys.values(), key=lambda e: (-e["n"], e["key"]))


def _json_field(cur, sample: str, col: str, entry: dict, total: int) -> dict:
    c, key = q(col), entry["key"]
    types, text = entry["types"], f"{c} ->> {lit(key)}"
    spec = {"path": f"{col}.{key}", "column": col, "json_key": key, "is_jsonb": True,
            "fill_pct": round(100 * entry["n"] / max(total, 1)), "values": []}
    if types == {"number"}:
        spec["type"] = Field.NUMBER
    elif types == {"boolean"}:
        spec["type"] = Field.BOOL
    elif types == {"string"}:
        share = _rows(cur, f"""select count(*) filter (where v ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}([T ]|$)'), count(*)
            from (select {text} as v from {sample} s) t where v is not null and v <> ''""")[0]
        if share[1] and share[0] >= share[1] * 0.95:
            spec["type"] = Field.DATETIME
        else:
            spec["type"], spec["values"] = _text_details(cur, sample, text)
    elif types == {"array"} and _json_array_of_text(cur, sample, f"{c} -> {lit(key)}"):
        spec["type"] = Field.LIST
        spec["values"] = _list_values(cur, sample, f"{c} -> {lit(key)}", True)
    elif types <= {"array", "object"}:
        spec["type"] = Field.JSON
    else:
        spec["type"] = Field.TEXT
    return spec


def _field_specs(cur, schema: str, name: str, total: int) -> list[dict]:
    rel = relation(schema, name)
    sample = f"(select * from {rel} limit {SAMPLE_ROWS})"
    columns = _columns(cur, schema, name)
    if not columns:
        return []
    fills = _rows(cur, "select " + ", ".join(f"count({q(c)})" for c, _ in columns) + f" from {sample} s")[0]
    rows_seen = min(total, SAMPLE_ROWS)
    specs, json_specs = [], []
    for (col, data_type), filled in zip(columns, fills):
        typ = _base_type(data_type)
        spec = {"path": col, "column": col, "json_key": None, "type": typ, "values": [],
                "is_jsonb": typ == Field.JSON, "fill_pct": round(100 * filled / max(rows_seen, 1))}
        if typ == Field.TEXT:
            spec["type"], spec["values"] = _text_details(cur, sample, q(col))
        elif typ == Field.LIST:
            spec["values"] = _list_values(cur, sample, q(col), False)
        elif typ == Field.JSON:
            if data_type == "jsonb":
                keys = _json_keys(cur, sample, col, rows_seen)
                if 0 < len(keys) <= MAX_JSON_KEYS:
                    json_specs += [_json_field(cur, sample, col, e, rows_seen) for e in keys]
                elif not keys and _json_array_of_text(cur, sample, q(col)):
                    spec["type"] = Field.LIST
                    spec["values"] = _list_values(cur, sample, q(col), True)
            else:
                spec["is_jsonb"] = False
        specs.append(spec)
    return specs + json_specs


def _label(spec: dict, taken: set[str]) -> str:
    """A label nobody else in this dataset has (the column chooser and exports need them to differ)."""
    key = spec["json_key"]
    label = humanize(key if key else spec["column"])
    if label.lower() in taken and key:
        label = f"{humanize(spec['column'])}: {label}"
    base, n = label, 2
    while label.lower() in taken:
        label, n = f"{base} {n}", n + 1
    taken.add(label.lower())
    return label


def _save_fields(ds: Dataset, specs: list[dict], total: int) -> int:
    existing = {f.path: f for f in ds.fields.all()}
    hide = KNOWN.get(ds.ref, {}).get("hide", [])
    taken = {f.label.lower() for f in existing.values()}
    shown = sum(1 for f in existing.values() if f.shown and f.path in {s["path"] for s in specs})
    added, next_order = 0, max((f.order for f in existing.values()), default=0)
    for spec in specs:
        f = existing.get(spec["path"])
        if f is None:
            next_order += 10
            filled_enough = spec["fill_pct"] >= 5 or not total     # an empty table: nothing to judge by yet
            show = (spec["type"] != Field.JSON and filled_enough and spec["path"] not in hide
                    and shown < SHOWN_BY_DEFAULT)
            shown += 1 if show else 0
            target = ds.edit_target
            editable = bool(target and not spec.get("json_key") and spec["column"] in target.columns)
            Field.objects.create(dataset=ds, label=_label(spec, taken), order=next_order, shown=show, editable=editable, **spec)
            added += 1
        else:
            for k in ("type", "is_jsonb", "fill_pct", "values"):
                setattr(f, k, spec[k])
            f.missing = False
            f.save()
    wanted = {s["path"] for s in specs}
    ds.fields.exclude(path__in=wanted).update(missing=True)
    return added


def _new_dataset(schema: str, name: str, kind: str, primary_key: list[str]) -> Dataset:
    known = KNOWN.get(f"{schema}.{name}")
    sort = (known or {}).get("sort", ("", "asc"))
    ds = Dataset.objects.create(
        schema=schema, name=name, kind=kind, label=(known or {}).get("label") or humanize(name.removeprefix("v_")),
        description=(known or {}).get("description", ""), enabled=not (known or {}).get("off", False),
        key_columns=primary_key or (known or {}).get("key", []), default_sort_field=sort[0], default_sort_dir=sort[1])
    if known and not known.get("off"):      # datasets we know start with the plan's rights; unknown ones with none
        for group in Group.objects.filter(name__in=STARTING_ROLES_VIEW):
            edit = group.name in STARTING_ROLES_EDIT and ds.edit_target is not None
            DatasetRight.objects.create(dataset=ds, group=group, can_view=True, can_edit=edit, can_hide=edit,
                                        can_export=group.name in STARTING_ROLES_EXPORT)
    return ds


def scan_one(cur, schema: str, name: str, kind: str) -> dict:
    """One table or view: returns what's new. Saved in one transaction: readers never see half a scan."""
    rel = relation(schema, name)
    with transaction.atomic():
        cur.execute(f"set local statement_timeout = '{SCAN_TIMEOUT}'")
        total = _rows(cur, f"select count(*) from {rel}")[0][0]
        specs = _field_specs(cur, schema, name, total)
        ds = Dataset.objects.filter(schema=schema, name=name).first()
        created = ds is None
        if created:
            ds = _new_dataset(schema, name, kind, _primary_key(cur, schema, name))
        ds.kind, ds.row_count, ds.missing, ds.scanned_at = kind, total, False, timezone.now()
        ds.save()
        added = _save_fields(ds, specs, total)
    return {"ref": ds.ref, "new": created, "fields_added": added, "rows": total}


def scan() -> dict:
    """Every dataset. One that fails (too slow, or being rebuilt) is reported and the rest still get scanned."""
    summary = {"datasets": 0, "new": [], "new_fields": 0, "missing": [], "errors": {}}
    with connection.cursor() as cur:
        found = _relations(cur)
        for schema, name, kind in found:
            try:
                r = scan_one(cur, schema, name, kind)
            except DatabaseError as e:
                ref = f"{schema}.{name}"
                logger.warning("catalog: %s couldn't be scanned: %s", ref, e)
                summary["errors"][ref] = str(e).strip().splitlines()[0][:200]
                continue
            summary["datasets"] += 1
            summary["new_fields"] += r["fields_added"]
            if r["new"]:
                summary["new"].append(r["ref"])
    seen = {(s, n) for s, n, _ in found}
    for ds in Dataset.objects.filter(missing=False):
        if (ds.schema, ds.name) not in seen:
            ds.missing = True
            ds.save(update_fields=["missing"])
            summary["missing"].append(ds.ref)
    return summary


def describe(summary: dict) -> str:
    text = f"{summary['datasets']} datasets scanned, {len(summary['new'])} new, {summary['new_fields']} new fields"
    if summary["new"]:
        text += f" (new: {', '.join(summary['new'][:10])})"
    if summary["missing"]:
        text += f"; gone: {', '.join(summary['missing'][:10])}"
    if summary["errors"]:
        text += f"; {len(summary['errors'])} couldn't be scanned: {', '.join(list(summary['errors'])[:5])}"
    return text
