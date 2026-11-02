"""worldnews: offline unit tests + an end-to-end run against fake news sites on localhost."""

import asyncio
import json
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from test_pipeline_e2e import Site   # tiny threaded HTTP server per fake site
from worldnews import extract
from worldnews.collect import run_collection, sort_latest
from worldnews.report import write_report
from worldnews.settings import CONFIG_DIR, load_settings
from worldnews.store import NewsStore
from worldnews.tagging import TechTagger


def rss(items: list[tuple[str, str, datetime]]) -> str:
    entries = "".join(
        f"<item><title>{t}</title><link>{u}</link><description>&lt;p&gt;About {t}&lt;/p&gt;</description>"
        f"<pubDate>{d.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate></item>" for t, u, d in items)
    return f"<?xml version='1.0'?><rss version='2.0'><channel><title>T</title>{entries}</channel></rss>"


NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


# ── extract ───────────────────────────────────────────────────────────────
def test_parse_feed_rss_and_atom():
    items = extract.parse_feed(rss([("Hello world", "https://x.com/a", NOW)]))
    assert items == [{"title": "Hello world", "url": "https://x.com/a", "summary": "About Hello world",
                      "published_at": "2026-11-02T12:00:00Z", "image": None}]
    atom = ("<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><entry><title>Atom item</title>"
            "<link href='/b'/><updated>2026-11-02T10:30:00+05:30</updated><summary>S</summary></entry></feed>")
    a = extract.parse_feed(atom, "https://site.org/")
    assert a[0]["url"] == "https://site.org/b" and a[0]["published_at"] == "2026-11-02T05:00:00Z"


def test_discover_feeds_and_normalize():
    html = ("<head><link rel='alternate' type='application/rss+xml' href='/feed/'>"
            "<link rel='alternate' type='application/atom+xml' href='https://cdn.x.com/atom.xml'>"
            "<link rel='stylesheet' href='/s.css'></head>")
    assert extract.discover_feeds(html, "https://x.com/") == ["https://x.com/feed/", "https://cdn.x.com/atom.xml"]
    assert extract.normalize_url("HTTPS://News.X.com/a/b?utm_source=t&id=5#top") == "https://news.x.com/a/b?id=5"


def test_homepage_headlines_picks_articles_not_navigation():
    cfg = json.loads((CONFIG_DIR / "settings.json").read_text())["homepage"]
    html = """
      <a href='/about'>About us and our long history of reporting</a>
      <a href='/tag/politics'>Politics news and analysis from around the world</a>
      <a href='/2026/11/02/markets-rally-as-inflation-cools'>Markets rally as inflation cools across the region</a>
      <a href='/2026/11/02/markets-rally-as-inflation-cools'><img src='x.jpg'></a>
      <a href='https://other.com/2026/11/02/elsewhere-story-here'>A story on a completely different website today</a>
      <a href='/world/asia/floods-hit-coastal-towns-after-storm-12345678'>Floods hit coastal towns after storm</a>
      <a href='/video'>Watch</a>
      <a href='/news/short'>Too short</a>"""
    got = extract.homepage_headlines(html, "https://www.paper.com/", cfg)
    assert [g["title"] for g in got] == ["Markets rally as inflation cools across the region",
                                         "Floods hit coastal towns after storm"]


def test_selector_headlines():
    html = "<div class='news_block'><p><a href='/breaking/x-1'>Big news here</a></p></div>"
    got = extract.selector_headlines(html, "https://www.dailymirror.lk/", {"base": "div.news_block", "title": "p > a", "link": "p > a"})
    assert got == [{"title": "Big news here", "url": "https://www.dailymirror.lk/breaking/x-1"}]


def test_article_meta_variants():
    m = extract.article_meta("<meta property='article:published_time' content='2026-11-02T08:00:00+05:30'>"
                             "<meta property='og:description' content='Summary here'>")
    assert m["published_at"] == "2026-11-02T02:30:00Z" and m["summary"] == "Summary here"
    ld = '<script type="application/ld+json">{"@graph":[{"@type":"NewsArticle","datePublished":"2026-11-01T10:00:00Z"}]}</script>'
    assert extract.article_meta(ld)["published_at"] == "2026-11-01T10:00:00Z"
    assert extract.article_meta("<time datetime='2026-10-31'>x</time>")["published_at"] == "2026-10-31T00:00:00Z"
    assert extract.parse_date("Sun, 02 Nov 2026 07:00:00 GMT") == "2026-11-02T07:00:00Z"
    assert extract.parse_date("not a date") is None


