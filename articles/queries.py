"""What the web page reads from schema articles. Plain queries with no Django, on a read-only connection. Lists leave the article text out;
only one article's page reads it, so a list stays quick."""

TS = "to_char({} at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"
COUNTRY_NAMES = {"US": "United States", "GB": "United Kingdom", "IN": "India", "AU": "Australia", "CA": "Canada"}
TEXT_STATUS = {"feed": "Full text, from the feed", "page": "Full text, from the page", "short": "Only a little text: the page may need a browser, which we don't use",
               "not_allowed": "No text: the site's robots.txt doesn't allow reading this page", "refused": "No text: the site refused us, and we leave it alone", "failed": "No text: the page couldn't be read"}
ITEM_COLUMNS = f"""url, title, summary, company, company_id, countries, tags, {TS.format('published_at')} as published_at, word_count, text_status, author, sector"""


def _rows(con, sql: str, params=()) -> list[dict]:
    cur = con.execute(sql, params)
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def summary(con) -> dict:
    counts = _rows(con, f"""select count(*) as articles, count(distinct company_id) as companies, count(*) filter (where text_status in ('feed', 'page')) as full_text,
            count(*) filter (where first_seen_at > now() - interval '24 hours') as new_24h, {TS.format('max(published_at)')} as newest from articles.articles""")[0]
    list_size = con.execute("select count(*) from articles.companies where in_list").fetchone()[0]
    return {**counts, "list_size": list_size,
            "by_tag": _rows(con, "select t as tag, count(*) as n from articles.articles, unnest(tags) t group by 1 order by 2 desc, 1"),
            "top_companies": _rows(con, "select company_id, company, count(*) as n from articles.v_articles group by 1, 2 order by 3 desc, 2 limit 15"),
            "coverage": coverage(con), "last_run": last_run(con)}


def coverage(con) -> list[dict]:
    """Per country: how many companies are on the list, and how many we can actually read."""
    return _rows(con, """select c as country, count(*) as companies, count(*) filter (where working_feeds > 0) as with_feed, count(*) filter (where articles > 0) as with_articles, sum(articles)::int as articles
        from articles.v_companies, unnest(countries) c where in_list group by 1 order by 2 desc""")


def last_run(con) -> dict | None:
    found = _rows(con, f"select {TS.format('run_at')} as run_at, step, counts from articles.runs where step = 'collect' order by run_at desc limit 1")
    return found[0] if found else None


def company_choices(con) -> list[dict]:
    return _rows(con, "select company_id as id, company as name, count(*) as n from articles.v_articles group by 1, 2 order by 2")


def items(con, tag=None, country=None, company=None, days=None, q=None, limit=100, offset=0) -> dict:
    where, params = ["true"], []
    if tag == "none":
        where.append("cardinality(tags) = 0")
    elif tag:
        where.append("%s = any(tags)"); params.append(tag)
    if country:
        where.append("%s = any(countries)"); params.append(country)
    if company:
        where.append("company_id = %s"); params.append(company)
    if days:
        where.append("published_at >= now() - make_interval(days => %s)"); params.append(int(days))
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(title ilike %s or summary ilike %s or text ilike %s or company ilike %s)"); params += [like] * 4
    cond = " and ".join(where)
    total = con.execute(f"select count(*) from articles.v_articles where {cond}", params).fetchone()[0]
    rows = _rows(con, f"select {ITEM_COLUMNS} from articles.v_articles where {cond} order by v_articles.published_at desc nulls last, url limit {int(limit)} offset {int(offset)}", params)
    return {"total": total, "items": rows}


def article(con, url: str) -> dict | None:
    found = _rows(con, f"select {ITEM_COLUMNS}, text, indexes, feed_url, date_from from articles.v_articles where url = %s", [url])
    if not found:
        return None
    row = found[0]
    row["more"] = _rows(con, f"""select url, title, {TS.format('published_at')} as published_at from articles.v_articles where company_id = %s and url <> %s order by v_articles.published_at desc nulls last limit 6""", [row["company_id"], url])
    return row


def companies(con, country=None, status=None, q=None, limit=100, offset=0) -> dict:
    where, params = ["in_list"], []
    if country:
        where.append("%s = any(countries)"); params.append(country)
    if status == "articles":
        where.append("articles > 0")
    elif status == "feed":
        where.append("working_feeds > 0 and articles = 0")
    elif status == "none":
        where.append("working_feeds = 0")
    if q:
        where.append("(name ilike %s or sector ilike %s or ticker ilike %s)"); params += [f"%{q}%"] * 3
    cond = " and ".join(where)
    total = con.execute(f"select count(*) from articles.v_companies where {cond}", params).fetchone()[0]
    rows = _rows(con, f"""select id, name, countries, indexes, sector, ticker, website, working_feeds, articles, {TS.format('latest_article')} as latest_article, feeds_note
        from articles.v_companies where {cond} order by articles desc, working_feeds desc, name limit {int(limit)} offset {int(offset)}""", params)
    return {"total": total, "items": rows}
