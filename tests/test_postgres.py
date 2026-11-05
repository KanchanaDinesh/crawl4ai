"""PostgreSQL setup (step 1 of docs/postgres-migration.md), on a throwaway server (conftest.pg_server)."""

import psycopg
import pytest

import project_paths
from warehouse import postgres


def test_setup_creates_everything_and_is_safe_to_repeat(pg_env):
    done = postgres.setup()
    n = postgres.names()
    assert f"created login {n['writer']}" in done and f"created login {n['reader']}" in done
    assert any(d.startswith(f"created database {n['database']}") for d in done)
    assert all(f"created schema {s}" in done for s in postgres.SCHEMAS)
    assert "added extension pg_trgm" in done
    assert postgres.setup() == []                     # second run: nothing new, no errors

    with postgres.connect("writer") as con:
        owner = con.execute("select pg_get_userbyid(datdba) from pg_database where datname = current_database()"
                            ).fetchone()[0]
        assert owner == n["writer"]
        schemas = {r[0] for r in con.execute(
            "select nspname from pg_namespace where pg_get_userbyid(nspowner) = current_user").fetchall()}
        assert set(postgres.SCHEMAS) <= schemas


def test_reader_reads_new_tables_but_can_never_write(pg_env):
    postgres.setup()
    with postgres.connect("writer") as con:          # a table made after setup: still readable (default privileges)
        con.execute("create table companies.demo (id int primary key, name text)")
        con.execute("insert into companies.demo values (1, 'Acme')")
    with postgres.connect("reader") as con:
        assert con.execute("select name from companies.demo").fetchall() == [("Acme",)]
        assert con.execute("show statement_timeout").fetchone()[0] == "30s"
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            con.execute("insert into companies.demo values (2, 'Nope')")
        con.rollback()
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            con.execute("create table news.nope (x int)")


def test_reading_never_waits_for_a_writer(pg_env):
    """The reason for the move: in DuckDB nobody could read while a run was writing."""
    postgres.setup()
    with postgres.connect("writer", autocommit=True) as con:
        con.execute("create table news.items (id int primary key)")
        con.execute("insert into news.items values (1)")
    writer = postgres.connect("writer")                # a run in the middle of saving a batch
    writer.execute("insert into news.items values (2)")
    writer.execute("update news.items set id = 10 where id = 1")
    try:
        with postgres.connect("reader", autocommit=True) as reader:
            reader.execute("set statement_timeout = '2s'")      # would fail here if reading had to wait
            assert reader.execute("select id from news.items order by id").fetchall() == [(1,)]
            writer.commit()
            assert reader.execute("select id from news.items order by id").fetchall() == [(2,), (10,)]
    finally:
        writer.close()


def test_check_reports_ok_after_setup_and_says_what_to_do_before(pg_env):
    before = postgres.check()
    assert not before[0].ok and "warehouse setup" in before[0].detail      # no database yet
    postgres.setup()
    results = postgres.check()
    assert all(r.ok for r in results), [(r.what, r.detail) for r in results]
    assert {"Schemas", "Extensions", "Reader login is read-only"} <= {r.what for r in results}


def test_setup_sets_passwords_from_the_urls(pg_env, monkeypatch):
    monkeypatch.setenv("CRAWL4AI_DATABASE_URL", pg_env["writer"] + " password=s3cret")
    postgres.setup()
    with psycopg.connect(pg_env["admin"]) as con:
        row = con.execute("select rolpassword is not null from pg_authid where rolname = %s",
                          [postgres.names()["writer"]]).fetchone()
    assert row == (True,)


def test_friendly_errors(pg_env, monkeypatch):
    with pytest.raises(postgres.DatabaseError, match="doesn't exist yet"):
        postgres.connect("writer")
    monkeypatch.setenv("CRAWL4AI_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/crawl4ai")
    with pytest.raises(postgres.DatabaseError, match="Can't reach PostgreSQL .*brew services start"):
        postgres.connect("writer")


def test_database_env_file(tmp_path, monkeypatch):
    env = tmp_path / "database.env"
    env.write_text('# comment\nexport CRAWL4AI_DATABASE_URL="postgresql://u:pw@server/crawl4ai"\n'
                   "CRAWL4AI_READER_URL=already-set-wins\n\nnot a setting\n")
    monkeypatch.delenv("CRAWL4AI_DATABASE_URL", raising=False)
    monkeypatch.setenv("CRAWL4AI_READER_URL", "from-environment")
    project_paths.load_database_env(env)
    import os
    assert os.environ["CRAWL4AI_DATABASE_URL"] == "postgresql://u:pw@server/crawl4ai"
    assert os.environ["CRAWL4AI_READER_URL"] == "from-environment"
    monkeypatch.delenv("CRAWL4AI_DATABASE_URL")       # don't leak into other tests


def test_the_web_app_login_owns_schema_app_and_the_reader_cant_see_it(pg_env):
    postgres.setup()
    postgres.migrate()
    with postgres.connect("app", autocommit=True) as con:
        con.execute("create table app.secrets (pw text)")                  # what Django will do for users
        assert con.execute("select count(*) from companies.companies").fetchone() == (0,)   # reads the data
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            con.execute("insert into companies.cursors values ('x', 'y', '{}', now())")      # but can't write it
    with postgres.connect("reader", autocommit=True) as con:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            con.execute("select * from app.secrets")
    checks = {c.what: c for c in postgres.check()}
    assert checks["Reader can't see the web app's tables"].ok and checks["PostgreSQL (web app login)"].ok
