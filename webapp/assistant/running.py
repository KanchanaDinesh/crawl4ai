"""Runs a checked query as the AI's own login: read only, the collected data only, 30 seconds at most."""

from contextlib import contextmanager
from dataclasses import dataclass, field

import psycopg

from warehouse.postgres import DatabaseError, connect
from webapp.explore.query import plain

PREVIEW_ROWS = 100
TIMEOUT = "30s"


class QueryFailed(ValueError):
    """The database's own message: goes back to the AI, which can fix the query."""


@dataclass
class Result:
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    total: int = 0


@contextmanager
def _session():
    """A transaction on the AI's own login. Tests swap this for one on their temporary data."""
    try:
        con = connect("agent", autocommit=True)
    except DatabaseError as e:
        raise RuntimeError(str(e)) from None
    try:
        with con.transaction():
            yield con
    finally:
        con.close()


def run(sql: str) -> Result:
    """The first rows and how many there are. Only the count ever goes back to the AI."""
    try:
        with _session() as con:
            con.execute(f"set local statement_timeout = '{TIMEOUT}'")
            con.execute("set local transaction_read_only = on")
            con.execute("set local search_path = pg_catalog, public")    # pg_catalog first: nothing can stand in for a built-in
            total = con.execute(f"select count(*) from ({sql}) as answer").fetchone()[0]
            cur = con.execute(f"select * from ({sql}) as answer limit {PREVIEW_ROWS}")
            columns = [d[0] for d in cur.description]
            return Result(columns, [[plain(v) for v in row] for row in cur.fetchall()], total)
    except psycopg.errors.QueryCanceled:
        raise QueryFailed("It took over 30 seconds. Make it cheaper: filter first, or compare fewer rows.") from None
    except psycopg.Error as e:
        raise QueryFailed(str(e).strip().splitlines()[0][:300]) from None
