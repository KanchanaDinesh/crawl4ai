"""Companies database: data/companies/companies.duckdb (its own file, like the other tools).

    companies   one row per company per source: id ("wikidata:Q95", "gleif:<LEI>", "cse:JKH.N0000", ...),
                name, country (ISO code: US, GB, AU, IN, LK, ...), website, domain, industry, extra (source's own
                fields), first_seen, last_seen. The same company can come from several sources (different ids).
    sites       what `update` read on each company website (one row per domain, the latest check):
                status ok / unreachable / not_allowed (robots.txt), social links, people, employees, contacts ...
    refusals    websites that refused us (401/403/429): not results, only "don't ask again before ..."
    cursors     where each source stopped, per country (collect continues there on the next run)
    runs        one row per collect / update run, with its summary
    v_companies companies with their site check (status, social links, people, employees)

Saving is an upsert: a company seen again keeps its first_seen and gets the newest name/website/industry.
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

SCHEMA = """
create table if not exists companies (
    id varchar primary key, source varchar, source_id varchar, name varchar, country varchar, website varchar,
    domain varchar, industry varchar, extra json, first_seen timestamptz, last_seen timestamptz);
create table if not exists sites (
    domain varchar primary key, url varchar, final_url varchar, checked_at timestamptz, status varchar, error varchar,
    name varchar, legal_name varchar, founded varchar, employees varchar, employees_hint varchar, address varchar,
    people json, emails json, phones json, social json, linkedin_people json, pages json);
create table if not exists refusals (domain varchar primary key, refused_at timestamptz, error varchar);
create table if not exists cursors (source varchar, key varchar, value json, updated_at timestamptz, primary key (source, key));
create table if not exists runs (started_at timestamptz, command varchar, seconds double, summary json);

create or replace view v_companies as
    select c.id, c.source, c.source_id, c.name, c.country, c.website, c.domain, c.industry, c.first_seen, c.last_seen,
           coalesce(s.status, case when c.domain is null then 'no website' else 'not read' end) as site_status,
           s.checked_at, s.social, json_array_length(s.people) as people, coalesce(s.employees, s.employees_hint) as employees,
           s.social->>'linkedin' as linkedin, s.legal_name, s.founded
    from companies c left join sites s on s.domain = c.domain;
