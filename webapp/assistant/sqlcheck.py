"""Checks SQL the AI wrote, before anything runs it. The AI only ever gets a description of the data, but what it writes is treated
as untrusted: one read-only statement, only datasets this person may see, only ordinary functions. What runs is the parsed query written
out again, not the text that came in, so a trick in the text (a comment, a second statement) doesn't survive. The login it runs
as can't do more than read the collected data anyway; this is the check in front of that wall, not instead of it."""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

MAX_SQL = 8000

# Functions sqlglot has no class for must be on this list. What's refused is everything else: anything that reads files, settings or other
# sessions, waits, or runs text as a query.
ALLOWED_FUNCTIONS = {
    "abs", "age", "array_agg", "array_length", "array_position", "array_to_string", "avg", "bool_and", "bool_or", "btrim", "ceil", "ceiling",
    "char_length", "character_length", "coalesce", "concat", "concat_ws", "count", "cume_dist", "date_part", "date_trunc", "dense_rank", "div",
    "exp", "extract", "first_value", "floor", "format", "greatest", "initcap", "jsonb_agg", "jsonb_array_elements", "jsonb_array_elements_text",
    "jsonb_array_length", "jsonb_build_object", "jsonb_each_text", "jsonb_extract_path_text", "jsonb_object_keys", "jsonb_typeof", "lag", "last_value",
    "lead", "least", "left", "length", "ln", "log", "lower", "lpad", "ltrim", "make_date", "make_interval", "max", "md5", "min", "mod", "now",
    "nth_value", "ntile", "nullif", "octet_length", "percent_rank", "percentile_cont", "percentile_disc", "position", "power", "rank", "regexp_count",
    "regexp_like", "regexp_match", "regexp_matches", "regexp_replace", "regexp_substr", "repeat", "replace", "reverse", "right", "round", "row_number",
    "rpad", "rtrim", "sign", "split_part", "sqrt", "starts_with", "stddev", "stddev_pop", "stddev_samp", "string_agg", "strpos", "substr", "substring",
    "sum", "timezone", "to_char", "to_date", "to_number", "to_timestamp", "translate", "trim", "trunc", "unaccent", "unnest", "upper", "var_pop",
    "var_samp", "variance", "current_date", "current_timestamp", "localtimestamp", "generate_series",
}
REFUSED_NAME_STARTS = ("pg_", "lo_", "dblink", "query_to_", "table_to_", "schema_to_", "database_to_", "cursor_to_", "set_config", "current_setting",
                       "has_", "txid_", "version", "inet_", "current_user", "current_version", "current_role", "current_available", "current_org", "current_secondary", "current_session", "session_user",
                       "current_schema", "current_database", "current_catalog", "read_", "to_file", "xpath", "xml", "x_m_l", "host")
NAMES_THAT_ARE_FUNCTIONS = {"current_user", "session_user", "current_role", "current_schema", "current_catalog", "current_database", "version", "user"}
NOT_A_READ = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Into, exp.Lock, exp.Set, exp.Transaction,
              exp.Commit, exp.Rollback, exp.TruncateTable, exp.Copy, exp.Grant, exp.Use, exp.LoadData, exp.Attach, exp.Detach, exp.Analyze, exp.Refresh)


class SqlRefused(ValueError):
    """Why not: goes back to the AI, which can try again, and to the person if it can't."""


@dataclass
class Checked:
    sql: str
    sources: list[str]           # "schema.name", in the order first read


def _parse(text: str):
    text = (text or "").strip().rstrip(";").strip()
    if not text:
        raise SqlRefused("There's no query.")
    if len(text) > MAX_SQL:
        raise SqlRefused(f"The query is over {MAX_SQL:,} characters. Make it simpler.")
    try:
        statements = sqlglot.parse(text, dialect="postgres")
    except (ParseError, TokenError) as e:
        raise SqlRefused(f"It can't be read as a query: {str(e)[:200]}") from None
    if len([s for s in statements if s is not None]) != 1:
        raise SqlRefused("Write exactly one statement.")
    return statements[0]


def _check_kind(tree) -> None:
    if not isinstance(tree, (exp.Select, exp.SetOperation, exp.Subquery)):
        raise SqlRefused("Only SELECT queries are allowed.")
    for node in tree.walk():
        if isinstance(node, NOT_A_READ):
            raise SqlRefused(f"Only reading is allowed ({type(node).__name__.upper()} isn't).")
        if isinstance(node, exp.Select) and node.args.get("locks"):
            raise SqlRefused("Locking rows isn't allowed.")


