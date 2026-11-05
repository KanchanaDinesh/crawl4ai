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
    topics_by_day = rows(con, f"""select strftime(date_trunc('day', coalesce(published_at, first_seen_at)), '%Y-%m-%d') as day,
            count(*) filter (where list_contains(topics, 'tech')) as tech,
            count(*) filter (where list_contains(topics, 'crypto')) as crypto,
            count(*) filter (where list_contains(topics, 'markets')) as markets
        from news_items where coalesce(published_at, first_seen_at) >= now() - interval 14 day group by 1 order by 1""")
    # UTC hour of publishing (the browser shifts it to local time); feeds that give only a date
    # show up as exactly midnight, so those are left out
    by_hour = rows(con, f"""select hour(published_at) as hour, count(*) as n from news_items
        where published_at >= now() - interval {max(int(days), 7)} day
          and strftime(published_at, '%H:%M:%S') <> '00:00:00' group by 1 order by 1""")
    return {"last_run": last, "totals": totals, "by_group": by_group, "by_day": by_day, "top_sources": top_sources,
            "topics_by_day": topics_by_day, "by_hour": by_hour}


def news_items(con, *, group=None, source=None, tech=False, topic=None, new=False, q=None, days=7,
               limit=100, offset=0) -> dict:
    where, params = [f"coalesce(published_at, first_seen_at) >= now() - interval {int(days)} day"], []
    if group:                                        # one group, or several: "MARKETS,CRYPTO"
        codes = [g.strip() for g in group.split(",") if g.strip()]
        where.append(f"group_code in ({', '.join('?' * len(codes))})"); params += codes
    if source:
        where.append("source = ?"); params.append(source)
    topic = topic or ("tech" if tech else None)
    if topic:
        where.append("list_contains(topics, ?)"); params.append(topic)
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


def news_analysis(con, days: int, groups: list[str] | None = None) -> dict:
    """Our own analysis of the period's headlines (worldnews/analysis.py): stories, topics, names, countries, tone."""
    from worldnews.analysis import analyze, balance_label
    from worldnews.analysis_report import fetch_items
    from worldnews.settings import load_settings
    settings = load_settings()
    items, previous = fetch_items(con, days, groups)
    if not items:
        return {"headlines": 0}
    result = analyze(items, previous, settings.section("analysis"), settings.group_names)
    result["tone"]["label"] = balance_label(result["tone"]["all"]["balance"])
    for g in result["tone"]["by_group"]:
        g["label"] = balance_label(g["balance"])
    return result


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
           r.registered_at as registered_ts, coalesce(p.name, co.name) as name, p.description,
           co.social, co.employees, json_array_length(co.people) as people, co.github_repos
    from v_candidates c
    left join v_qualifications q using (domain)
    left join v_registrations r using (domain)
    left join v_profiles p using (domain)
    left join {companies} co using (domain)
