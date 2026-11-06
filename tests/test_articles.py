"""articles: the company lists, tags, reading feeds and pages, and the rules (robots.txt, refusals, no guessed dates), offline against local servers."""

import asyncio
import json
import time
from pathlib import Path

import pytest
from aiohttp import web as aioweb

from articles import companies_list as cl
from articles.cli import _collect, _companies, _feeds, _known_feeds
from articles.grabbing import Web, article_text, find_feeds, parse_items, read_article, read_feed
from articles.store import ArticleStore
from articles.tagging import compile_tags, tag_article
from test_pipeline_e2e import Site

SETTINGS = json.loads((Path(__file__).parent.parent / "articles" / "config" / "settings.json").read_text())
COMPILED = compile_tags(SETTINGS["tags"])
CFG = SETTINGS["collect"]
FETCH = {"user_agent": "honest-test-bot/1.0 (test)", "timeout_seconds": 5, "concurrency": 3, "gap_seconds": 0}
DISCOVERY = {**SETTINGS["discovery"], "subdomains": []}
LONG = " ".join(["This paragraph about how companies run their cloud platforms and what changed this year."] * 30)


# ── the lists ────────────────────────────────────────────────────────────────────────────────────────────────
def row(*cells):
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def table(heads, rows, extra=""):
    return f'<table class="wikitable{extra}"><tr>' + "".join(f"<th>{h}</th>" for h in heads) + "</tr>" + "".join(rows) + "</table>"


def members_html(n=35, heads=("Symbol", "Name", "Sector"), order=(0, 1, 2)):
    rows = [row(*[(f"T{i}", f'<a href="/wiki/Comp_{i}">Comp {i}</a>', "Tech")[j] for j in order]) for i in range(n)]
    return table(heads, rows)


def test_finds_the_members_table_among_others():
    decoy = table(("Year", "Closing level"), [row(y, 100) for y in range(40)]) + table(("Contract size", "Exchange"), [row("a", "b")])
    found = cl.parse_index_table(decoy + members_html())
    assert len(found) == 35 and found[0] == {"name": "Comp 0", "title": "Comp 0", "ticker": "T0", "sector": "Tech"}


@pytest.mark.parametrize("heads,order", [(("Company", "Ticker", "ISIN[34]", "FTSE industry classification benchmark sector[35]"), (1, 0, 2)), (("Company name", "Symbol", "Sector[15]"), (1, 0, 2)), (("Symbol", "Company", "Sector"), (0, 1, 2))])
def test_each_country_pages_table_shape(heads, order):
    html = table(heads, [row(*[(f"T{i}", f'<a href="/wiki/Comp_{i}">Comp {i}[1]</a>', "x")[j] for j in order]) for i in range(35)])
    found = cl.parse_index_table(html)
    assert len(found) == 35 and found[3]["ticker"] == "T3" and found[3]["name"] == "Comp 3" and found[3]["title"] == "Comp 3"


def test_sector_is_preferred_over_a_column_of_codes_and_share_classes_are_one_name():
    html = table(("Symbol", "Company", "GICS", "Sector"), [row(f"T{i}", "Alphabet Inc. (Class C)", "45", "Technology") for i in range(35)])
    assert cl.parse_index_table(html)[0]["sector"] == "Technology" and cl.parse_index_table(html)[0]["name"] == "Alphabet Inc."


def test_a_company_without_its_own_page_is_kept_by_name():
    html = table(("Symbol", "Company"), [row(f"T{i}", '<a href="/w/index.php?title=X&redlink=1">No Page Ltd</a>' if i == 0 else f"Plain {i}") for i in range(35)])
    found = cl.parse_index_table(html)
    assert found[0]["title"] is None and found[0]["name"] == "No Page Ltd" and found[1]["title"] is None


