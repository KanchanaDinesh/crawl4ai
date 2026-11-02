"""SQL behind the dashboard. Each function takes an open (read-only) DuckDB connection.

Times are returned as ISO strings in UTC ("2026-11-02T08:15:00Z"); the browser shows them in local time.
"""

import json

UTC = "%Y-%m-%dT%H:%M:%SZ"


def rows(con, sql: str, params=None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def one(con, sql: str, params=None) -> dict:
    found = rows(con, sql, params)
    return found[0] if found else {}


def _ts(col: str) -> str:
    return f"strftime({col}, '{UTC}')"


# ── news ─────────────────────────────────────────────────────────────────
def news_summary(con, days: int) -> dict:
    last = one(con, f"""select {_ts('run_at')} as run_at, sources, sources_ok, headlines, new_headlines, item_limit
                        from news_runs order by run_at desc limit 1""")
    totals = one(con, f"""select count(*) as headlines,
            count(*) filter (where first_seen_at >= now() - interval {int(days)} day) as in_period,
            count(*) filter (where first_seen_at >= current_date) as today,
            count(*) filter (where list_contains(topics, 'tech') and first_seen_at >= now() - interval {int(days)} day) as tech_in_period,
            count(distinct source) as sources_seen
        from news_items""")
    by_group = rows(con, f"""select group_code, count(*) as n from news_items
        where first_seen_at >= now() - interval {int(days)} day group by 1 order by 2 desc""")
    by_day = rows(con, f"""select strftime(date_trunc('day', coalesce(published_at, first_seen_at)), '%Y-%m-%d') as day,
            count(*) as n, count(*) filter (where list_contains(topics, 'tech')) as tech
        from news_items where coalesce(published_at, first_seen_at) >= now() - interval 14 day group by 1 order by 1""")
    top_sources = rows(con, f"""select source, group_code, count(*) as n from news_items
        where first_seen_at >= now() - interval {int(days)} day group by 1, 2 order by 3 desc limit 12""")
    return {"last_run": last, "totals": totals, "by_group": by_group, "by_day": by_day, "top_sources": top_sources}


def news_items(con, *, group=None, source=None, tech=False, new=False, q=None, days=7,
               limit=100, offset=0) -> dict:
    where, params = [f"coalesce(published_at, first_seen_at) >= now() - interval {int(days)} day"], []
    if group:
        where.append("group_code = ?"); params.append(group)
    if source:
        where.append("source = ?"); params.append(source)
    if tech:
        where.append("list_contains(topics, 'tech')")
    if new:
        where.append("first_seen_at >= (select date_trunc('second', max(run_at)) from news_runs)")
    if q:
        where.append("(title ilike ? or summary ilike ? or source ilike ?)"); params += [f"%{q}%"] * 3
    cond = " and ".join(where)
    total = con.execute(f"select count(*) from news_items where {cond}", params).fetchone()[0]
    items = rows(con, f"""select url, title, summary, source, group_code, topics, tech_terms, times_seen,
            {_ts('published_at')} as published_at, {_ts('first_seen_at')} as first_seen_at,
            first_seen_at >= (select date_trunc('second', max(run_at)) from news_runs) as is_new
        from news_items where {cond}
        order by coalesce(published_at, first_seen_at) desc limit {int(limit)} offset {int(offset)}""", params)
    return {"total": total, "items": items}


def news_sources(con) -> list[dict]:
    """Status of every source in the last run, plus how many runs in a row it has failed."""
    return rows(con, f"""
        with ordered as (
            select *, row_number() over (partition by source order by run_at desc) as rn from news_source_runs),
        streak as (
            select source, count(*) as failed_streak from ordered o
            where status = 'failed' and rn < coalesce((select min(rn) from ordered o2
                  where o2.source = o.source and o2.status = 'ok'), 1000000)
            group by 1)
        select o.source, o.group_code, o.status, o.via, o.headlines, o.error, {_ts('o.run_at')} as run_at,
               coalesce(s.failed_streak, 0) as failed_streak
        from ordered o left join streak s using (source) where o.rn = 1
        order by o.status, o.group_code, o.source""")


def news_runs(con, limit=20) -> list[dict]:
    return rows(con, f"""select {_ts('run_at')} as run_at, item_limit, sources, sources_ok, headlines, new_headlines,
            round(epoch(finished_at) - epoch(run_at)) as seconds
        from news_runs order by run_at desc limit {int(limit)}""")


# ── tech intel ───────────────────────────────────────────────────────────
TECH_DOMAINS = """
    select c.domain, c.source, c.url as candidate_url, c.detail,
           coalesce(c.signal_at, c.discovered_at) as seen_ts, c.discovered_at as found_ts,
           coalesce(q.status, 'not checked') as status, q.reason, q.category, q.score, q.title, q.final_url,
           r.registered_at as registered_ts, p.name, p.description
    from v_candidates c
    left join v_qualifications q using (domain)
    left join v_registrations r using (domain)
    left join v_profiles p using (domain)
"""


def tech_summary(con, days: int) -> dict:
    d = int(days)
    counts = one(con, f"""select
            (select count(distinct domain) from v_candidates) as candidates,
            (select count(*) from v_candidates where discovered_at >= now() - interval {d} day) as new_candidates,
            (select count(*) from v_qualifications where status = 'qualified') as qualified,
            (select count(*) from v_qualifications where status = 'watch') as watch,
            (select count(*) from v_qualifications where status = 'rejected') as rejected,
            (select count(*) from v_profiles) as profiled,
            (select count(*) from v_changes where detected_at >= now() - interval {d} day) as changes,
            (select count(*) from v_registrations where registered_at is not null) as with_registration""")
    by_category = rows(con, "select category, count(*) as n from v_qualifications where status = 'qualified' group by 1 order by 2 desc")
    by_source = rows(con, f"select source, count(*) as n from v_candidates where discovered_at >= now() - interval {d} day group by 1 order by 2 desc")
    by_status = rows(con, f"""select coalesce(q.status, 'not checked') as status, count(*) as n
        from v_candidates c left join v_qualifications q using (domain)
        where c.discovered_at >= now() - interval {d} day group by 1 order by 2 desc""")
    tech_stack = rows(con, """select t.value::varchar as tech, count(*) as n
        from v_profiles, json_each(tech_stack) t group by 1 order by 2 desc limit 15""")
    by_day = rows(con, """select strftime(date_trunc('day', discovered_at), '%Y-%m-%d') as day, count(*) as n
        from v_candidates where discovered_at >= now() - interval 14 day group by 1 order by 1""")
    for r in tech_stack:
        r["tech"] = r["tech"].strip('"')
    return {"counts": counts, "by_category": by_category, "by_source": by_source, "by_status": by_status,
            "tech_stack": tech_stack, "by_day": by_day}


def tech_domains(con, *, status=None, category=None, source=None, q=None, days=30, limit=100, offset=0) -> dict:
    where, params = [f"seen_ts >= now() - interval {int(days)} day or found_ts >= now() - interval {int(days)} day"], []
    if status:
        where.append("status = ?"); params.append(status)
    if category:
        where.append("category = ?"); params.append(category)
    if source:
        where.append("source = ?"); params.append(source)
    if q:
        where.append("(domain ilike ? or coalesce(title, '') ilike ? or coalesce(name, '') ilike ? "
                     "or coalesce(description, '') ilike ? or coalesce(reason, '') ilike ?)")
        params += [f"%{q}%"] * 5
    cond = " and ".join(f"({w})" for w in where)
    base = f"select * from ({TECH_DOMAINS}) where {cond}"
    total = con.execute(f"select count(*) from ({base})", params).fetchone()[0]
    items = rows(con, f"""select domain, source, status, reason, category, score, coalesce(name, title) as name,
            description, coalesce(final_url, candidate_url) as url, detail->>'title' as source_title,
            {_ts('seen_ts')} as seen_at, {_ts('found_ts')} as found_at, {_ts('registered_ts')} as registered_at
        from ({base}) order by seen_ts desc nulls last, score desc nulls last
        limit {int(limit)} offset {int(offset)}""", params)
    return {"total": total, "items": items}


def tech_domain(con, domain: str) -> dict:
    def latest_json(table: str, extra: str = "") -> dict | None:
        r = con.execute(f"select data from {table} where key = ? {extra} order by row_id desc limit 1", [domain]).fetchone()
        return json.loads(r[0]) if r else None

    history = [json.loads(d) for (d,) in con.execute(
        "select data from qualifications where key = ? order by row_id", [domain]).fetchall()]
    changes = rows(con, f"""select change_type, field, json_extract_string(before, '$') as before, json_extract_string(after, '$') as after,
        {_ts('detected_at')} as detected_at from v_changes where domain = ? order by detected_at desc""", [domain])
    profile = latest_json("profiles", "and data->>'status' = 'ok'")
    if profile:
        profile.pop("external_domains", None)
        for page in profile.get("pages", []):
            page.pop("raw_file", None)
    return {"domain": domain, "candidate": latest_json("candidates"), "qualifications": history,
            "registration": latest_json("registrations"), "profile": profile, "changes": changes}


def tech_changes(con, days=30, limit=100) -> list[dict]:
    return rows(con, f"""select domain, change_type, field, json_extract_string(before, '$') as before, json_extract_string(after, '$') as after,
        {_ts('detected_at')} as detected_at from v_changes
        where detected_at >= now() - interval {int(days)} day order by detected_at desc limit {int(limit)}""")


def tech_runs(con) -> list[dict]:
    return rows(con, """select key as stage, data->>'status' as status, data->>'finished_at' as finished_at,
            (data->'summary')::varchar as summary
        from runs qualify row_number() over (partition by key order by row_id desc) = 1 order by finished_at desc""")
