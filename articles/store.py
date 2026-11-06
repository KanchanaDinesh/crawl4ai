"""Articles' database: PostgreSQL schema `articles` (warehouse/schema/articles.sql). Saved in short transactions, one company or feed at a time,
so a run stopped anywhere continues where it was."""

import json


class ArticleStore:
    def __init__(self):
        from warehouse import postgres
        self.con = postgres.connect("writer", autocommit=True)
        self.con.execute("set timezone = 'UTC'")
        postgres.migrate(self.con, ("articles",))

    def save_companies(self, rows: list[dict]) -> None:
        from warehouse.postgres import clean_text as t
        with self.con.transaction():
            cur = self.con.cursor()
            for r in rows:
                cur.execute("""insert into articles.companies (id, name, countries, indexes, in_list, website, ticker, sector, market_cap, cap_currency, cap_date, source)
                    values (%s, %s, %s, %s, true, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (id) do update set name = excluded.name, countries = excluded.countries, indexes = excluded.indexes, in_list = true,
                        website = coalesce(excluded.website, articles.companies.website), ticker = coalesce(excluded.ticker, articles.companies.ticker),
                        sector = coalesce(excluded.sector, articles.companies.sector), market_cap = coalesce(excluded.market_cap, articles.companies.market_cap),
                        cap_currency = coalesce(excluded.cap_currency, articles.companies.cap_currency), cap_date = coalesce(excluded.cap_date, articles.companies.cap_date),
                        source = excluded.source, updated_at = now()""",
                            [r["id"], t(r["name"]), r["countries"], r["indexes"], t(r.get("website")), t(r.get("ticker")), t(r.get("sector")), r.get("market_cap"), r.get("cap_currency"), r.get("cap_date"), r["source"]])

    def retire_others(self, index_names: list[str], keep_ids: list[str]) -> int:
        """Those whose indexes were all read and no longer have them have left the list. Their articles stay."""
        cur = self.con.execute("""update articles.companies set in_list = false, updated_at = now()
            where in_list and source = 'wikipedia' and indexes <@ %s and not (id = any(%s))""", [index_names, keep_ids])
        return cur.rowcount

    def companies_needing_feeds(self, again_after_days: int) -> list[dict]:
        cur = self.con.execute("""select id, name, website, countries from articles.companies c where in_list
            and (feeds_checked_at is null or feeds_checked_at < now() - make_interval(days => %s)) order by array_position(array['US','GB','IN','AU','CA'], countries[1]), name""", [again_after_days])
        return [dict(zip([c.name for c in cur.description], r)) for r in cur.fetchall()]

    def companies_missing_known_feeds(self, known_for) -> list[dict]:
        """A feed newly added to the settings shouldn't have to wait for the 30-day check."""
        have = {r[0] for r in self.con.execute("select url from articles.feeds").fetchall()}
        cur = self.con.execute("select id, name, website, countries from articles.companies where in_list")
        rows = [dict(zip([c.name for c in cur.description], r)) for r in cur.fetchall()]
        return [c for c in rows if set(known_for(c)) - have]

    def save_feeds(self, company_id: str, feeds: list[dict], note: str) -> None:
        from warehouse.postgres import clean_text as t
        with self.con.transaction():
            cur = self.con.cursor()
            for f in feeds:
                cur.execute("""insert into articles.feeds (url, company_id, found_how, title) values (%s, %s, %s, %s)
                    on conflict (url) do update set title = excluded.title""", [f["url"], company_id, f["found_how"], t(f.get("title"))])
            cur.execute("update articles.companies set feeds_checked_at = now(), feeds_note = %s where id = %s", [t(note), company_id])

    def feeds_due(self, every_hours: int, country: str | None = None) -> list[dict]:
        """Those never read first, then the longest waiting. One feed gets asked at a time per site, whichever order."""
        where = "and %s = any(c.countries)" if country else ""
        cur = self.con.execute(f"""select f.url, f.company_id, c.name as company, c.countries from articles.feeds f join articles.companies c on c.id = f.company_id
            where (f.refused_until is null or f.refused_until < now()) and (f.last_checked is null or f.last_checked < now() - make_interval(hours => %s)) {where}
            order by f.last_checked nulls first, f.url""", [every_hours, country] if country else [every_hours])
        return [dict(zip([c.name for c in cur.description], r)) for r in cur.fetchall()]

    def known_urls(self, company_id: str) -> set[str]:
        return {r[0] for r in self.con.execute("select url from articles.articles where company_id = %s", [company_id]).fetchall()}

    def save_feed_result(self, feed: dict, result: dict, refused_wait_days: int) -> int:
        from warehouse.postgres import clean_text as t
        added = 0
        with self.con.transaction():
            cur = self.con.cursor()
            for a in result["new"]:
                cur.execute("""insert into articles.articles (url, company_id, feed_url, title, summary, text, text_status, word_count, author, published_at, date_from, tags)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict (url) do nothing""",
                            [a["url"], feed["company_id"], feed["url"], t(a["title"]), t(a["summary"]), t(a["text"]), a["text_status"], a["word_count"], t(a["author"]), a["published_at"], a["date_from"], a["tags"]])
                added += cur.rowcount
            cur.execute("""update articles.feeds set last_checked = now(), last_ok = case when %s then now() else last_ok end, items_last = %s, error = %s,
                    refused_until = case when %s then now() + make_interval(days => %s) end where url = %s""",
                        [result["status"] == "ok", result["items"], t(result["error"]), result["status"] == "refused", refused_wait_days, feed["url"]])
        return added

    def retag(self, compiled: dict, body_hits_needed: int) -> dict:
        """Apply the word lists to what's saved. Nothing is downloaded. An article that no longer has a tag stays, with none: the text cost a
        request to get, and the word list may change again."""
        from .tagging import tag_article
        rows = self.con.execute("select url, title, summary, text, tags from articles.articles").fetchall()
        changed = [(new, url) for url, title, summary, text, tags in rows if (new := tag_article(title, summary, text, compiled, body_hits_needed)) != tags]
        if changed:
            with self.con.transaction():
                self.con.cursor().executemany("update articles.articles set tags = %s where url = %s", changed)
        return {"articles": len(rows), "changed": len(changed), "no_tag_now": sum(1 for new, _ in changed if not new)}

    def save_run(self, step: str, seconds: float, counts: dict, note: str | None = None) -> None:
        self.con.execute("insert into articles.runs (step, seconds, counts, note) values (%s, %s, %s, %s)", [step, seconds, json.dumps(counts), note])

    def close(self) -> None:
        self.con.close()
