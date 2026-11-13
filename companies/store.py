"""Companies database: schema `companies` in PostgreSQL (database crawl4ai; tables in warehouse/schema/companies.sql).

    companies   one row per company per source: id ("wikidata:Q95", "gleif:<LEI>", "cse:JKH.N0000", ...),
                name, country (ISO code: US, GB, AU, IN, LK, ...), website, domain, industry, extra (source's own
                fields), first_seen, last_seen. The same company can come from several sources (different ids).
    sites       what `update` read on each company website (one row per domain, the latest check):
                status ok / unreachable / not_allowed (robots.txt), social links, people, employees, contacts ...
    refusals    websites that refused us (401/403/429): not results, only "don't ask again before ..."
    cursors     where each source stopped, per country (collect continues there on the next run)
    runs        one row per collect / update run, with its summary
    v_companies companies with their site check (status, social links, people, employees), and name_en: the name in
                English letters (the name itself, or the English name the source gives), null when none is known
    name_script, english_name   set by PostgreSQL (a trigger) from name and extra: which letters the name is written
                in (null = Latin; mixed, other, none), and the English or Latin name a source gives when it isn't Latin.
                The dashboard hides companies with neither unless asked; nothing is deleted.

Moved from DuckDB (data/companies/companies.duckdb) on 2026-11-06: in PostgreSQL the dashboard and anyone else
can read while collect/update write. Every save is its own short transaction, so nothing is locked for long.

Saving is an upsert: a company seen again keeps its first_seen and gets the newest name/website/industry, except where a
person corrected a field and the source only repeats what it said before (warehouse/edits.py, rule A).
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

COMPANY_FIELDS = ("id", "source", "source_id", "name", "country", "website", "domain", "industry", "extra")
SITE_JSON = ("people", "emails", "phones", "social", "linkedin_people", "pages")
SITE_FIELDS = ("domain", "url", "final_url", "checked_at", "status", "error", "name", "legal_name", "founded",
               "employees", "employees_hint", "address") + SITE_JSON


def data_dir() -> Path:
    """data/companies/: files that aren't in the database (Companies House zips waiting to be imported)."""
    from warehouse import DATA_DIRS
    return DATA_DIRS["companies"]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CompanyStore:
    def __init__(self, folder: Path | None = None):
        """folder: where downloads go (default data/companies/). The data itself is in PostgreSQL
        (CRAWL4AI_DATABASE_URL); the tables are created or updated here when warehouse/schema/companies.sql changed."""
        from warehouse import postgres
        self.folder = Path(folder) if folder else data_dir()
        self.con = postgres.connect("writer", autocommit=True)
        self.con.execute("set timezone = 'UTC'")
        postgres.migrate(self.con, ("companies",))

    @property
    def download_dir(self) -> Path:
        """Big source files (Companies House zips) wait here until they're imported."""
        folder = self.folder / "downloads"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    # ── companies ──────────────────────────────────────────────
    def upsert_companies(self, rows: list[dict], seen_at: str | None = None) -> tuple[int, int]:
        """Add new companies and refresh known ones. Returns (added, updated).

        A batch (a page of GLEIF, a Wikidata query: up to ~27,000 rows) is loaded with COPY into a temporary
        table and merged with one statement, in one transaction: fast, and readers see the whole batch or none."""
        from warehouse import edits
        from warehouse.postgres import clean_text, json_text
        seen_at = seen_at or now_iso()
        unique: dict[str, dict] = {}
        for r in rows:                                   # one row per id (a batch with the same id twice fails)
            if r.get("id") and r.get("name"):
                unique[r["id"]] = r
        if not unique:
            return 0, 0
        con = self.con
        with con.transaction():
            con.execute("drop table if exists staged")
            con.execute("""create temp table staged (id text, source text, source_id text, name text, country text,
                           website text, domain text, industry text, extra jsonb) on commit drop""")
            with con.cursor().copy(f"copy staged ({', '.join(COMPANY_FIELDS)}) from stdin") as cp:
                for r in unique.values():
                    cp.write_row([json_text(r.get(f)) if f == "extra" else clean_text(r.get(f))
                                  for f in COMPANY_FIELDS])
            edits.protect_staged(con.cursor(), "companies.companies", "staged", "collector:companies")
            added = con.execute("""select count(*) from staged s
                                   where not exists (select 1 from companies.companies c where c.id = s.id)""").fetchone()[0]
            con.execute("""
                insert into companies.companies as c (id, source, source_id, name, country, website, domain, industry,
                                                      extra, first_seen, last_seen)
                select id, source, source_id, name, country, website, domain, industry, extra,
                       %(seen)s::timestamptz, %(seen)s::timestamptz
                from staged
                on conflict (id) do update set
                    name = excluded.name, country = coalesce(excluded.country, c.country),
                    website = coalesce(excluded.website, c.website), domain = coalesce(excluded.domain, c.domain),
                    industry = coalesce(excluded.industry, c.industry), extra = excluded.extra,
                    last_seen = excluded.last_seen""", {"seen": seen_at})
        return added, len(unique) - added

    def count(self, where: str = "true", params: list | None = None) -> int:
        """Companies matching a WHERE clause (placeholders: %s)."""
        return self.con.execute(f"select count(*) from companies.companies where {where}", params or []).fetchone()[0]

    # ── cursors: where each source stopped ─────────────────────
    def cursors(self, source: str) -> dict[str, dict]:
        rows = self.con.execute("select key, value from companies.cursors where source = %s", [source]).fetchall()
        return {k: v for k, v in rows}

    def set_cursor(self, source: str, key: str, value: dict) -> None:
        from warehouse.postgres import json_text
        self.con.execute("""insert into companies.cursors values (%s, %s, %s, now())
                            on conflict (source, key) do update set value = excluded.value, updated_at = now()""",
                         [source, key, json_text(value)])

    # ── sites: what `update` read on each website ──────────────
    def save_site(self, row: dict) -> None:
        from warehouse.postgres import clean_text, json_text
        values = [json_text(row.get(f)) if f in SITE_JSON else clean_text(row.get(f)) for f in SITE_FIELDS]
        updates = ", ".join(f"{f} = excluded.{f}" for f in SITE_FIELDS if f != "domain")
        with self.con.transaction():
            self.con.execute(f"""insert into companies.sites ({', '.join(SITE_FIELDS)})
                                 values ({', '.join(['%s'] * len(SITE_FIELDS))})
                                 on conflict (domain) do update set {updates}""", values)
            self.con.execute("delete from companies.refusals where domain = %s", [row["domain"]])

    def save_refusal(self, domain: str, error: str) -> None:
        self.con.execute("""insert into companies.refusals values (%s, now(), %s)
                            on conflict (domain) do update set refused_at = now(), error = excluded.error""",
                         [domain, error])

    def due_sites(self, config: dict, priority: list[str], limit: int, countries: list[str] | None = None,
                  domains: list[str] | None = None) -> list[dict]:
        """Websites to read now: never read, or read longer ago than recheck_days (retry_failed_days when it
        couldn't be read), and not refused recently. One row per domain (several companies can share a website).

        Never-read sites first, and the countries take turns: the oldest site of each country, then the
        second of each, ... (priority countries first in each round). Otherwise the US, with 25,000 company
        websites, would keep Sri Lanka waiting for weeks."""
        now = datetime.now(timezone.utc)
        ago = lambda days: now - timedelta(days=float(days))  # noqa: E731
        where, params = ["c.domain is not null", "c.website is not null"], []
        if domains:
            where.append("c.domain = any(%s)")
            params.append(list(domains))
        else:
            where.append("(s.domain is null or (s.status in ('ok', 'not_allowed') and s.checked_at < %s) "
                         "or (s.status not in ('ok', 'not_allowed') and s.checked_at < %s))")
            where.append("(r.domain is null or r.refused_at < %s)")
            params += [ago(config.get("recheck_days", 30)), ago(config.get("retry_failed_days", 7)),
                       ago(config.get("refused_wait_days", 14))]
        if countries:
            where.append("c.country = any(%s)")
            params.append(list(countries))
        codes = [c for c in priority if c.isalnum()]
        prio = ("case country " + " ".join(f"when '{c}' then {i}" for i, c in enumerate(codes))
                + f" else {len(codes)} end, ") if codes else ""
        rows = self.con.execute(f"""
            with due as (
                select c.domain,
                       (array_agg(c.website order by c.first_seen, c.id))[1] as website,
                       (array_agg(c.country order by c.first_seen, c.id))[1] as country,
                       min(c.first_seen) as first_seen, max(s.checked_at) is not null as read_before
                from companies.companies c
                     left join companies.sites s on s.domain = c.domain
                     left join companies.refusals r on r.domain = c.domain
                where {" and ".join(where)}
                group by c.domain)
            select domain, website, country from due
            order by read_before,
                     row_number() over (partition by read_before, country order by first_seen, domain),
                     {prio}country collate "C"
            limit {int(limit)}""", params).fetchall()
        return [{"domain": d, "website": w, "country": c} for d, w, c in rows]

    # ── runs ───────────────────────────────────────────────────
    def record_run(self, command: str, started_at: str, seconds: float, summary: dict) -> None:
        from warehouse.postgres import json_text
        self.con.execute("insert into companies.runs values (%s, %s, %s, %s)",
                         [started_at, command, seconds, json_text(summary)])

    def close(self) -> None:
        self.con.close()
