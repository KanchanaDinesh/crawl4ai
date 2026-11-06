"""PostgreSQL: the database every tool is moving to (plan: docs/postgres-migration.md).

Why: DuckDB allows one writer per file, and while a run writes nobody else can read it, so the dashboard and
analysis had to wait for runs. In PostgreSQL collectors write and many people read at the same time.

One database (`crawl4ai`), one schema per tool, named like the prefixes `warehouse sql` already uses:
news, tech, markets, vehicles, companies, scrapers; plus `app` for the web app's own tables (users, roles, menus,
audit log: docs/user-platform-plan.md). Three logins:

    crawl4ai           collectors and setup: create tables, read, write (owns the database and the data schemas)
    crawl4ai_reader    dashboard, analysis, other users: read only, and a query stops after 30 s
    crawl4ai_agent     the AI assistant's queries: read only, the collected data only, and a query stops after 30 s
    crawl4ai_app       the web app (webapp/, Django): owns schema `app`, reads the data schemas

Where to connect (environment, or KEY=value lines in .local/database.env, which git ignores):

    CRAWL4AI_DATABASE_URL   writer   default postgresql://crawl4ai@localhost:5432/crawl4ai
    CRAWL4AI_READER_URL     reader   default postgresql://crawl4ai_reader@localhost:5432/crawl4ai
    CRAWL4AI_APP_URL        web app  default postgresql://crawl4ai_app@localhost:5432/crawl4ai
    CRAWL4AI_ADMIN_URL      setup    default postgresql://localhost:5432/postgres  (your own login; it creates
                                     the logins and the database, so it must be allowed to)

The database name and the two login names come from those URLs, so a server can use other names. A password
in a URL is set on that login by `setup`. On the Mac, Homebrew's PostgreSQL lets local logins in without one.

    uv run python -m warehouse setup      create or repair the logins, database, extensions, schemas and grants
    uv run python -m warehouse pg-check   check it all (also part of: uv run python doctor.py)
"""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import project_paths

project_paths.apply_local_env()

SCHEMAS = ("news", "tech", "markets", "vehicles", "companies", "scrapers", "foresight", "articles")
APP_SCHEMA = "app"                         # the web app's own tables; never readable by the reader login
USERDATA_SCHEMA = "userdata"               # people's own tables, fields and links; owned by the web app's login
AUDIT_SCHEMA = "audit"                     # people's corrections and their history; never readable by the reader login
EXTENSIONS = ("pg_trgm", "unaccent")      # pg_trgm: fast "name contains" search; unaccent: search ignoring accents
READER_STATEMENT_TIMEOUT = "30s"
LOW_DISK_GB = 2.0

DEFAULT_URLS = {
    "writer": "postgresql://crawl4ai@localhost:5432/crawl4ai",
    "reader": "postgresql://crawl4ai_reader@localhost:5432/crawl4ai",
    "admin": "postgresql://localhost:5432/postgres",
    "app": "postgresql://crawl4ai_app@localhost:5432/crawl4ai",
    "agent": "postgresql://crawl4ai_agent@localhost:5432/crawl4ai",
}
ENV_NAMES = {"writer": "CRAWL4AI_DATABASE_URL", "reader": "CRAWL4AI_READER_URL", "admin": "CRAWL4AI_ADMIN_URL",
             "app": "CRAWL4AI_APP_URL", "agent": "CRAWL4AI_AGENT_URL"}


class DatabaseError(RuntimeError):
    """A problem with a message that says what to do next."""


def url(role: str = "writer") -> str:
    return os.environ.get(ENV_NAMES[role]) or DEFAULT_URLS[role]


def _parts(conninfo: str) -> dict:
    from psycopg.conninfo import conninfo_to_dict
    return conninfo_to_dict(conninfo)


def _where(conninfo: str) -> str:
    """host:port/dbname as user, without the password, for messages."""
    p = _parts(conninfo)
    return f"{p.get('host') or 'local socket'}:{p.get('port') or 5432}/{p.get('dbname', '?')} as {p.get('user') or 'you'}"


