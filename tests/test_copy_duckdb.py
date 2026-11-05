"""Step 2 of docs/postgres-migration.md: copy the DuckDB databases into PostgreSQL, with checks."""

from datetime import datetime, timezone

import duckdb
import pytest

from warehouse import DB_FILES, postgres
from warehouse.cli import init
from warehouse.copy_duckdb import copy_all, copy_tool, report


# companies moved to PostgreSQL (step 3), so its store no longer makes a DuckDB file: this is the last DuckDB
# layout (companies/store.py before 2026-11-06), as the real data/companies/companies.duckdb still has it.
OLD_COMPANIES_DUCKDB = """
create table companies (id varchar primary key, source varchar, source_id varchar, name varchar, country varchar,
    website varchar, domain varchar, industry varchar, extra json, first_seen timestamptz, last_seen timestamptz);
create table sites (domain varchar primary key, url varchar, final_url varchar, checked_at timestamptz, status varchar,
    error varchar, name varchar, legal_name varchar, founded varchar, employees varchar, employees_hint varchar,
    address varchar, people json, emails json, phones json, social json, linkedin_people json, pages json);
create table refusals (domain varchar primary key, refused_at timestamptz, error varchar);
create table cursors (source varchar, key varchar, value json, updated_at timestamptz, primary key (source, key));
create table runs (started_at timestamptz, command varchar, seconds double, summary json);
"""


@pytest.fixture
def duck_files(tmp_path, monkeypatch, pg_env):
    """Every tool's DuckDB file, made by the tools' own stores (so the test notices when a tool adds a table or a
    column that warehouse/schema/*.sql doesn't have), in a temp folder. The PostgreSQL tables start empty."""
    for name, path in list(DB_FILES.items()):
        monkeypatch.setitem(DB_FILES, name, tmp_path / path.parent.name / path.name)
    postgres.setup()
    init()
    DB_FILES["companies"].parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(DB_FILES["companies"])) as con:
        con.execute(OLD_COMPANIES_DUCKDB)
    return DB_FILES


def _fill(duck_files):
    with duckdb.connect(str(duck_files["companies"])) as con:
        con.execute("""insert into companies values
            ('wikidata:Q1', 'wikidata', 'Q1', 'Acme' || chr(0) || ' Ltd', 'LK', 'https://acme.lk', 'acme.lk', 'banks',
             '{"note": "a\\u0000b", "lei": "X"}', '2026-11-01 10:00:00+00', '2026-11-05 10:00:00+00'),
            ('gleif:L2', 'gleif', 'L2', 'Beta PLC', 'GB', null, null, null, null,
             '2026-11-02 10:00:00+00', '2026-11-04 10:00:00+00')""")
        con.execute("""insert into sites (domain, url, checked_at, status, social, people) values
            ('acme.lk', 'https://acme.lk', '2026-11-05 11:00:00+00', 'ok', '{"linkedin": "https://x"}', '[]')""")
    with duckdb.connect(str(duck_files["news"])) as con:
        con.execute("""insert into news_items (url, title, published_at, source, group_code, topics, first_seen_at,
                       last_seen_at, times_seen)
                       values ('https://n/1', 'Rupee closes', '2026-11-05 08:00:00+00', 'EconomyNext', 'LK',
                               ['markets', 'lk'], '2026-11-05 08:05:00+00', '2026-11-05 09:00:00+00', 2)""")
    with duckdb.connect(str(duck_files["tech"])) as con:     # plain TIMESTAMP, written in the session's time zone
        con.execute("set TimeZone = 'Asia/Colombo'")
        con.execute("""insert into candidates (key, created_at, data)
                       values ('getvector.sh', timestamp '2026-11-05 23:15:54', '{"domain": "getvector.sh"}')""")


def test_copies_every_table_with_matching_counts_and_newest_times(duck_files):
    _fill(duck_files)
    results = copy_all()
    assert all(r.ok for r in results), report(results)
    assert {r.schema for r in results} == set(postgres.SCHEMAS)
    companies = next(r for r in results if r.schema == "companies")
    assert next(t for t in companies.tables if t.table == "companies").cleaned == 2      # NUL in name and JSON

    with postgres.connect("reader") as con:
        assert con.execute("select name, extra->>'note', extra->>'lei' from companies.companies "
                           "where id = 'wikidata:Q1'").fetchone() == ("Acme Ltd", "ab", "X")
        assert con.execute("select social->>'linkedin' from companies.sites").fetchone() == ("https://x",)
        assert con.execute("select topics from news.news_items").fetchone() == (["markets", "lk"],)
        # 23:15:54 in Colombo (+05:30) is 17:45:54 UTC
        assert con.execute("select created_at from tech.candidates").fetchone()[0] == \
            datetime(2026, 11, 5, 17, 45, 54, tzinfo=timezone.utc)
        hits = con.execute("select name from companies.companies where lower(name) like '%acme%'").fetchall()
        assert hits == [("Acme Ltd",)]


def test_copy_again_needs_replace_and_replace_is_safe_to_repeat(duck_files):
    _fill(duck_files)
    assert copy_tool("companies").ok
    again = copy_tool("companies")
    assert not again.ok and not again.copied
    assert "--replace" in report([again])
    for _ in range(2):
        assert copy_tool("companies", replace=True).ok
    with postgres.connect("reader") as con:
        assert con.execute("select count(*) from companies.companies").fetchone()[0] == 2


def test_check_only_finds_differences_and_copies_nothing(duck_files):
    _fill(duck_files)
    before = copy_tool("news", check_only=True)
    assert not before.ok and not before.copied               # nothing in PostgreSQL yet
    assert copy_tool("news").ok
    assert copy_tool("news", check_only=True).ok
    with duckdb.connect(str(duck_files["news"])) as con:     # a new headline arrives in DuckDB afterwards
        con.execute("insert into news_items (url, title, last_seen_at) values ('https://n/2', 'New', now())")
    after = copy_tool("news", check_only=True)
    assert not after.ok
    assert "BAD" in report([after], check_only=True)


def test_sequences_continue_after_the_copied_ids(duck_files):
    _fill(duck_files)
    assert copy_tool("tech").ok
    with postgres.connect("writer") as con:
        top = con.execute("select max(row_id) from tech.candidates").fetchone()[0]
        new = con.execute("insert into tech.runs (key, data) values ('x', '{}') returning row_id").fetchone()[0]
    assert new > top


def test_a_busy_duckdb_file_is_reported_not_waited_for(duck_files, monkeypatch):
    import warehouse.copy_duckdb as cd

    def busy(*a, **k):
        raise RuntimeError("companies.duckdb is in use by another program")
    monkeypatch.setattr(cd, "duck_connect", busy)
    result = copy_tool("companies")
    assert not result.ok and "a run is writing" in result.error


def test_a_table_postgres_doesnt_know_fails_the_whole_tool(duck_files):
    _fill(duck_files)
    with duckdb.connect(str(duck_files["vehicles"])) as con:
        con.execute("create table surprise (x int)")
    result = copy_tool("vehicles")
    assert not result.ok and not result.copied
    assert "warehouse/schema/vehicles.sql" in report([result])
    with postgres.connect("reader") as con:                 # nothing of vehicles was kept
        assert con.execute("select count(*) from vehicles.runs").fetchone()[0] == 0