"""
# A database from before `techintel company` existed has no v_companies view until techintel opens it again
NO_COMPANIES = """(select null::varchar as domain, null::varchar as name, null::json as social, null::varchar as employees,
                  null::json as people, null::integer as github_repos where false)"""
TECH_COMPANY = {"linkedin": "social->>'linkedin' is not null", "people": "people > 0", "employees": "employees is not null",
                "any": "social is not null and social::varchar <> '{}'"}


def has_view(con, name: str) -> bool:
    return con.execute("select count(*) from duckdb_views() where view_name = ?", [name]).fetchone()[0] > 0


def _companies(con) -> str:
    return "v_companies" if has_view(con, "v_companies") else NO_COMPANIES


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
    by_day = rows(con, """select strftime(date_trunc('day', c.discovered_at), '%Y-%m-%d') as day, count(*) as n,
            count(*) filter (where q.status = 'qualified') as qualified
        from v_candidates c left join v_qualifications q using (domain)
        where c.discovered_at >= now() - interval 14 day group by 1 order by 1""")
    # How old the qualified sites' domains are: a site registered last week is a real launch signal
    domain_age = rows(con, """select case
                when r.registered_at is null then 'unknown'
                when r.registered_at >= now() - interval 7 day then 'under 1 week'
                when r.registered_at >= now() - interval 30 day then 'under 1 month'
                when r.registered_at >= now() - interval 365 day then 'under 1 year'
                when r.registered_at >= now() - interval 1825 day then '1-5 years'
                else 'over 5 years' end as age, count(*) as n
        from v_qualifications q left join v_registrations r using (domain)
        where q.status = 'qualified' group by 1""")
    order = ["under 1 week", "under 1 month", "under 1 year", "1-5 years", "over 5 years", "unknown"]
    domain_age.sort(key=lambda r: order.index(r["age"]))
    companies = []
    if has_view(con, "v_companies"):
        counts.update(one(con, """select count(*) as companies, count(linkedin) as with_linkedin,
            count(*) filter (where json_array_length(people) > 0) as with_people from v_companies"""))
        companies = rows(con, """select s.key as platform, count(*) as n from v_companies, json_each(social) s
            group by 1 order by 2 desc limit 12""")
    new_domains = one(con, f"""select count(*) as n from v_qualifications q join v_registrations r using (domain)
        where q.status = 'qualified' and r.registered_at >= now() - interval 30 day""")
    counts["new_domains_30d"] = new_domains.get("n", 0)
    for r in tech_stack:
        r["tech"] = r["tech"].strip('"')
    return {"counts": counts, "by_category": by_category, "by_source": by_source, "by_status": by_status,
            "tech_stack": tech_stack, "by_day": by_day, "domain_age": domain_age, "social_platforms": companies}


TECH_SORT = {"seen": "seen_ts desc nulls last, score desc nulls last",
             "score": "score desc nulls last, seen_ts desc nulls last",
             "registered": "registered_ts desc nulls last, seen_ts desc nulls last",
             "name": "lower(coalesce(name, title, domain))"}


def tech_domains(con, *, status=None, category=None, source=None, q=None, days=30, limit=100, offset=0,
                 sort="seen", company=None) -> dict:
    where, params = [f"seen_ts >= now() - interval {int(days)} day or found_ts >= now() - interval {int(days)} day"], []
    if status:
        where.append("status = ?"); params.append(status)
    if category:
        where.append("category = ?"); params.append(category)
    if source:
        where.append("source = ?"); params.append(source)
    if company in TECH_COMPANY:
        where.append(TECH_COMPANY[company])
    if q:
        where.append("(domain ilike ? or coalesce(title, '') ilike ? or coalesce(name, '') ilike ? "
                     "or coalesce(description, '') ilike ? or coalesce(reason, '') ilike ?)")
        params += [f"%{q}%"] * 5
    cond = " and ".join(f"({w})" for w in where)
    base = f"select * from ({TECH_DOMAINS.format(companies=_companies(con))}) where {cond}"
    total = con.execute(f"select count(*) from ({base})", params).fetchone()[0]
    items = rows(con, f"""select domain, source, status, reason, category, score, coalesce(name, title) as name,
            description, coalesce(final_url, candidate_url) as url, detail->>'title' as source_title,
            {_ts('seen_ts')} as seen_at, {_ts('found_ts')} as found_at, {_ts('registered_ts')} as registered_at,
            social, employees, people, github_repos
        from ({base}) order by {TECH_SORT.get(sort, TECH_SORT["seen"])}
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
    company = latest_json("companies", "and data->>'status' = 'ok'") if has_view(con, "v_companies") else None
    return {"domain": domain, "candidate": latest_json("candidates"), "qualifications": history,
            "registration": latest_json("registrations"), "profile": profile, "changes": changes, "company": company}


def tech_changes(con, days=30, limit=100) -> list[dict]:
    return rows(con, f"""select domain, change_type, field, json_extract_string(before, '$') as before, json_extract_string(after, '$') as after,
        {_ts('detected_at')} as detected_at from v_changes
        where detected_at >= now() - interval {int(days)} day order by detected_at desc limit {int(limit)}""")


def tech_runs(con) -> list[dict]:
    return rows(con, """select key as stage, data->>'status' as status, data->>'finished_at' as finished_at,
            try_cast(data->>'seconds' as double) as seconds, (data->'summary')::varchar as summary
        from runs qualify row_number() over (partition by key order by row_id desc) = 1 order by finished_at desc""")


# ── markets ──────────────────────────────────────────────────────────────
KEY_SYMBOLS = [("index", "^GSPC"), ("index", "^IXIC"), ("index", "^DJI"), ("index", "^VIX"), ("index", "ASPI"),
               ("index", "S&P SL20"), ("commodity", "GC=F"), ("commodity", "SI=F"), ("crypto", "BTC"),
               ("crypto", "ETH"), ("fx", "LKR=X"), ("commodity_lkr", "GOLD24K_LKR_PAWN")]