def test_page_ids_follow_redirects_and_normalised_titles():
    data = {"query": {"normalized": [{"from": "S&P_x", "to": "S&P x"}], "redirects": [{"from": "S&P x", "to": "Real"}, {"from": "Google", "to": "Google LLC"}],
                      "pages": [{"title": "Real", "pageprops": {"wikibase_item": "Q1"}}, {"title": "Google LLC", "pageprops": {"wikibase_item": "Q2"}}, {"title": "Nope", "missing": True}, {"title": "No item"}]}}
    assert cl.parse_page_ids(data) == {"S&P_x": "Q1", "S&P x": "Q1", "Real": "Q1", "Google": "Q2", "Google LLC": "Q2"}


def site_claim(url, rank="normal"):
    return {"rank": rank, "mainsnak": {"datavalue": {"value": url}}}


def cap_claim(amount, day, unit="Q4917", rank="normal"):
    return {"rank": rank, "mainsnak": {"datavalue": {"value": {"amount": f"+{amount}", "unit": f"http://www.wikidata.org/entity/{unit}"}}},
            "qualifiers": {"P585": [{"datavalue": {"value": {"time": f"+{day}T00:00:00Z"}}}]} if day else {}}


def test_website_and_the_newest_market_value():
    assert cl.pick_website([site_claim("https://www.apple.com/tw"), site_claim("https://apple.com/")]) == "https://apple.com/"
    assert cl.pick_website([site_claim("https://a.com/"), site_claim("https://long.example.com/x", "preferred")]) == "https://long.example.com/x"
    assert cl.pick_website([site_claim("http://a.com"), site_claim("https://longer.com")]) == "https://longer.com"
    assert cl.pick_website([]) is None
    apple = [site_claim("https://mac.com/"), site_claim("https://www.apple.com/")]
    assert cl.pick_website(apple, "Apple Inc.") == "https://www.apple.com/"
    data = {"entities": {"Q1": {"claims": {"P856": [site_claim("https://a.com")], "P2226": [cap_claim(5, "2020-01-01"), cap_claim(9, "2026-01-01"), cap_claim(99, "2027-01-01", rank="deprecated")]}}, "Q2": {"claims": {}}}}
    found = cl.parse_entities(data)
    assert found["Q1"] == {"website": "https://a.com", "cap_date": "2026-01-01", "market_cap": 9.0, "cap_currency": "Q4917"}
    assert found["Q2"] == {"website": None, "cap_date": None, "market_cap": None, "cap_currency": None}


def test_a_date_known_only_to_the_year_or_month_is_still_a_date():
    data = {"entities": {"Q1": {"claims": {"P2226": [cap_claim(5, "2023-00-00")]}}, "Q2": {"claims": {"P2226": [cap_claim(5, "2023-07-00")]}}}}
    found = cl.parse_entities(data)
    assert found["Q1"]["cap_date"] == "2023-01-01" and found["Q2"]["cap_date"] == "2023-07-01"


def test_one_row_per_company():
    base = {"ticker": None, "sector": None, "website": None, "market_cap": None, "cap_currency": None, "cap_date": None, "source": "wikipedia"}
    rows = [{**base, "id": "Q1", "name": "Alphabet", "countries": ["US"], "indexes": ["S&P 100"], "ticker": "GOOGL"}, {**base, "id": "Q1", "name": "Alphabet", "countries": ["US"], "indexes": ["S&P 100"], "ticker": "GOOG"},
            {**base, "id": "Q2", "name": "Rio Tinto", "countries": ["GB"], "indexes": ["FTSE 100"]}, {**base, "id": "Q2", "name": "Rio Tinto", "countries": ["AU"], "indexes": ["S&P/ASX 50"], "website": "https://r.com"}]
    merged = {c["id"]: c for c in cl.merge(rows)}
    assert len(merged) == 2 and merged["Q1"]["ticker"] == "GOOGL"
    assert merged["Q2"]["countries"] == ["AU", "GB"] and merged["Q2"]["indexes"] == ["FTSE 100", "S&P/ASX 50"] and merged["Q2"]["website"] == "https://r.com"