"""


def db_path() -> Path:
    from warehouse import DB_FILES
    return DB_FILES["companies"]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


class CompanyStore:
    def __init__(self, path: Path | None = None):
        from warehouse import connect
        self.path = Path(path or db_path())
        self.con = connect(self.path)
        self.con.execute("set TimeZone = 'UTC'")
        self.con.execute(SCHEMA)

    @property
    def download_dir(self) -> Path:
        """Big source files (Companies House zips) wait here until they're imported."""
        folder = self.path.parent / "downloads"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    # ── companies ──────────────────────────────────────────────
    def upsert_companies(self, rows: list[dict], seen_at: str | None = None) -> tuple[int, int]:
        """Add new companies and refresh known ones. Returns (added, updated).

        Rows go in as column lists (one fast statement), not one insert per row: a page of GLEIF or
        a Wikidata query can be 27,000 rows, and row-by-row inserts took 7s for 30,000."""
        seen_at = seen_at or now_iso()
        unique: dict[str, dict] = {}
        for r in rows:                                   # one row per id (a batch with the same id twice fails)
            if r.get("id") and r.get("name"):
                unique[r["id"]] = r
        if not unique:
            return 0, 0
        cols = {f: [] for f in COMPANY_FIELDS}
        for r in unique.values():
            for f in COMPANY_FIELDS:
                cols[f].append(_json(r.get(f)) if f == "extra" else r.get(f))
        con = self.con
        con.execute("create or replace temp table staged (id varchar, source varchar, source_id varchar, name varchar, "
                    "country varchar, website varchar, domain varchar, industry varchar, extra json)")
        con.execute("insert into staged select " + ", ".join(f"unnest(${i + 1})" for i in range(len(COMPANY_FIELDS))),
                    [cols[f] for f in COMPANY_FIELDS])
        added = con.execute("select count(*) from staged s where not exists (select 1 from companies c where c.id = s.id)"
                            ).fetchone()[0]
        con.execute("""
            insert into companies
            select id, source, source_id, name, country, website, domain, industry, extra, $1::timestamptz, $1::timestamptz
            from staged
            on conflict (id) do update set
                name = excluded.name, country = coalesce(excluded.country, companies.country),
                website = coalesce(excluded.website, companies.website), domain = coalesce(excluded.domain, companies.domain),
                industry = coalesce(excluded.industry, companies.industry), extra = excluded.extra,
                last_seen = excluded.last_seen""", [seen_at])
        con.execute("drop table staged")
        return added, len(unique) - added

    def count(self, where: str = "true", params: list | None = None) -> int:
        return self.con.execute(f"select count(*) from companies where {where}", params or []).fetchone()[0]

    # ── cursors: where each source stopped ─────────────────────
    def cursors(self, source: str) -> dict[str, dict]:
        rows = self.con.execute("select key, value from cursors where source = ?", [source]).fetchall()
        return {k: json.loads(v) for k, v in rows}

    def set_cursor(self, source: str, key: str, value: dict) -> None:
        self.con.execute("insert or replace into cursors values (?, ?, ?, ?)", [source, key, _json(value), now_iso()])

    # ── sites: what `update` read on each website ──────────────
    def save_site(self, row: dict) -> None:
        values = [_json(row.get(f)) if f in SITE_JSON else row.get(f) for f in SITE_FIELDS]
        self.con.execute(f"insert or replace into sites ({', '.join(SITE_FIELDS)}) values ({', '.join('?' * len(SITE_FIELDS))})",
                         values)
        self.con.execute("delete from refusals where domain = ?", [row["domain"]])

    def save_refusal(self, domain: str, error: str) -> None:
        self.con.execute("insert or replace into refusals values (?, ?, ?)", [domain, now_iso(), error])

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
            where.append("c.domain in (select unnest(?))")
            params.append(list(domains))
        else:
            where.append("(s.domain is null or (s.status in ('ok', 'not_allowed') and s.checked_at < ?) "
                         "or (s.status not in ('ok', 'not_allowed') and s.checked_at < ?))")
            where.append("(r.domain is null or r.refused_at < ?)")
            params += [ago(config.get("recheck_days", 30)), ago(config.get("retry_failed_days", 7)),
                       ago(config.get("refused_wait_days", 14))]
        if countries:
            where.append("c.country in (select unnest(?))")
            params.append(list(countries))
        codes = [c for c in priority if c.isalnum()]
        prio = ("case country " + " ".join(f"when '{c}' then {i}" for i, c in enumerate(codes))
                + f" else {len(codes)} end, ") if codes else ""
        rows = self.con.execute(f"""
            with due as (
                select c.domain, arg_min(c.website, c.first_seen) as website, arg_min(c.country, c.first_seen) as country,
                       min(c.first_seen) as first_seen, max(s.checked_at) is not null as read_before
                from companies c left join sites s on s.domain = c.domain left join refusals r on r.domain = c.domain
                where {" and ".join(where)}
                group by c.domain)
            select domain, website, country from due
            order by read_before,
                     row_number() over (partition by read_before, country order by first_seen, domain), {prio}country
            limit {int(limit)}""", params).fetchall()
        return [{"domain": d, "website": w, "country": c} for d, w, c in rows]

    # ── runs ───────────────────────────────────────────────────
    def record_run(self, command: str, started_at: str, seconds: float, summary: dict) -> None:
        self.con.execute("insert into runs values (?, ?, ?, ?)", [started_at, command, seconds, _json(summary)])

    def close(self) -> None:
        self.con.close()