QUOTE_COLS = f"""asset_class, symbol, name, price, prev_close, change, change_pct, currency, volume, market_cap, rank,
    high, low, {_ts('as_of')} as as_of, {_ts('run_at')} as run_at, source,
    try_cast(extra->>'pct_1h' as double) as pct_1h, try_cast(extra->>'pct_7d' as double) as pct_7d,
    try_cast(extra->>'turnover' as double) as turnover, extra->>'unit' as unit, extra->>'note' as note,
    extra->>'image' as image"""


def _spark(con, asset_class: str, symbol: str, days: int = 30) -> list[float]:
    pts = [r[0] for r in con.execute(f"""select price from quotes where asset_class = ? and symbol = ? and price is not null
        and run_at >= now() - interval {int(days)} day order by run_at""", [asset_class, symbol]).fetchall()]
    if len(pts) >= 3:
        return pts[-60:]
    extra = con.execute("select extra->'spark' from v_latest where asset_class = ? and symbol = ?", [asset_class, symbol]).fetchone()
    if extra and extra[0]:
        return [p for p in json.loads(extra[0]) if p is not None]
    return pts


def markets_summary(con) -> dict:
    cards = []
    for cls, sym in KEY_SYMBOLS:
        row = one(con, f"select {QUOTE_COLS} from v_latest where asset_class = ? and symbol = ?", [cls, sym])
        if row:
            row["spark"] = _spark(con, cls, sym)
            cards.append(row)
    last = one(con, f"select {_ts('run_at')} as run_at, quotes, errors::varchar as errors from runs order by run_at desc limit 1")
    sources = rows(con, """select source, grp, ok, count, error, seconds from source_runs
        where run_at = (select max(run_at) from runs) order by ok, grp, source""")
    counts = rows(con, "select asset_class, count(*) as n from v_latest group by 1 order by 1")
    return {"cards": cards, "last_run": last, "sources": sources, "counts": counts}


def markets_quotes(con, asset_class: str, q: str | None = None, limit: int = 500) -> list[dict]:
    where, params = ["asset_class = ?"], [asset_class]
    if asset_class == "index_all":
        where, params = ["asset_class = 'index'"], []
    if q:
        where.append("(symbol ilike ? or name ilike ?)"); params += [f"%{q}%"] * 2
    order = "rank nulls last" if asset_class == "crypto" else "market_cap desc nulls last, symbol"
    return rows(con, f"select {QUOTE_COLS} from v_latest where {' and '.join(where)} order by {order} limit {int(limit)}", params)


def history_points(con, asset_class: str, symbol: str, days: int = 90) -> list[dict]:
    """Price history, oldest first: [{"t": ISO time, "price": p}, ...].

    Two sources, merged: the daily closes Yahoo sends with each price (about 3 months, so there's
    history from the first run on), and the price saved by every run. Days that have saved runs use
    those; earlier days use the daily close."""
    saved = rows(con, f"""select {_ts('run_at')} as t, price from quotes
        where asset_class = ? and symbol = ? and price is not null and run_at >= now() - interval {int(days)} day
        order by run_at""", [asset_class, symbol])
    first_saved_day = saved[0]["t"][:10] if saved else "9999"
    daily = con.execute("select extra->'daily' from v_latest where asset_class = ? and symbol = ?",
                        [asset_class, symbol]).fetchone()
    cutoff = con.execute(f"select strftime(now() - interval {int(days)} day, '%Y-%m-%d')").fetchone()[0]
    early = []
    if daily and daily[0]:
        for day, close in json.loads(daily[0]):
            if close is not None and cutoff <= day < first_saved_day:
                early.append({"t": f"{day}T12:00:00Z", "price": close})   # midday UTC: the same date everywhere
    return early + saved


def markets_history(con, asset_class: str, symbol: str, days: int = 90) -> dict:
    pts = history_points(con, asset_class, symbol, days)
    latest = one(con, f"select {QUOTE_COLS} from v_latest where asset_class = ? and symbol = ?", [asset_class, symbol])
    return {"quote": latest, "points": pts, "spark": _spark(con, asset_class, symbol, days)}


def markets_compare(con, items: list[tuple[str, str]], days: int = 30) -> list[dict]:
    """Several instruments' history, for one chart (the browser shows each as % change from the start)."""
    out = []
    for cls, sym in items[:8]:
        q = one(con, "select asset_class, symbol, name, currency from v_latest where asset_class = ? and symbol = ?", [cls, sym])
        if q:
            out.append({**q, "points": history_points(con, cls, sym, days)})
    return out