def names() -> dict:
    """The database and login names, taken from the writer, reader and app URLs."""
    w, r, a, g = _parts(url("writer")), _parts(url("reader")), _parts(url("app")), _parts(url("agent"))
    return {"database": w.get("dbname") or "crawl4ai", "writer": w.get("user") or "crawl4ai",
            "reader": r.get("user") or "crawl4ai_reader", "app": a.get("user") or "crawl4ai_app",
            "agent": g.get("user") or "crawl4ai_agent", "agent_password": g.get("password"),
            "writer_password": w.get("password"), "reader_password": r.get("password"),
            "app_password": a.get("password")}


def connect(role: str = "writer", conninfo: str | None = None, **kwargs):
    """A psycopg connection. Friendly errors for the usual problems (server not running, no database yet)."""
    import psycopg
    conninfo = conninfo or url(role)
    try:
        return psycopg.connect(conninfo, connect_timeout=5, **kwargs)
    except psycopg.OperationalError as e:
        text = str(e)
        where = _where(conninfo)
        if "does not exist" in text and "database" in text:
            raise DatabaseError(f"The database doesn't exist yet ({where}). Create it:  "
                                f"uv run python -m warehouse setup") from e
        if "role" in text and "does not exist" in text:
            raise DatabaseError(f"The login doesn't exist yet ({where}). Create it:  "
                                f"uv run python -m warehouse setup") from e
        if "Connection refused" in text or "could not connect" in text or "timeout" in text.lower():
            raise DatabaseError(f"Can't reach PostgreSQL ({where}). Is it running? On the Mac:  "
                                f"brew services start postgresql@18") from e
        raise DatabaseError(f"Can't connect to PostgreSQL ({where}): {text.strip()}") from e


# --- setup -------------------------------------------------------------------------------------------------