class FakeWikipedia:
    """Answers the three API calls of fetch_index."""
    def __init__(self, html):
        self.html = html

    async def get(self, url, host_gap=None):
        from types import SimpleNamespace
        if "action=parse" in url:
            body = {"parse": {"text": self.html}}
        elif "action=query" in url:
            body = {"query": {"pages": [{"title": f"Comp {i}", "pageprops": {"wikibase_item": f"Q{i}"}} for i in range(60)]}}
        else:
            body = {"entities": {f"Q{i}": {"claims": {"P856": [site_claim(f"https://c{i}.com")]}} for i in range(60)}}
        return SimpleNamespace(ok=True, text=json.dumps(body), error=None, status=200)


def test_fetch_index_gives_ids_and_websites():
    found = asyncio.run(cl.fetch_index(FakeWikipedia(members_html()), {"country": "IN", "name": "Nifty 50", "page": "NIFTY_50", "at_least": 30}))
    assert len(found) == 35 and found[2]["id"] == "Q2" and found[2]["website"] == "https://c2.com" and found[2]["countries"] == ["IN"]


def test_a_changed_page_is_an_error_and_the_old_list_stays(pg):
    store = ArticleStore()
    settings = {"fetch": FETCH, "indexes": [{"country": "IN", "name": "Nifty 50", "page": "NIFTY_50", "at_least": 30}], "extra_companies": []}
    import articles.cli as cli

    class GoodWeb(FakeWikipedia):
        def __init__(self, cfg):
            super().__init__(members_html())

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            pass
    original = cli.Web
    cli.Web = GoodWeb
    try:
        assert asyncio.run(_companies(store, settings))["companies"] == 35
        GoodWeb.__init__ = lambda self, cfg: FakeWikipedia.__init__(self, members_html(5))    # the page's layout changed
        result = asyncio.run(_companies(store, settings))
    finally:
        cli.Web = original
    assert "found only" in result["failed"]["Nifty 50"] and result["left_the_list"] == 0
    assert store.con.execute("select count(*) from articles.companies where in_list").fetchone()[0] == 35
    store.close()


def test_a_company_that_leaves_its_index_keeps_its_articles(pg):
    store = ArticleStore()
    base = {"ticker": None, "sector": None, "website": None, "market_cap": None, "cap_currency": None, "cap_date": None, "source": "wikipedia", "countries": ["IN"], "indexes": ["Nifty 50"]}
    store.save_companies([{**base, "id": "Q1", "name": "Stays"}, {**base, "id": "Q2", "name": "Leaves"}, {**base, "id": "Q3", "name": "Other index", "indexes": ["FTSE 100"]}])
    assert store.retire_others(["Nifty 50"], ["Q1"]) == 1
    assert dict(store.con.execute("select name, in_list from articles.companies").fetchall()) == {"Stays": True, "Leaves": False, "Other index": True}
    store.close()


# ── tags ─────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("title,want,refuse", [
    ("How we use AI to answer customers", "ai", None),
    ("Moving to the cloud: what we learned", "cloud", None),
    ("New email security for Gmail users", "email", None),
    ("Our new GPU for data centers", "chips", None),
    ("Ransomware attacks are rising", "security", None),
    ("Paint your house with Said's ai-free fair colours", None, "ai"),     # "ai" in lower case, and inside other words
    ("Cloudflare results day", None, "cloud"),                             # a name isn't the word
    ("Wet weather on the way", None, None),
])
def test_tags_in_the_title(title, want, refuse):
    tags = tag_article(title, "", "", COMPILED)
    if want:
        assert want in tags
    if refuse:
        assert refuse not in tags
    if not want and not refuse:
        assert tags == []


def test_one_mention_in_the_body_isnt_enough():
    assert tag_article("Annual results", "", "Profits rose. We also looked at AI once.", COMPILED) == []
    assert tag_article("Annual results", "", "AI here. AI there. AI everywhere.", COMPILED) == ["ai"]
    assert tag_article("Annual results", "An update on AI", "", COMPILED) == ["ai"]