def test_tech_tagger_and_sorting():
    tagger = TechTagger(["ai", "chip", "openai", "data center"])
    item = {"title": "OpenAI signs chip deal", "summary": "New data center planned"}
    tagger.tag(item, "US")
    assert item["topics"] == ["tech"] and item["tech_terms"] == ["chip", "data center", "openai"]
    plain = {"title": "Rain expected in Colombo", "summary": "Maintain caution"}   # "ai" inside words must not match
    tagger.tag(plain, "LK")
    assert plain["topics"] == []
    items = [{"t": 1, "published_at": None}, {"t": 2, "published_at": "2026-11-01T00:00:00Z"},
             {"t": 3, "published_at": "2026-11-02T00:00:00Z"}, {"t": 4}]
    assert [i["t"] for i in sort_latest(items)] == [3, 2, 1, 4]


# ── end to end ────────────────────────────────────────────────────────────
@pytest.fixture
def news_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDNEWS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WORLDNEWS_OUTPUT_DIR", str(tmp_path / "out"))
    cfg = tmp_path / "config"
    cfg.mkdir()
    shutil.copy(CONFIG_DIR / "settings.json", cfg / "settings.json")
    return cfg


def article_page(title, published, summary):
    return (f"<html><head><title>{title}</title><meta property='article:published_time' content='{published}'>"
            f"<meta property='og:description' content='{summary}'></head><body><h1>{title}</h1></body></html>")


def test_end_to_end(news_env):
    feed_items = [(f"Feed story number {n} about markets", f"/story-{n}", NOW - timedelta(hours=n)) for n in range(15)]
    feedsite = Site({"/": "<html>home</html>"})
    feedsite.pages["/rss.xml"] = rss([(t, f"http://{feedsite.domain}{u}", d) for t, u, d in reversed(feed_items)])

    discsite = Site({"/": "<html><head><link rel='alternate' type='application/rss+xml' href='/feed'></head></html>"})
    discsite.pages["/feed"] = rss([("OpenAI launches new chip for data centers", f"http://{discsite.domain}/ai-chip", NOW)])

    pages = {}
    links = []
    for n in range(12):
        slug = f"/2026/11/02/local-council-approves-new-budget-plan-{n}"
        title = f"Local council approves new budget plan part {n}"
        links.append(f"<a href='{slug}'>{title}</a>")
        pages[slug] = article_page(title, (NOW - timedelta(hours=(n * 7) % 12)).isoformat(), f"Summary {n}")
    homesite = Site({"/": "<html><body><a href='/about'>About this newspaper and its staff</a>" + "".join(links) + "</body></html>",
                     **pages})

    selsite = Site({"/": "<div class='blk'><h4><a href='/a-1'>Selector headline one</a></h4></div>"
                         "<div class='blk'><h4><a href='/a-2'>Selector headline two</a></h4></div>"})
    broken = Site({})   # every page 404
    blocked = Site({"/robots.txt": "User-agent: *\nDisallow: /\n", "/": "<a href='/2026/11/02/a-b-c-d'>Hidden story is here today</a>"})

    sources = [
        {"name": "Feed Site", "group": "US", "homepage": f"http://{feedsite.domain}/", "feeds": [f"http://{feedsite.domain}/rss.xml"]},
        {"name": "Disc Site", "group": "TECH", "homepage": f"http://{discsite.domain}/", "feeds": []},
        {"name": "Home Site", "group": "LK", "homepage": f"http://{homesite.domain}/", "feeds": []},
        {"name": "Selector Site", "group": "LK", "homepage": f"http://{selsite.domain}/", "feeds": [],
         "selectors": {"base": "div.blk", "title": "h4 a", "link": "h4 a"}},
        {"name": "Broken Site", "group": "IN", "homepage": f"http://{broken.domain}/", "feeds": [f"http://{broken.domain}/rss"]},
        {"name": "Blocked Site", "group": "AU", "homepage": f"http://{blocked.domain}/", "feeds": []},
    ]
    (news_env / "sources.json").write_text(json.dumps(sources))
    settings = load_settings(news_env)
    store = NewsStore(settings.data_dir)

    snap = asyncio.run(run_collection(settings, store, settings.load_sources(), limit=10, use_browser=False))
    by = {s["name"]: s for s in snap["sources"]}

    feed = by["Feed Site"]
    assert feed["status"] == "ok" and feed["via"] == "feed" and len(feed["items"]) == 10
    assert [i["title"] for i in feed["items"][:2]] == ["Feed story number 0 about markets", "Feed story number 1 about markets"]

    disc = by["Disc Site"]
    assert disc["via"] == "discovered feed" and "tech" in disc["items"][0]["topics"]
    assert store.feed_cache() == {"Disc Site": f"http://{discsite.domain}/feed"}

    home = by["Home Site"]
    assert home["via"] == "homepage (http)" and len(home["items"]) == 10
    dates = [i["published_at"] for i in home["items"]]
    assert dates == sorted(dates, reverse=True) and all(dates)           # newest first, all dated from article pages
    assert home["items"][0]["summary"].startswith("Summary")
    assert not any("About this newspaper" in i["title"] for i in home["items"])

    assert by["Selector Site"]["via"] == "selectors (http)" and len(by["Selector Site"]["items"]) == 2
    assert by["Broken Site"]["status"] == "failed" and "HTTP 404" in by["Broken Site"]["error"]
    assert by["Blocked Site"]["status"] == "failed" and "robots" in by["Blocked Site"]["error"]
    assert snap["totals"]["new"] == snap["totals"]["headlines"] == 10 + 1 + 10 + 2

    # second run: nothing new, discovered feed reused; then one new story appears
    snap2 = asyncio.run(run_collection(settings, store, settings.load_sources(), limit=10, use_browser=False))
    assert snap2["totals"]["new"] == 0
    assert {s["name"]: s["via"] for s in snap2["sources"]}["Disc Site"] == "feed"
    feedsite.pages["/rss.xml"] = rss([(t, f"http://{feedsite.domain}{u}", d) for t, u, d in feed_items]
                                     + [("Breaking: brand new story", f"http://{feedsite.domain}/new", NOW + timedelta(minutes=5))])
    snap3 = asyncio.run(run_collection(settings, store, settings.load_sources(), limit=10, use_browser=False))
    first = {s["name"]: s for s in snap3["sources"]}["Feed Site"]["items"][0]
    assert first["title"] == "Breaking: brand new story" and first["is_new"] and snap3["totals"]["new"] == 1

    # the database has the history: unique headlines, how often each was seen, per-source health
    con = store.con
    assert con.execute("select count(*) from news_items").fetchone()[0] == 24
    assert con.execute("select times_seen from news_items where title = 'Feed story number 0 about markets'").fetchone()[0] == 3
    assert con.execute("select count(*) from news_runs").fetchone()[0] == 3
    assert con.execute("select count(*) from news_source_runs where status = 'failed'").fetchone()[0] == 6
    assert con.execute("select list_contains(topics, 'tech') from news_items where source = 'Disc Site'").fetchone()[0]
    assert con.execute("select feed_url from feed_cache").fetchone()[0].endswith("/feed")
    store.close()

    paths = write_report(snap3, settings)
    page = open(paths["html"], encoding="utf-8").read()
    assert page.index("id='US'") < page.index("id='LK'") < page.index("id='TECH'")      # priority order
    assert page.count("target='_blank'") == page.count("<a href='http") + page.count("<a class") - page.count("class='chip' href='#")
    assert "Breaking: brand new story" in page and "<span class='new'>NEW</span>" in page
    assert "Sources with no headlines (2)" in page
    assert open(paths["csv"], encoding="utf-8").read().count("\n") == 1 + 23
    for s in (feedsite, discsite, homesite, selsite, broken, blocked):
        s.server.shutdown()