def setup(admin_url: str | None = None) -> list[str]:
    """Create whatever is missing: both logins, the database, extensions, schemas, grants. Safe to run again.

    Returns what it did, one line each (empty when everything was already there; grants are always re-applied)."""
    from psycopg import sql
    n = names()
    admin = admin_url or url("admin")
    done: list[str] = []
    with connect(conninfo=admin, autocommit=True) as con:
        for key in ("writer", "reader", "app", "agent"):
            role = n[key]
            if not con.execute("select 1 from pg_roles where rolname = %s", [role]).fetchone():
                con.execute(sql.SQL("create role {} login").format(sql.Identifier(role)))
                done.append(f"created login {role}")
            if n[f"{key}_password"]:
                con.execute(sql.SQL("alter role {} password {}").format(
                    sql.Identifier(role), sql.Literal(n[f"{key}_password"])))
        if not con.execute("select 1 from pg_database where datname = %s", [n["database"]]).fetchone():
            con.execute(sql.SQL("create database {} owner {} encoding 'UTF8' template template0").format(
                sql.Identifier(n["database"]), sql.Identifier(n["writer"])))
            done.append(f"created database {n['database']} (owner {n['writer']})")

    db_admin = _with_dbname(admin, n["database"])
    with connect(conninfo=db_admin, autocommit=True) as con:
        W, R, D = sql.Identifier(n["writer"]), sql.Identifier(n["reader"]), sql.Identifier(n["database"])
        A, G = sql.Identifier(n["app"]), sql.Identifier(n["agent"])
        for ext in EXTENSIONS:
            if not con.execute("select 1 from pg_extension where extname = %s", [ext]).fetchone():
                con.execute(sql.SQL("create extension {}").format(sql.Identifier(ext)))
                done.append(f"added extension {ext}")
        for schema in SCHEMAS:
            S = sql.Identifier(schema)
            if not con.execute("select 1 from pg_namespace where nspname = %s", [schema]).fetchone():
                con.execute(sql.SQL("create schema {} authorization {}").format(S, W))
                done.append(f"created schema {schema}")
            con.execute(sql.SQL("grant usage on schema {} to {}").format(S, R))
            con.execute(sql.SQL("grant select on all tables in schema {} to {}").format(S, R))
            # tables the writer makes later are readable too, without running setup again
            con.execute(sql.SQL("alter default privileges for role {} in schema {} grant select on tables to {}")
                        .format(W, S, R))
            # the web app reads every dataset too (editing rights come per column, later: user-platform plan)
            con.execute(sql.SQL("grant usage on schema {} to {}").format(S, A))
            con.execute(sql.SQL("grant select on all tables in schema {} to {}").format(S, A))
            con.execute(sql.SQL("alter default privileges for role {} in schema {} grant select on tables to {}")
                        .format(W, S, A))
            # what the AI assistant's queries run as: the collected data and nothing else (never app, audit or userdata)
            con.execute(sql.SQL("grant usage on schema {} to {}").format(S, G))
            con.execute(sql.SQL("grant select on all tables in schema {} to {}").format(S, G))
            con.execute(sql.SQL("alter default privileges for role {} in schema {} grant select on tables to {}")
                        .format(W, S, G))
        # the web app's own schema: users, roles, menus, audit log (Django creates the tables in it)
        if not con.execute("select 1 from pg_namespace where nspname = %s", [APP_SCHEMA]).fetchone():
            con.execute(sql.SQL("create schema {} authorization {}").format(sql.Identifier(APP_SCHEMA), A))
            done.append(f"created schema {APP_SCHEMA} (owner {n['app']})")
        # people's own tables: the web app makes them while it runs, so it owns this schema; the reader can read them
        if not con.execute("select 1 from pg_namespace where nspname = %s", [USERDATA_SCHEMA]).fetchone():
            con.execute(sql.SQL("create schema {} authorization {}").format(sql.Identifier(USERDATA_SCHEMA), A))
            done.append(f"created schema {USERDATA_SCHEMA} (owner {n['app']})")
        con.execute(sql.SQL("grant usage on schema {} to {}").format(sql.Identifier(USERDATA_SCHEMA), R))
        con.execute(sql.SQL("grant select on all tables in schema {} to {}").format(sql.Identifier(USERDATA_SCHEMA), R))
        con.execute(sql.SQL("alter default privileges for role {} in schema {} grant select on tables to {}")
                    .format(A, sql.Identifier(USERDATA_SCHEMA), R))
        con.execute(sql.SQL("alter role {} in database {} set search_path = {}, public").format(
            A, D, sql.Identifier(APP_SCHEMA)))
        con.execute(sql.SQL("grant connect on database {} to {}").format(D, R))
        con.execute(sql.SQL("grant connect on database {} to {}").format(D, A))
        con.execute(sql.SQL("grant connect on database {} to {}").format(D, G))
        con.execute(sql.SQL("alter role {} in database {} set default_transaction_read_only = on").format(G, D))
        con.execute(sql.SQL("alter role {} in database {} set statement_timeout = {}").format(
            G, D, sql.Literal(READER_STATEMENT_TIMEOUT)))
        con.execute(sql.SQL("revoke create on schema public from public"))
        grant_edit_rights(con)
        # the reader can never change anything, and a heavy query can't keep the server busy for long
        con.execute(sql.SQL("alter role {} in database {} set default_transaction_read_only = on").format(R, D))
        con.execute(sql.SQL("alter role {} in database {} set statement_timeout = {}").format(
            R, D, sql.Literal(READER_STATEMENT_TIMEOUT)))
    return done


def grant_edit_rights(con) -> None:
    """What the web app may change in the collected data: only the columns people may correct (warehouse/edits.py), so a
    bug in the app can't damage anything else. Needs the tables' owner, which the app isn't. Safe to run again."""
    from psycopg import sql
    from . import edits
    app = names()["app"]
    if not con.execute("select 1 from pg_roles where rolname = %s", [app]).fetchone():
        return
    A = sql.Identifier(app)
    if con.execute("select to_regclass('audit.field_source')").fetchone()[0]:
        con.execute(sql.SQL("grant usage on schema audit to {}").format(A))
        con.execute(sql.SQL("grant select, insert on audit.field_history to {}").format(A))
        con.execute(sql.SQL("grant usage on sequence audit.field_history_id_seq to {}").format(A))
        con.execute(sql.SQL("grant select, insert, update, delete on audit.field_source, audit.hidden_records to {}").format(A))
    for table, target in edits.TARGETS.items():
        if con.execute("select to_regclass(%s)", [table]).fetchone()[0]:
            schema, name = table.split(".")
            con.execute(sql.SQL("grant update ({}) on {}.{} to {}").format(
                sql.SQL(", ").join(map(sql.Identifier, edits.protected_columns(target))),
                sql.Identifier(schema), sql.Identifier(name), A))