def test_capitals_matter_only_for_words_written_in_capitals():
    assert tag_article("apis and more", "", "", COMPILED) == []
    assert "developer" in tag_article("A new API for payments", "", "", COMPILED)
    assert "cloud" in tag_article("CLOUD COSTS ARE RISING", "", "", COMPILED)


# ── reading ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_article_text_keeps_the_article_not_the_menus():
    html = """<html><body><nav><p>Home News Blog</p></nav><script>var a = 1;</script><aside><p>Subscribe now</p></aside><article><h2>Heading</h2><p>First   part.</p><p>Second part.</p></article><footer><p>Legal</p></footer></body></html>"""
    assert article_text(html) == "Heading\nFirst part.\nSecond part."
    assert "Only one" in article_text("<main><p>Only one</p></main>") and article_text("<div>No paragraphs here</div>") == "No paragraphs here"


def feed(*items, title="Our blog"):
    body = ""
    for i in items:
        date = f"<pubDate>{i['date']}</pubDate>" if i.get("date") else ""
        content = f"<content:encoded><![CDATA[{i['body']}]]></content:encoded>" if i.get("body") else ""
        author = f"<dc:creator>{i['author']}</dc:creator>" if i.get("author") else ""
        body += f"<item><title>{i['title']}</title><link>{i['url']}</link><description><![CDATA[{i.get('summary', '')}]]></description>{date}{content}{author}</item>"
    return f'<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>{title}</title>{body}</channel></rss>'


def test_parse_items_body_author_and_date():
    items = parse_items(feed({"title": "A", "url": "https://x.com/a#top", "summary": "<p>Short <b>bit</b></p>", "date": "Fri, 06 Nov 2026 10:00:00 GMT", "body": "<p>The whole thing</p>", "author": "Ada"}, {"title": "B", "url": "/b"}), "https://x.com/feed")
    assert items[0]["url"] == "https://x.com/a" and items[0]["summary"] == "Short bit" and items[0]["author"] == "Ada" and "whole thing" in items[0]["body_html"] and items[0]["published_at"].startswith("2026-11-06T10:00")
    assert items[1]["url"] == "https://x.com/b" and items[1]["published_at"] is None


class Hits:
    def __init__(self, body):
        self.body, self.count = body, 0

    def __call__(self, path):
        self.count += 1
        return self.body


def _site(pages):
    return Site({"/robots.txt": "User-agent: *\nDisallow: /private/\n", **pages})


def page_html(text=LONG, date=None):
    meta = f'<meta property="article:published_time" content="{date}">' if date else ""
    return f"<html><head>{meta}</head><body><article><p>{text}</p></article></body></html>"


def _read(item):
    async def go():
        async with Web(FETCH) as web:
            return await read_article(web, item, CFG, COMPILED), web
    return asyncio.run(go())


def test_full_body_in_the_feed_means_no_page_request():
    hits = Hits(page_html())
    site = _site({"/a": hits})
    article, _ = _read({"url": f"http://{site.domain}/a", "title": "Our cloud platform", "summary": "s", "body_html": f"<p>{LONG}</p>", "published_at": "2026-11-06T10:00:00+00:00"})
    assert hits.count == 0 and article["text_status"] == "feed" and article["word_count"] > 100 and "cloud" in article["tags"] and article["date_from"] == "feed"


def test_a_short_feed_item_opens_the_page_and_takes_its_date():
    site = _site({"/a": page_html(date="2026-10-01T08:00:00Z")})
    article, _ = _read({"url": f"http://{site.domain}/a", "title": "Our cloud platform", "summary": "s", "body_html": "", "published_at": None})
    assert article["text_status"] == "page" and article["published_at"].startswith("2026-10-01") and article["date_from"] == "page"


