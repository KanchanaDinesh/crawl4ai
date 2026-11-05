"""Copy every DuckDB table into PostgreSQL (step 2 of docs/postgres-migration.md).

    uv run python -m warehouse copy-from-duckdb                    all tools
    uv run python -m warehouse copy-from-duckdb --tool companies   one tool (repeatable)
    uv run python -m warehouse copy-from-duckdb --replace          copy again: empties the PostgreSQL tables first
    uv run python -m warehouse copy-from-duckdb --check            only compare, copy nothing

The DuckDB files are only read, never changed. Each tool is copied in one transaction: if any table fails
or its check doesn't match, nothing of that tool is kept, and readers never see half a copy.

The check per table: the number of rows, and the newest time in it (e.g. the latest run), must be the same
in DuckDB and PostgreSQL.

Two things are changed on the way, and counted in the report:
  - PostgreSQL can't store the character NUL (\\x00) in text, nor \\u0000 in JSON: they are removed.
  - Plain DuckDB timestamps (techintel's created_at, in the Mac's local time) become timestamptz.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime

from . import DB_FILES, connect as duck_connect
from . import postgres

BATCH = 5000
# the column whose newest value is compared, first match wins (else the first timestamp column)
NEWEST_COLUMNS = ("last_seen", "last_seen_at", "checked_at", "run_at", "saved_at", "started_at", "created_at",
                  "updated_at", "refused_at")


@dataclass
class TableResult:
    table: str
    duck_rows: int = 0
    pg_rows: int | None = None
    newest_column: str | None = None
    duck_newest: datetime | None = None
    pg_newest: datetime | None = None
    cleaned: int = 0                    # values with NUL characters removed
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.duck_rows == self.pg_rows and self.duck_newest == self.pg_newest


@dataclass
class ToolResult:
    schema: str
    path: str
    tables: list[TableResult] = field(default_factory=list)
    error: str | None = None
    copied: bool = False
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None and all(t.ok for t in self.tables)


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _duck_columns(duck, table: str) -> list[tuple[str, str]]:
    return duck.execute("select column_name, data_type from duckdb_columns() where table_name = ? "
                        "order by column_index", [table]).fetchall()


def _select_list(cols: list[tuple[str, str]]) -> str:
    """JSON as text; plain TIMESTAMP as timestamptz (DuckDB reads it in its session time zone, the Mac's)."""
    out = []
    for name, dtype in cols:
        if dtype == "JSON":
            out.append(f"{_q(name)}::varchar")
        elif dtype == "TIMESTAMP":
            out.append(f"{_q(name)}::timestamptz")
        else:
            out.append(_q(name))
    return ", ".join(out)


def _clean(value, is_json: bool, counter: list[int]):
    """Remove what PostgreSQL refuses: NUL in text, and the \\u0000 escape in JSON."""
    if isinstance(value, str):
        fixed = value.replace("\x00", "")
        if is_json:
            fixed = fixed.replace("\\u0000", "")
        if fixed != value:
            counter[0] += 1
        return fixed
    if isinstance(value, list):
        return [_clean(v, False, counter) for v in value]
    return value


def _newest_column(pg_cols: dict[str, str]) -> str | None:
    for name in NEWEST_COLUMNS:
        if pg_cols.get(name, "").startswith("timestamp"):
            return name
    return next((n for n, t in pg_cols.items() if t.startswith("timestamp")), None)


def _pg_columns(pg, schema: str, table: str) -> dict[str, str]:
    rows = pg.execute("select column_name, data_type from information_schema.columns "
                      "where table_schema = %s and table_name = %s order by ordinal_position",
                      [schema, table]).fetchall()
    return dict(rows)


def _duck_newest(duck, table: str, column: str, dtype: str):
    expr = f"{_q(column)}::timestamptz" if dtype == "TIMESTAMP" else _q(column)
    return duck.execute(f"select max({expr}) from {_q(table)}").fetchone()[0]


def _reset_sequences(pg, schema: str) -> None:
    """Sequences continue after the highest copied id (techintel's row_seq is shared by all its tables)."""
    rows = pg.execute("""select table_name, column_name, column_default from information_schema.columns
                         where table_schema = %s and column_default like 'nextval(%%'""", [schema]).fetchall()
    highest: dict[str, int] = {}
    for table, column, default in rows:
        seq = default.split("'")[1]
        top = pg.execute(f"select max({_q(column)}) from {_q(schema)}.{_q(table)}").fetchone()[0] or 0
        highest[seq] = max(highest.get(seq, 0), top)
    for seq, top in highest.items():
        if top:
            pg.execute("select setval(%s, %s)", [seq, top])


def copy_tool(schema: str, replace: bool = False, check_only: bool = False) -> ToolResult:
    from psycopg import sql
    path = DB_FILES[schema]
    result = ToolResult(schema, str(path))
    t0 = time.monotonic()
    if not path.exists():
        result.error = "no DuckDB file (nothing to copy)"
        return result
    try:
        duck = duck_connect(path, read_only=True, wait_seconds=10)
    except RuntimeError:
        result.error = "a run is writing to the DuckDB file; copy this tool again when it has finished"
        return result
    duck.execute("set enable_progress_bar = false")     # it would draw over our report
    try:
        postgres.migrate(schemas=[schema])
        with postgres.connect("writer") as pg:
            tables = [r[0] for r in duck.execute(
                "select table_name from duckdb_tables() order by table_name").fetchall()]
            for table in tables:
                result.tables.append(_copy_table(duck, pg, schema, table, replace, check_only))
            if result.ok and not check_only:
                _reset_sequences(pg, schema)
                pg.commit()
                result.copied = True
            else:
                pg.rollback()       # all or nothing per tool
    finally:
        duck.close()
        result.seconds = round(time.monotonic() - t0, 1)
    return result


def _copy_table(duck, pg, schema: str, table: str, replace: bool, check_only: bool) -> TableResult:
    from psycopg import sql
    res = TableResult(table)
    duck_cols = _duck_columns(duck, table)
    res.duck_rows = duck.execute(f"select count(*) from {_q(table)}").fetchone()[0]
    pg_cols = _pg_columns(pg, schema, table)
    if not pg_cols:
        res.error = f"no PostgreSQL table {schema}.{table} (add it to warehouse/schema/{schema}.sql)"
        return res
    missing = [n for n, _ in duck_cols if n not in pg_cols]
    if missing:
        res.error = f"PostgreSQL table has no column(s) {', '.join(missing)} (warehouse/schema/{schema}.sql)"
        return res
    target = sql.Identifier(schema, table)
    existing = pg.execute(sql.SQL("select count(*) from {}").format(target)).fetchone()[0]

    if not check_only:
        if existing and not replace:
            res.error = f"already has {existing:,} rows in PostgreSQL; to copy again use --replace"
            res.pg_rows = existing
            return res
        if existing:
            pg.execute(sql.SQL("truncate {}").format(target))
        names = [n for n, _ in duck_cols]
        json_cols = [dtype == "JSON" for _, dtype in duck_cols]
        counter = [0]
        cur = duck.execute(f"select {_select_list(duck_cols)} from {_q(table)}")
        copy_sql = sql.SQL("copy {} ({}) from stdin").format(target, sql.SQL(", ").join(map(sql.Identifier, names)))
        with pg.cursor().copy(copy_sql) as cp:
            while rows := cur.fetchmany(BATCH):
                for row in rows:
                    cp.write_row([_clean(v, j, counter) for v, j in zip(row, json_cols)])
        res.cleaned = counter[0]

    res.pg_rows = pg.execute(sql.SQL("select count(*) from {}").format(target)).fetchone()[0]
    res.newest_column = _newest_column(pg_cols)
    if res.newest_column:
        dtype = dict(duck_cols).get(res.newest_column, "")
        res.duck_newest = _duck_newest(duck, table, res.newest_column, dtype)
        res.pg_newest = pg.execute(sql.SQL("select max({}) from {}").format(
            sql.Identifier(res.newest_column), target)).fetchone()[0]
    if not res.ok and not res.error:
        res.error = "PostgreSQL doesn't match DuckDB (see the numbers)"
    return res


def copy_all(schemas=None, replace: bool = False, check_only: bool = False) -> list[ToolResult]:
    return [copy_tool(s, replace, check_only) for s in (schemas or postgres.SCHEMAS)]


def report(results: list[ToolResult], check_only: bool = False) -> str:
    lines = []
    for r in results:
        state = "OK" if r.ok else "FAILED"
        what = "compared" if check_only else ("copied" if r.copied else "not copied")
        lines.append(f"{r.schema}: {state}, {what} in {r.seconds:.1f}s  ({r.path})")
        if r.error:
            lines.append(f"  {r.error}")
        for t in r.tables:
            mark = "ok " if t.ok else "BAD"
            pg_rows = "-" if t.pg_rows is None else f"{t.pg_rows:,}"
            newest = ""
            if t.newest_column:
                newest = f"  newest {t.newest_column} {_fmt(t.duck_newest)}"
                if t.duck_newest != t.pg_newest:
                    newest += f" vs {_fmt(t.pg_newest)}"
            cleaned = f"  ({t.cleaned} values had NUL characters removed)" if t.cleaned else ""
            lines.append(f"  [{mark}] {t.table:<18} DuckDB {t.duck_rows:>9,}  PostgreSQL {pg_rows:>9}{newest}{cleaned}")
            if t.error and not t.ok and t.error != "PostgreSQL doesn't match DuckDB (see the numbers)":
                lines.append(f"        {t.error}")
    return "\n".join(lines)


def _fmt(value) -> str:
    if value is None:
        return "none"
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value)