def _with_dbname(conninfo: str, dbname: str) -> str:
    from psycopg.conninfo import make_conninfo
    return make_conninfo(conninfo, dbname=dbname)


# --- check -------------------------------------------------------------------------------------------------

@dataclass
class Check:
    ok: bool
    what: str
    detail: str = ""
    warn: bool = False         # shown, but not a failure


def check() -> list[Check]:
    """Everything the tools need from PostgreSQL, as a list of results (stops early when it can't connect)."""
    import psycopg
    n = names()
    out: list[Check] = []
    try:
        con = connect("writer", autocommit=True)
    except DatabaseError as e:
        return [Check(False, "PostgreSQL (writer login)", str(e))]
    with con:
        version = con.execute("show server_version").fetchone()[0]
        out.append(Check(True, "PostgreSQL (writer login)", f"version {version}, {_where(url('writer'))}"))
        have = {r[0] for r in con.execute("select nspname from pg_namespace").fetchall()}
        missing = [s for s in SCHEMAS if s not in have]
        out.append(Check(not missing, "Schemas", ", ".join(SCHEMAS) if not missing
                         else f"missing {', '.join(missing)} (run: uv run python -m warehouse setup)"))
        exts = {r[0] for r in con.execute("select extname from pg_extension").fetchall()}
        missing = [e for e in EXTENSIONS if e not in exts]
        out.append(Check(not missing, "Extensions", ", ".join(EXTENSIONS) if not missing
                         else f"missing {', '.join(missing)} (run: uv run python -m warehouse setup)"))
        size = con.execute("select pg_size_pretty(pg_database_size(current_database()))").fetchone()[0]
        out.append(Check(True, "Database size", size))

    try:
        rcon = connect("reader", autocommit=False)
    except DatabaseError as e:
        out.append(Check(False, "PostgreSQL (reader login)", str(e)))
        return out + _disk_check()
    with rcon:
        timeout = rcon.execute("show statement_timeout").fetchone()[0]
        try:
            rcon.execute(f"create table {SCHEMAS[0]}._reader_write_probe (x int)")
            rcon.rollback()
            out.append(Check(False, "Reader login is read-only", f"{n['reader']} could create a table "
                             "(run: uv run python -m warehouse setup)"))
        except psycopg.errors.ReadOnlySqlTransaction:
            rcon.rollback()
            out.append(Check(True, "Reader login is read-only", f"{n['reader']}, queries stop after {timeout}"))
        except psycopg.Error as e:          # e.g. no permission: also can't write, but say what happened
            rcon.rollback()
            out.append(Check(True, "Reader login is read-only", f"{n['reader']} ({type(e).__name__})"))
        # the web app's tables (users, password hashes) are never readable by the reader login
        app_readable = rcon.execute("""select coalesce((select has_schema_privilege(current_user, oid, 'usage')
                                       from pg_namespace where nspname = %s), false)""", [APP_SCHEMA]).fetchone()[0]
        out.append(Check(not app_readable, "Reader can't see the web app's tables",
                         "ok" if not app_readable else f"{n['reader']} can use schema {APP_SCHEMA}: remove that grant"))
    return out + _app_check(n) + _agent_check(n) + _disk_check()


def _agent_check(n: dict) -> list[Check]:
    """The AI assistant's queries run as this login: people's accounts, corrections and own tables must stay out of its reach."""
    try:
        con = connect("agent", autocommit=True)
    except DatabaseError as e:
        return [Check(False, "PostgreSQL (AI assistant login)", str(e))]
    with con:
        reachable = [s for s in (APP_SCHEMA, AUDIT_SCHEMA, USERDATA_SCHEMA) if con.execute(
            """select coalesce((select has_schema_privilege(current_user, oid, 'usage') from pg_namespace where nspname = %s), false)""", [s]).fetchone()[0]]
    return [Check(not reachable, "AI assistant's login reads collected data only",
                  n["agent"] if not reachable else f"{n['agent']} can use {', '.join(reachable)}: remove that grant")]