def test_no_date_anywhere_stays_empty_never_a_guess():
    site = _site({"/a": page_html()})
    article, _ = _read({"url": f"http://{site.domain}/a", "title": "Our cloud platform", "summary": "s", "body_html": "", "published_at": None})
    assert article["published_at"] is None and article["date_from"] == "none"


def test_a_page_robots_txt_disallows_isnt_opened():
    hits = Hits(page_html())
    site = _site({"/private/a": hits})
    article, _ = _read({"url": f"http://{site.domain}/private/a", "title": "Our cloud platform", "summary": "A summary", "body_html": "", "published_at": "2026-11-06T10:00:00+00:00"})
    assert hits.count == 0 and article["text_status"] == "not_allowed" and article["summary"] == "A summary" and article["text"] is None


def test_an_article_about_nothing_we_track_isnt_opened_or_kept():
    hits = Hits(page_html("Our charity football day raised money."))
    site = _site({"/a": hits})
    article, _ = _read({"url": f"http://{site.domain}/a", "title": "Charity football day", "summary": "Fun for everyone", "body_html": "", "published_at": None})
    assert article is None and hits.count == 0


def test_a_page_that_only_draws_itself_in_a_browser_is_marked_short():
    site = _site({"/a": "<html><body><div id=root></div></body></html>"})
    article, _ = _read({"url": f"http://{site.domain}/a", "title": "Our cloud platform", "summary": "s", "body_html": "", "published_at": None})
    assert article["text_status"] == "short" and article["word_count"] == 0


def run_refusing(handler_status, scenario):
    """A real local server that answers these statuses, so the honest headers and refusals are the real thing."""
    seen = []

    async def main():
        async def handler(request):
            seen.append(dict(request.headers))
            status = handler_status.get(request.path, 200)
            body = feed({"title": "Cloud news", "url": f"http://{request.host}/page", "summary": "s"}) if request.path == "/feed" else page_html()
            return aioweb.Response(text=body, status=status, content_type="text/html")
        app = aioweb.Application()
        app.router.add_get("/{tail:.*}", handler)
        runner = aioweb.AppRunner(app)
        await runner.setup()
        site = aioweb.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with Web(FETCH) as web:
                return await scenario(web, f"http://127.0.0.1:{port}"), seen
        finally:
            await runner.cleanup()
    return asyncio.run(main())


@pytest.mark.parametrize("status", [401, 403, 429])
def test_a_refused_feed_is_reported_and_the_host_left_alone(status):
    async def scenario(web, base):
        result = await read_feed(web, {"url": f"{base}/feed", "company_id": "x"}, set(), CFG, COMPILED, time.monotonic() + 60)
        return result, web.refused
    (result, refused), seen = run_refusing({"/feed": status}, scenario)
    assert result["status"] == "refused" and result["error"] == f"HTTP {status}" and len(refused) == 1 and len(seen) == 1


def test_a_refused_page_stops_more_pages_from_that_site_and_the_summary_is_kept():
    async def scenario(web, base):
        first = await read_article(web, {"url": f"{base}/page", "title": "Our cloud platform", "summary": "s", "body_html": "", "published_at": None}, CFG, COMPILED)
        second = await read_article(web, {"url": f"{base}/page2", "title": "More on AI", "summary": "s", "body_html": "", "published_at": None}, CFG, COMPILED)
        return first, second
    (first, second), seen = run_refusing({"/page": 403, "/page2": 200}, scenario)
    assert first["text_status"] == "refused" and second["text_status"] == "refused"
    assert not any("page2" in h.get("Referer", "") for h in seen)
    assert seen[-1]["User-Agent"] == "honest-test-bot/1.0 (test)" and "Origin" not in seen[-1] and "Referer" not in seen[-1]


# ── finding feeds ────────────────────────────────────────────────────────────────────────────────────────────
def _find(site, known=(), discovery=DISCOVERY):
    async def go():
        async with Web(FETCH) as web:
            return await find_feeds(web, {"id": "x", "name": "X", "website": f"http://{site.domain}/"}, discovery, list(known))
    return asyncio.run(go())