PERF_HORIZONS = (("1d", 1), ("1w", 7), ("1m", 30), ("3m", 90))


def _change_since(points: list[dict], price: float, days: int, now_iso: str) -> float | None:
    """% change from the last price at or before `days` ago (None if the history doesn't go back that far)."""
    from datetime import datetime, timedelta
    now = datetime.strptime(now_iso, UTC)
    target = (now - timedelta(days=days)).strftime(UTC)
    before = [p["price"] for p in points if p["t"] <= target]
    if not before or not before[-1]:
        return None
    return (price - before[-1]) / before[-1] * 100


def markets_performance(con) -> list[dict]:
    """How the main instruments did over 1 day, 1 week, 1 month and 3 months."""
    now_iso = con.execute(f"select strftime(now(), '{UTC}')").fetchone()[0]
    picks = rows(con, f"""select {QUOTE_COLS} from v_latest
        where asset_class in ('index', 'commodity', 'fx', 'commodity_lkr') or (asset_class = 'crypto' and rank <= 5)
        order by case asset_class when 'index' then 1 when 'commodity' then 2 when 'commodity_lkr' then 3
                 when 'fx' then 4 else 5 end, rank nulls last, symbol""")
    out = []
    for q in picks:
        if q["price"] is None:
            continue
        pts = history_points(con, q["asset_class"], q["symbol"], 100)
        row = {k: q[k] for k in ("asset_class", "symbol", "name", "price", "currency", "change_pct")}
        for label, d in PERF_HORIZONS:
            row[label] = _change_since(pts, q["price"], d, now_iso)
        if q["change_pct"] is not None:
            row["1d"] = q["change_pct"]               # the source's own day change, the same as on the cards
        if row["1w"] is None and q.get("pct_7d") is not None:
            row["1w"] = q["pct_7d"]
        row["spark"] = [p["price"] for p in pts][-60:]
        out.append(row)
    return out


# ── vehicles (vehicles.duckdb) ────────────────────────────────────────────
VEHICLE_BASE = """
    with site_last as (select site, max(saved_at) as last_saved from v_vehicle_ads group by site),
         changes as (select url, price_lkr as new_price, previous_price_lkr, saved_at as changed_at
                     from v_vehicle_price_changes qualify row_number() over (partition by url order by saved_at desc) = 1)
    select l.*, l.first_seen >= s.last_saved as is_new, c.previous_price_lkr, c.changed_at
    from v_vehicle_ads_latest l join site_last s using (site) left join changes c using (url)
"""


def vehicles_summary(con, days: int = 7) -> dict:
    d = int(days)
    counts = one(con, f"""select count(*) as ads,
            count(*) filter (where is_new) as new_last_run,
            count(*) filter (where first_seen >= now() - interval 1 day) as new_24h,
            count(*) filter (where previous_price_lkr is not null and price_lkr < previous_price_lkr
                             and changed_at >= now() - interval {d} day) as price_drops,
            round(median(price_lkr)) as median_price
        from ({VEHICLE_BASE})""")
    sites = rows(con, f"""select site, count(*) as ads, count(*) filter (where is_new) as new_last_run,
            {_ts('max(saved_at)')} as last_saved from ({VEHICLE_BASE}) group by site order by site""")
    per_day = rows(con, """select strftime(date_trunc('day', first_seen), '%Y-%m-%d') as day, count(*) as n
        from v_vehicle_ads_latest where first_seen >= now() - interval 14 day group by 1 order by 1""")
    price_ranges = rows(con, """select case
                when price_lkr < 1000000 then 'under 1M' when price_lkr < 3000000 then '1M - 3M'
                when price_lkr < 6000000 then '3M - 6M' when price_lkr < 10000000 then '6M - 10M'
                when price_lkr < 20000000 then '10M - 20M' else '20M and more' end as range,
            min(price_lkr) as lo, count(*) as n
        from v_vehicle_ads_latest where price_lkr >= 50000 group by 1 order by lo""")
    # The make is the first word of the title ("Toyota Axio 2016" -> Toyota); prices above 50,000 only
    # (smaller numbers are rents or monthly payments)
    makes = rows(con, """select lower(split_part(trim(title), ' ', 1)) as make, count(*) as n,
            round(median(price_lkr) filter (where price_lkr >= 50000)) as median_price
        from v_vehicle_ads_latest where title is not null group by 1 order by 2 desc, 1 limit 12""")
    for r in makes:
        r["make"] = r["make"].capitalize() if r["make"] and r["make"].isalpha() and len(r["make"]) > 3 else (r["make"] or "").upper()
    return {"counts": counts, "sites": sites, "by_day": per_day, "price_ranges": price_ranges, "makes": makes}