def test_old_jsonl_history_is_imported(news_env):
    settings = load_settings(news_env)
    settings.data_dir.mkdir(parents=True)
    (settings.data_dir / "items.jsonl").write_text(json.dumps(
        {"url": "https://x.com/a", "title": "Old headline", "source": "X", "group": "US", "topics": ["tech"],
         "collected_at": "2026-10-01T00:00:00Z", "published_at": "2026-09-30T23:00:00Z"}) + "\n")
    (settings.data_dir / "feed_cache.json").write_text('{"X": "https://x.com/feed"}')
    store = NewsStore(settings.data_dir)
    assert store.known_urls() == {"https://x.com/a"} and store.feed_cache() == {"X": "https://x.com/feed"}
    assert (settings.data_dir / "items.jsonl.imported").exists() and not (settings.data_dir / "items.jsonl").exists()
    store.close()


def test_topic_tagging_crypto_and_markets():
    from worldnews.tagging import TopicTagger
    settings = json.loads((CONFIG_DIR / "settings.json").read_text())
    tagger = TopicTagger.from_settings(settings)
    item = {"title": "Bitcoin jumps as Fed signals rate cut", "summary": "Stocks rally on Wall Street"}
    tagger.tag(item, "US")
    assert set(item["topics"]) >= {"crypto", "markets"} and "bitcoin" in item["crypto_terms"]
    blog = {"title": "Protocol upgrade notes", "summary": "Testnet schedule"}
    tagger.tag(blog, "CRYPTO")
    assert blog["topics"] == ["crypto"]                                   # group implies the topic
    plain = {"title": "Rain expected in Colombo", "summary": "Heavy showers"}
    tagger.tag(plain, "LK")
    assert plain["topics"] == []