GOOD = feed({"title": "A", "url": "https://x.com/a"})


def test_finds_a_feed_from_the_homepage_and_skips_comment_feeds():
    site = _site({"/": '<html><head><link rel="alternate" type="application/rss+xml" href="/comments/feed/"><link rel="alternate" type="application/rss+xml" href="/feed/"></head></html>', "/feed/": GOOD, "/comments/feed/": GOOD})
    feeds, note = _find(site, discovery={**DISCOVERY, "paths": [], "max_feeds": 3})
    assert [f["url"].split(site.domain)[1] for f in feeds] == ["/feed/"] and feeds[0]["found_how"] == "homepage" and feeds[0]["title"] == "Our blog" and "1 feeds" in note


def test_finds_a_feed_at_the_usual_places_and_ignores_things_that_arent_feeds():
    site = _site({"/": "<html></html>", "/blog/": "<html>a page</html>", "/rss.xml": GOOD, "/feed/": "<rss><channel><title>empty</title></channel></rss>"})
    feeds, _ = _find(site)
    assert [f["found_how"] for f in feeds] == ["guess"] and feeds[0]["url"].endswith("/rss.xml")


def test_guessing_respects_robots_txt_and_its_budget():
    hits = Hits(GOOD)
    site = Site({"/robots.txt": "User-agent: *\nDisallow: /\n", "/": "<html></html>", "/feed/": hits})
    feeds, note = _find(site)
    assert feeds == [] and hits.count == 0 and "no feed" in note
    many = Hits("<html></html>")
    site = _site({p: many for p in ("/", *DISCOVERY["paths"])})
    _find(site, discovery={**DISCOVERY, "max_requests": 3})
    assert many.count == 3


def test_the_same_feed_at_two_addresses_counts_once_and_comment_feeds_dont_count():
    comments = feed({"title": "A reply", "url": "https://x.com/c"}, title="Comments on: An old post")
    site = _site({"/": '<html><head><link rel="alternate" type="application/rss+xml" href="/story/feed/"></head></html>', "/story/feed/": comments, "/feed/": GOOD, "/rss/": GOOD})
    feeds, _ = _find(site)
    assert [f["url"].split(site.domain)[1] for f in feeds] == ["/feed/"]


def test_job_ads_in_a_feed_arent_articles():
    hits = Hits(page_html())
    site = _site({"/careers-listing/senior-cloud-engineer/": hits, "/jobs/1/": hits, "/blog/job-readiness/": page_html()})
    for path in ("/careers-listing/senior-cloud-engineer/", "/jobs/1/"):
        article, _ = _read({"url": f"http://{site.domain}{path}", "title": "Senior Cloud Engineer", "summary": "s", "body_html": "", "published_at": None})
        assert article is None
    assert hits.count == 0
    article, _ = _read({"url": f"http://{site.domain}/blog/job-readiness/", "title": "Cloud skills for job readiness", "summary": "s", "body_html": "", "published_at": None})
    assert article is not None


def test_a_feed_added_to_the_settings_doesnt_wait_for_the_monthly_check(pg):
    store = ArticleStore()
    base = {"countries": ["US"], "indexes": ["S&P 100"], "website": None, "ticker": None, "sector": None, "market_cap": None, "cap_currency": None, "cap_date": None, "source": "wikipedia"}
    store.save_companies([{**base, "id": "Q1", "name": "Netflix, Inc."}, {**base, "id": "Q2", "name": "Some Bank"}])
    store.save_feeds("Q1", [], "no feed found")
    store.save_feeds("Q2", [], "no feed found")
    assert store.companies_needing_feeds(30) == []
    assert [c["name"] for c in store.companies_missing_known_feeds(lambda c: _known_feeds(SETTINGS, c))] == ["Netflix, Inc."]
    store.close()