def vehicle_ads(con, *, site=None, q=None, new=False, days=30, limit=100, offset=0) -> dict:
    where, params = [f"first_seen >= now() - interval {int(days)} day"], []
    if site:
        where.append("site = ?"); params.append(site)
    if new:
        where.append("is_new")
    if q:
        where.append("(title ilike ? or coalesce(location, '') ilike ?)"); params += [f"%{q}%"] * 2
    cond = " and ".join(where)
    total = con.execute(f"select count(*) from ({VEHICLE_BASE}) where {cond}", params).fetchone()[0]
    items = rows(con, f"""select site, title, url, price, price_lkr, previous_price_lkr, location, year, mileage, posted,
            image, times_seen, is_new, {_ts('first_seen')} as first_seen, {_ts('saved_at')} as last_seen,
            {_ts('changed_at')} as changed_at
        from ({VEHICLE_BASE}) b where {cond} order by b.first_seen desc, b.rank nulls last
        limit {int(limit)} offset {int(offset)}""", params)
    return {"total": total, "items": items}


def vehicle_price_changes(con, days: int = 30, limit: int = 30) -> list[dict]:
    return rows(con, f"""select site, title, url, previous_price_lkr, price_lkr, {_ts('saved_at')} as changed_at
        from v_vehicle_price_changes where saved_at >= now() - interval {int(days)} day
        order by saved_at desc limit {int(limit)}""")


# ── companies (python -m companies): PostgreSQL, schema companies ────────────
# These take a psycopg connection (reader login). Placeholders are %s, and times come back as UTC strings.
COMPANY_FILTERS = {
    "website": "website is not null", "no_website": "website is null", "read": "site_status = 'ok'",
    "not_read": "site_status = 'not read'", "failed": "site_status in ('unreachable', 'not_allowed')",
    "social": "social is not null and social <> '{}'::jsonb", "linkedin": "linkedin is not null",
    "people": "people > 0", "employees": "employees is not null",
}
COMPANY_SORT = {"best": "(website is null), (site_status <> 'ok'), name", "name": "name",
                "newest": "first_seen desc, name", "country": "country nulls last, name"}


def _pg_ts(col: str) -> str:
    """A timestamptz as "2026-11-02T08:15:00Z" (what the browser expects, like _ts for DuckDB)."""
    return f"""to_char({col} at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')"""


def _json(value, empty):
    """jsonb comes back from psycopg already decoded; older data or text columns may still be a JSON string."""
    if value is None:
        return empty
    return json.loads(value) if isinstance(value, str) else value


def _country_names() -> dict:
    try:
        from companies.countries import country_names
        return country_names()
    except Exception:  # noqa: BLE001 - names are only a nicety
        return {}