def _check_relations(tree, allowed: dict) -> list[str]:
    local = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    sources = []
    for table in tree.find_all(exp.Table):
        if isinstance(table.this, exp.Func):        # a function in FROM: its name is checked with the others
            continue
        name, schema = table.name, table.db
        if table.catalog:
            raise SqlRefused(f"Use schema.table, not a database name ({table.sql(dialect='postgres')}).")
        if not schema:
            if name.lower() in local:
                continue
            raise SqlRefused(f"Name the schema of every table ({name} on its own isn't allowed).")
        ref = f"{schema}.{name}"
        if ref not in allowed:
            raise SqlRefused(f"{ref} isn't one of the datasets available here.")
        if ref not in sources:
            sources.append(ref)
    if not sources:
        raise SqlRefused("The query reads no dataset.")
    return sources


def _check_functions(tree) -> None:
    for dot in tree.find_all(exp.Dot):             # schema.function(...)
        if isinstance(dot.expression, exp.Func):
            raise SqlRefused("Call functions by their plain name, without a schema.")
    for func in tree.find_all(exp.Func):
        # sqlglot knows some by name (current_user, version) and parses the rest as Anonymous: both have to pass
        names = [str(func.this)] if isinstance(func, exp.Anonymous) else list(func.sql_names())
        if isinstance(func, exp.Anonymous) and names[0].lower() not in ALLOWED_FUNCTIONS:
            raise SqlRefused(f"The function {names[0].lower()} isn't allowed.")
        for name in names:
            if name.lower().startswith(REFUSED_NAME_STARTS):
                raise SqlRefused(f"The function {name.lower()} isn't allowed.")
    for column in tree.find_all(exp.Column):       # the ones written without brackets can also parse as plain names
        if not column.table and column.name.lower() in NAMES_THAT_ARE_FUNCTIONS:
            raise SqlRefused(f"{column.name} isn't allowed.")


def _check_output(tree) -> None:
    """The result becomes a view and a grid's columns, so each needs a name of its own. Told to the AI now, it fixes it itself."""
    names = [e.output_name.lower() for e in tree.selects]
    if any(not n for n in names):
        raise SqlRefused("Give every returned column a name with AS.")
    repeated = sorted({n for n in names if names.count(n) > 1})
    if repeated:
        raise SqlRefused(f"Two returned columns have the same name ({', '.join(repeated)}). Give each its own.")


def _check_columns(tree, sources: list[str], allowed: dict) -> None:
    """A field an administrator switched off isn't there for the assistant either, and asking for everything would bring it along."""
    aliases = {t.alias_or_name.lower() for t in tree.find_all(exp.Table)} | {s.alias_or_name.lower() for s in tree.find_all(exp.Subquery) if s.alias}
    for star in tree.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            raise SqlRefused("Name the columns instead of using *.")
    for column in tree.find_all(exp.Column):
        if isinstance(column.this, exp.Star):
            raise SqlRefused("Name the columns instead of using *.")
        if not column.table and column.name.lower() in aliases and not _is_known_column(column.name, sources, allowed):
            raise SqlRefused(f"Name the columns of {column.name} instead of using the whole row.")
    usable = set()
    blocked = set()
    for ref in sources:
        usable |= allowed[ref]["columns"]
        blocked |= allowed[ref]["blocked_columns"]
    blocked -= usable
    keys = {key for ref in sources for _, key in allowed[ref]["blocked_keys"]}
    holders = {column for ref in sources for column, _ in allowed[ref]["blocked_keys"]}
    extracts = (exp.JSONExtract, exp.JSONExtractScalar, exp.JSONBExtract, exp.JSONBExtractScalar)
    for column in tree.find_all(exp.Column):
        if column.name in blocked:
            raise SqlRefused(f"The field {column.name} isn't available.")
        # the whole JSON would carry the field that's switched off, so only its other keys can be read
        if column.name in holders and not (isinstance(column.parent, extracts) and column.parent.this is column):
            raise SqlRefused(f"Read {column.name} one key at a time: {column.name}->>'key'.")
    for node in tree.find_all(*extracts):
        key = node.expression.sql(dialect="postgres").strip("'\"$. ")
        if key in keys:
            raise SqlRefused(f"The field {key} isn't available.")
    for literal in tree.find_all(exp.Literal):
        if literal.is_string and literal.this in keys:
            raise SqlRefused(f"The field {literal.this} isn't available.")


def _is_known_column(name: str, sources: list[str], allowed: dict) -> bool:
    return any(name in allowed[ref]["columns"] for ref in sources)


def check(text: str, allowed: dict) -> Checked:
    """allowed: {"schema.name": {"columns": set, "blocked_columns": set, "blocked_keys": {(column, key)}}}, what this person may use. Raises SqlRefused."""
    tree = _parse(text)
    _check_kind(tree)
    sources = _check_relations(tree, allowed)
    _check_functions(tree)
    _check_columns(tree, sources, allowed)
    _check_output(tree)
    return Checked(sql=tree.sql(dialect="postgres", comments=False), sources=sources)