def test_a_known_feed_is_checked_and_a_dead_one_doesnt_count():
    site = _site({"/known": GOOD, "/": "<html></html>"})
    feeds, _ = _find(site, known=[f"http://{site.domain}/known", f"http://{site.domain}/dead"])
    assert [f["url"].split(site.domain)[1] for f in feeds] == ["/known"] and feeds[0]["found_how"] == "known"


def test_known_feeds_match_by_company_name():
    assert _known_feeds(SETTINGS, {"name": "Alphabet Inc."})[0] == "https://blog.google/rss/"
    assert _known_feeds(SETTINGS, {"name": "OpenAI"}) == ["https://openai.com/news/rss.xml"]
    assert _known_feeds(SETTINGS, {"name": "Some Bank"}) == []
    assert _known_feeds(SETTINGS, {"name": "Metro Inc."}) == [] and _known_feeds(SETTINGS, {"name": "Meta Platforms"}) == ["https://engineering.fb.com/feed/"]


def test_settings_are_honest():
    agent = SETTINGS["fetch"]["user_agent"]
    assert "bot" in agent and "github.com" in agent and "Mozilla" not in agent and "@" not in agent    # no one's email, and no browser's name
    assert sorted(i["country"] for i in SETTINGS["indexes"]) == ["AU", "CA", "GB", "IN", "US"]
    assert set(SETTINGS["tags"]) >= {"cloud", "email", "web_services", "ai"}


# ── end to end ───────────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def pg(pg_env):
    from warehouse import postgres
    postgres.setup()
    return pg_env


def _world(site_domain):
    base = f"http://{site_domain}"
    return feed({"title": "Our cloud platform grows", "url": f"{base}/a", "summary": "Short", "date": "Fri, 06 Nov 2026 10:00:00 GMT"},
                {"title": "New email tools", "url": f"{base}/b", "summary": "Short", "body": f"<p>{LONG}</p>", "date": "Thu, 05 Nov 2026 10:00:00 GMT"},
                {"title": "Charity football day", "url": f"{base}/c", "summary": "Fun", "date": "Wed, 04 Nov 2026 10:00:00 GMT"},
                {"title": "AI in a private place", "url": f"{base}/private/d", "summary": "Kept with no text", "date": "Tue, 03 Nov 2026 10:00:00 GMT"})


def test_end_to_end_saves_dates_tags_and_runs_once(pg):
    pages = {"/": '<html><head><link rel="alternate" type="application/rss+xml" href="/feed/"></head></html>', "/a": page_html(date="2020-01-01T00:00:00Z"), "/c": Hits(page_html())}
    site = _site(pages)
    pages["/feed/"] = None
    site.pages["/feed/"] = _world(site.domain)
    settings = {**SETTINGS, "fetch": FETCH, "discovery": DISCOVERY}
    store = ArticleStore()
    store.save_companies([{"id": "Q1", "name": "Acme", "countries": ["US"], "indexes": ["S&P 100"], "website": f"http://{site.domain}/", "ticker": "ACM", "sector": "Tech", "market_cap": None, "cap_currency": None, "cap_date": None, "source": "wikipedia"}])
    assert asyncio.run(_feeds(store, settings, 1))["with_feeds"] == 1
    assert asyncio.run(_feeds(store, settings, 1))["checked"] == 0                  # checked lately: not looked for again
    first = asyncio.run(_collect(store, settings, 1, None))
    assert (first["new"], first["not_about_services"]) == (3, 1) and site.pages["/c"].count == 0
    rows = {r[0].split("/")[-1]: r[1:] for r in store.con.execute("select url, to_char(published_at at time zone 'UTC', 'YYYY-MM-DD'), tags, text_status, date_from from articles.articles").fetchall()}
    assert rows["a"] == ("2026-11-06", ["cloud"], "page", "feed")                    # the feed's own date
    assert rows["b"] == ("2026-11-05", ["cloud", "email"], "feed", "feed")           # the body talks about cloud a lot too
    assert rows["d"][1:3] == (["ai"], "not_allowed")
    assert "c" not in rows
    assert asyncio.run(_collect(store, settings, 1, None))["feeds"] == 0             # read within the last 6 hours: not asked again
    store.con.execute("update articles.feeds set last_checked = now() - interval '7 hours'")
    again = asyncio.run(_collect(store, settings, 1, None))
    assert again["feeds"] == 1 and again["new"] == 0
    # read through the view, with the read-only login
    from warehouse.postgres import connect
    reader = connect("reader", autocommit=True)
    assert reader.execute("select company, countries from articles.v_articles limit 1").fetchone() == ("Acme", ["US"])
    assert reader.execute("select working_feeds, articles from articles.v_companies").fetchone() == (1, 3)
    reader.close()
    store.close()


