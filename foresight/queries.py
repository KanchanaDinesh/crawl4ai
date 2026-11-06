"""What the web page and others read from schema foresight. Plain queries with no Django, on a read-only connection, so the page
never re-does an analyst's work."""

from .analysts.news import OUTCOME_LABELS, QUESTIONS

QUESTION_LABELS = {"price_7d": "Price in 7 days", **{k: v["label"] for k, v in QUESTIONS.items()}}
QUESTION_OUTCOMES = {"price_7d": ("up", "flat", "down"), **{k: v["outcomes"] for k, v in QUESTIONS.items()}}
QUESTION_ZONES = {"price_7d": ("oversold", "middle", "overbought"), **{k: v["zones"] for k, v in QUESTIONS.items()}}
GROUP_LABELS = {"stock_lk": "CSE shares", "crypto": "Coins", "stories": "Stories", "topics": "Topics"}

COLUMNS = """f.id, f.section, f.grp, f.subject, f.subject_name, f.question, f.as_of::text as as_of, f.check_on::text as check_on, f.start_value,
    f.scenarios, f.reasons, f.signals, f.cases, f.separate_weeks, f.method, f.method_version"""


def _rows(con, sql: str, params=()) -> list[dict]:
    cur = con.execute(sql, params)
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def open_forecasts(con, section: str, question: str | None = None, grp: str | None = None, zone: str | None = None, q: str | None = None, limit: int = 500) -> list[dict]:
    """The newest forecast of each subject and question whose date hasn't been checked yet."""
    where, params = ["f.section = %s", "o.forecast_id is null"], [section]
    if question:
        where.append("f.question = %s"); params.append(question)
    if grp:
        where.append("f.grp = %s"); params.append(grp)
    if zone:
        where.append("f.signals ->> 'zone' = %s"); params.append(zone)
    if q:
        where.append("(f.subject ilike %s or f.subject_name ilike %s)"); params += [f"%{q}%"] * 2
    return _rows(con, f"""select distinct on (f.subject, f.question) {COLUMNS}
        from foresight.forecasts f left join foresight.outcomes o on o.forecast_id = f.id
        where {' and '.join(where)} order by f.subject, f.question, f.as_of desc limit {int(limit)}""", params)


def counts(con, section: str) -> dict:
    row = _rows(con, """select count(*) filter (where o.forecast_id is null) as open, count(o.score) as checked,
            count(o.forecast_id) filter (where o.score is null) as not_checkable, min(f.check_on) filter (where o.forecast_id is null)::text as next_check,
            max(f.as_of)::text as newest
        from foresight.forecasts f left join foresight.outcomes o on o.forecast_id = f.id where f.section = %s""", [section])[0]
    row["questions"] = _rows(con, """select f.question, count(distinct f.subject) as n from foresight.forecasts f left join foresight.outcomes o on o.forecast_id = f.id
        where f.section = %s and o.forecast_id is null group by 1""", [section])
    row["groups"] = _rows(con, """select f.grp, count(distinct f.subject) as n from foresight.forecasts f left join foresight.outcomes o on o.forecast_id = f.id
        where f.section = %s and o.forecast_id is null group by 1""", [section])
    return row


def backtests(con, section: str) -> list[dict]:
    """The newest test of each question and group."""
    return _rows(con, """select distinct on (grp, question) grp, question, verdict, cases, weeks, score, normal_score, skill, skill_low, skill_high,
            first_day::text as first_day, last_day::text as last_day, details
        from foresight.backtests where section = %s order by grp, question, run_at desc""", [section])


def track_record(con, section: str) -> list[dict]:
    return _rows(con, """select grp, question, checked, score, normal_score, skill, not_checkable from foresight.v_track_record where section = %s order by grp, question""", [section])


def latest_run(con, section: str) -> dict | None:
    found = _rows(con, "select to_char(run_at at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') as run_at, made, checked, not_made, note, progress from foresight.runs where section = %s order by runs.run_at desc limit 1", [section])
    return found[0] if found else None


def recent_outcomes(con, section: str, limit: int = 12) -> list[dict]:
    return _rows(con, f"""select {COLUMNS}, o.actual, o.score, o.normal_score, o.move_pct, o.end_value from foresight.outcomes o join foresight.forecasts f on f.id = o.forecast_id
        where f.section = %s order by f.check_on desc, f.id limit {int(limit)}""", [section])


def forecast(con, forecast_id: int) -> dict | None:
    found = _rows(con, f"""select {COLUMNS}, o.actual, o.score, o.normal_score, o.move_pct, o.end_value, o.end_day::text as end_day
        from foresight.forecasts f left join foresight.outcomes o on o.forecast_id = f.id where f.id = %s""", [forecast_id])
    return found[0] if found else None


def earlier(con, section: str, subject: str, question: str, before_id: int, limit: int = 8) -> list[dict]:
    """The same question about the same subject, before this one."""
    return _rows(con, f"""select {COLUMNS}, o.actual, o.score, o.normal_score from foresight.forecasts f left join foresight.outcomes o on o.forecast_id = f.id
        where f.section = %s and f.subject = %s and f.question = %s and f.id <> %s order by f.as_of desc limit {int(limit)}""", [section, subject, question, before_id])


def watching(con, limit: int = 15) -> dict:
    """The stories saved for the newest day, biggest first."""
    day = con.execute("select max(day) from foresight.signals where section = 'news' and kind = 'story'").fetchone()[0]
    if day is None:
        return {"day": None, "stories": []}
    found = con.execute("select payload from foresight.signals where section = 'news' and kind = 'story' and day = %s", [day]).fetchall()
    return {"day": day.isoformat(), "stories": sorted((r[0] for r in found), key=lambda s: -s["sources"])[:limit]}


def topics_now(con, days: int = 7) -> list[dict]:
    """Each topic's share of headlines over the saved days so far, newest week. A plain count, not a forecast."""
    return _rows(con, f"""select subject as topic, sum((payload ->> 'count')::int) as headlines, sum((payload ->> 'total')::int) as total,
            round(100.0 * sum((payload ->> 'count')::int) / nullif(sum((payload ->> 'total')::int), 0), 1)::float8 as share, count(*) as days
        from foresight.signals where section = 'news' and kind = 'topic'
            and day > (select max(day) from foresight.signals where section = 'news' and kind = 'topic') - {int(days)}
        group by 1 order by share desc""")


__all__ = ["OUTCOME_LABELS", "QUESTION_LABELS", "QUESTION_OUTCOMES", "QUESTION_ZONES", "GROUP_LABELS"]