def companies_summary(con) -> dict:
    names = _country_names()
    counts = one(con, f"""select count(*) as companies, count(website) as with_website,
        count(distinct domain) as websites, count(distinct country) as countries, count(distinct source) as sources,
        count(*) filter (where first_seen >= now() - interval '1 day') as new_24h,
        {_pg_ts('max(first_seen)')} as last_added from companies.companies""")
    counts.update(one(con, """select count(*) filter (where status = 'ok') as sites_ok, count(*) as sites_checked,
        count(*) filter (where status = 'ok' and social is not null and social <> '{}'::jsonb) as sites_with_social,
        count(*) filter (where status = 'ok' and jsonb_typeof(people) = 'array' and jsonb_array_length(people) > 0)
            as sites_with_people
        from companies.sites"""))
    counts["refusals"] = one(con, "select count(*) as n from companies.refusals")["n"]
    by_country = rows(con, """select country, count(*) as n, count(website) as with_website,
        count(*) filter (where site_status = 'ok') as sites_ok from companies.v_companies
        group by 1 order by n desc, country""")
    for r in by_country:
        r["name"] = names.get(r["country"] or "", r["country"] or "unknown")
    by_source = rows(con, f"""select source, count(*) as n, count(website) as with_website,
        count(distinct country) as countries, {_pg_ts('max(last_seen)')} as last_seen from companies.companies
        group by 1 order by n desc""")
    by_status = rows(con, """select site_status as status, count(*) as n from companies.v_companies
        group by 1 order by n desc""")
    social = rows(con, """select k as platform, count(*) as n from companies.sites, jsonb_object_keys(social) k
        where status = 'ok' and jsonb_typeof(social) = 'object' group by 1 order by n desc, k""")
    by_day = rows(con, """select to_char(first_seen at time zone 'UTC', 'YYYY-MM-DD') as day, count(*) as n,
        count(website) as with_website
        from companies.companies where first_seen >= current_date - interval '14 days' group by 1 order by 1""")
    industries = rows(con, """select industry, count(*) as n from companies.companies where industry is not null
        group by 1 order by n desc, industry limit 15""")
    runs = rows(con, f"""select command, {_pg_ts('started_at')} as started_at, seconds, summary from companies.runs
        order by runs.started_at desc limit 10""")
    for r in runs:
        s = _json(r.pop("summary"), {})
        r["text"] = (f"{s.get('added', 0):,} new, {s.get('total', 0):,} in total" if r["command"] == "collect" else
                     f"{s.get('checked', 0)} websites: {s.get('ok', 0)} ok, {s.get('unreachable', 0)} unreachable, "
                     f"{s.get('refused', 0)} refused")
        r["problems"] = "; ".join(f"{k}: {v.get('refused') or v.get('error')}" for k, v in (s.get("sources") or {}).items()
                                  if v.get("refused") or v.get("error"))
    progress = []
    for source, n, done in con.execute("""select source, count(*), count(*) filter (where value->>'done_at' is not null)
            from companies.cursors where left(key, 1) <> '_' group by 1 order by 1""").fetchall():
        progress.append({"source": source, "parts": n, "done": done})
    return {"counts": counts, "by_country": by_country, "by_source": by_source, "by_status": by_status,
            "social": social, "by_day": by_day, "industries": industries, "runs": runs, "progress": progress}


def companies_list(con, *, country=None, source=None, status=None, q=None, sort="best", limit=100, offset=0) -> dict:
    where, params = ["true"], []
    if country:
        where.append("country = %s")
        params.append(country)
    if source:
        where.append("source = %s")
        params.append(source)
    if status in COMPANY_FILTERS:
        where.append(COMPANY_FILTERS[status])
    if q:
        where.append("(lower(name) like %s or domain ilike %s or industry ilike %s)")
        like = "%" + q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        params += [like] * 3
    base = f"from companies.v_companies where {' and '.join(where)}"
    total = con.execute(f"select count(*) {base}", params).fetchone()[0]
    items = rows(con, f"""select id, source, name, country, website, domain, industry, site_status, social::text as social,
        people, employees, {_pg_ts('first_seen')} as first_seen, {_pg_ts('checked_at')} as checked_at
        {base} order by {COMPANY_SORT.get(sort, COMPANY_SORT['best'])}, id limit {int(limit)} offset {int(offset)}""",
                 params)
    names = _country_names()
    for r in items:
        r["country_name"] = names.get(r["country"] or "", r["country"])
    return {"total": total, "items": items}


def company_detail(con, company_id: str) -> dict:
    c = one(con, f"""select id, source, source_id, name, country, website, domain, industry, extra,
        {_pg_ts('first_seen')} as first_seen, {_pg_ts('last_seen')} as last_seen
        from companies.companies where id = %s""", [company_id])
    if not c:
        return {}
    c["extra"] = _json(c["extra"], {})
    c["country_name"] = _country_names().get(c["country"] or "", c["country"])
    site = None
    if c["domain"]:
        site = one(con, f"""select domain, url, final_url, {_pg_ts('checked_at')} as checked_at, status, error, name,
            legal_name, founded, employees, employees_hint, address, people, emails, phones, social, linkedin_people, pages
            from companies.sites where domain = %s""", [c["domain"]]) or None
        if site:
            for k in ("people", "emails", "phones", "social", "linkedin_people", "pages"):
                site[k] = _json(site[k], {} if k == "social" else [])
        refusal = one(con, f"""select {_pg_ts('refused_at')} as refused_at, error from companies.refusals
            where domain = %s""", [c["domain"]])
        c["refusal"] = refusal or None
    same = rows(con, """select id, source, name, country from companies.companies where domain = %s and id <> %s
        order by source, id limit 20""", [c["domain"], c["id"]]) if c["domain"] else []
    return {"company": c, "site": site, "same_website": same}