def _app_check(n: dict) -> list[Check]:
    """The web app's login: connects, owns schema `app`, can read the data."""
    try:
        acon = connect("app", autocommit=True)
    except DatabaseError as e:
        return [Check(False, "PostgreSQL (web app login)", str(e))]
    with acon:
        owner = acon.execute("select pg_get_userbyid(nspowner) from pg_namespace where nspname = %s",
                             [APP_SCHEMA]).fetchone()
        if not owner or owner[0] != n["app"]:
            return [Check(False, "PostgreSQL (web app login)", f"schema {APP_SCHEMA} missing or not owned by "
                          f"{n['app']} (run: uv run python -m warehouse setup)")]
        tables = acon.execute("select count(*) from pg_tables where schemaname = %s", [APP_SCHEMA]).fetchone()[0]
        return [Check(True, "PostgreSQL (web app login)", f"{n['app']} owns schema {APP_SCHEMA} ({tables} tables"
                      + ("" if tables else "; create them: uv run python -m webapp migrate") + ")")]


def _disk_check() -> list[Check]:
    """Free space where PostgreSQL keeps its files, when it's on this machine and we may ask where that is."""
    try:
        with connect(conninfo=_with_dbname(url("admin"), names()["database"]), autocommit=True) as con:
            row = con.execute("select setting from pg_settings where name = 'data_directory'").fetchone()
    except Exception:  # noqa: BLE001  (no admin login here, e.g. on a server: nothing to check)
        return []
    if not row or not Path(row[0]).exists():
        return []
    free_gb = shutil.disk_usage(row[0]).free / 1e9
    low = free_gb < LOW_DISK_GB
    return [Check(True, "Free disk space for PostgreSQL",
                  f"{free_gb:.1f} GB at {row[0]}" + (" (low: big imports such as the GLEIF file won't fit)"
                                                      if low else ""), warn=low)]


# --- tables ------------------------------------------------------------------------------------------------

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"


def schema_file(schema: str) -> Path:
    return SCHEMA_DIR / f"{schema}.sql"


MIGRATE_LOCK = 4_242_001        # advisory lock id: two programs starting at once don't create tables at the same time


def migrate(con=None, schemas: list[str] | tuple[str, ...] = SCHEMAS) -> list[str]:
    """Create the tables, sequences, indexes and views of each schema (warehouse/schema/<schema>.sql).

    A file is run only when it changed since it last ran (its SHA-256 is kept in public.schema_versions), so
    opening a store costs one quick query, and never takes locks that could make dashboard readers wait.
    Every statement in the files is `create ... if not exists` / `create or replace`, so running one again is
    safe too. Returns the schemas whose file was run."""
    import hashlib
    own = con is None
    con = con or connect("writer", autocommit=True)
    ran: list[str] = []
    schemas = (AUDIT_SCHEMA, *[s for s in schemas if s != AUDIT_SCHEMA])   # the data schemas' views look at audit's tables
    try:
        con.execute("""create table if not exists public.schema_versions (
                           schema text primary key, sha256 text not null, applied_at timestamptz not null)""")
        for schema in schemas:
            text = schema_file(schema).read_text(encoding="utf-8")
            digest = hashlib.sha256(text.encode()).hexdigest()
            row = con.execute("select sha256 from public.schema_versions where schema = %s", [schema]).fetchone()
            if row and row[0] == digest:
                continue
            with con.transaction():
                con.execute("select pg_advisory_xact_lock(%s)", [MIGRATE_LOCK])
                row = con.execute("select sha256 from public.schema_versions where schema = %s", [schema]).fetchone()
                if row and row[0] == digest:        # another program ran it while we waited
                    continue
                con.execute(text)
                con.execute("""insert into public.schema_versions values (%s, %s, now())
                               on conflict (schema) do update set sha256 = excluded.sha256, applied_at = now()""",
                            [schema, digest])
                ran.append(schema)
        if ran:
            grant_edit_rights(con)
    finally:
        if own:
            con.close()
    return ran


# --- values ------------------------------------------------------------------------------------------------

def clean_text(value):
    """PostgreSQL can't store the NUL character in text: drop it (scraped pages sometimes contain one)."""
    return value.replace("\x00", "") if isinstance(value, str) else value


def json_text(value) -> str | None:
    """A value as JSON text for a jsonb column (None stays NULL). NUL can't be stored in jsonb either."""
    import json
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str).replace("\\u0000", "")