def test_outlook_means_the_email_app_not_a_forecast():
    assert "email" not in tag_article("Our 2027 market outlook", "", "", COMPILED)
    assert "email" in tag_article("New Microsoft Outlook features", "", "", COMPILED)
    assert "chips" not in tag_article("Agents in Microsoft Foundry", "", "", COMPILED)


def test_retag_applies_new_word_lists_without_losing_articles(pg):
    store = ArticleStore()
    store.save_companies([{"id": "Q1", "name": "Acme", "countries": ["US"], "indexes": ["S&P 100"], "website": None, "ticker": None, "sector": None, "market_cap": None, "cap_currency": None, "cap_date": None, "source": "wikipedia"}])
    feed_row = {"url": "https://a.test/feed", "company_id": "Q1"}
    store.save_feeds("Q1", [{"url": feed_row["url"], "found_how": "known", "title": "t"}], "n")
    article = {"summary": None, "text": None, "text_status": "short", "word_count": 0, "author": None, "published_at": None, "date_from": "none"}
    store.save_feed_result(feed_row, {"status": "ok", "error": None, "items": 2, "skipped": 0, "new": [{**article, "url": "https://a.test/1", "title": "Our market outlook", "tags": ["email"]},
                                                                                                      {**article, "url": "https://a.test/2", "title": "A cloud story", "tags": ["cloud"]}]}, 14)
    assert store.retag(COMPILED, 3) == {"articles": 2, "changed": 1, "no_tag_now": 1}
    assert dict(store.con.execute("select title, tags from articles.articles").fetchall()) == {"Our market outlook": [], "A cloud story": ["cloud"]}
    assert store.retag(COMPILED, 3)["changed"] == 0
    store.close()


def test_a_refused_feed_waits_two_weeks(pg):
    store = ArticleStore()
    store.save_companies([{"id": "Q1", "name": "Acme", "countries": ["US"], "indexes": ["S&P 100"], "website": None, "ticker": None, "sector": None, "market_cap": None, "cap_currency": None, "cap_date": None, "source": "wikipedia"}])
    store.save_feeds("Q1", [{"url": "https://acme.test/feed", "found_how": "known", "title": "t"}], "note")
    feed_row = store.feeds_due(6)[0]
    store.save_feed_result(feed_row, {"status": "refused", "error": "HTTP 429", "items": 0, "new": [], "skipped": 0}, 14)
    store.con.execute("update articles.feeds set last_checked = now() - interval '7 hours'")
    assert store.feeds_due(6) == []
    store.con.execute("update articles.feeds set refused_until = now() - interval '1 hour'")
    assert len(store.feeds_due(6)) == 1
    assert store.feeds_due(6, "GB") == []
    store.close()


def test_collect_stops_when_the_disk_is_nearly_full(pg, monkeypatch):
    import shutil
    from collections import namedtuple
    store = ArticleStore()
    monkeypatch.setattr(shutil, "disk_usage", lambda p: namedtuple("U", "total used free")(1, 1, int(0.1e9)))
    assert asyncio.run(_collect(store, {**SETTINGS, "fetch": FETCH}, 1, None))["note"] == "disk nearly full"
    store.close()
