"""The Articles page: what it shows from what's saved, its filters, who may see it, and that scraped text can't do anything on the page."""

import pytest
from django.contrib.auth.models import Group, User
from django.urls import reverse
from urllib.parse import quote

from articles.store import ArticleStore
from webapp.catalog.models import Dataset, DatasetRight

pytestmark = pytest.mark.django_db


def make_user(name, *roles):
    u = User.objects.create_user(name, password="correct-horse-battery-9")
    u.groups.set(Group.objects.filter(name__in=roles))
    return u


def company(id_, name, countries=("US",), **extra):
    return {"id": id_, "name": name, "countries": list(countries), "indexes": ["S&P 100"], "website": f"https://{id_.lower()}.test/", "ticker": "T", "sector": "Tech", "market_cap": None, "cap_currency": None, "cap_date": None, "source": "wikipedia", **extra}


def article(url, title, tags, when="2026-11-05 10:00:00+00", text="First paragraph.\nSecond paragraph.", status="page", summary="A summary", **extra):
    return {"url": url, "title": title, "summary": summary, "text": text, "text_status": status, "word_count": len((text or "").split()), "author": None, "published_at": when, "date_from": "feed", "tags": tags, **extra}


@pytest.fixture
def saved(pg_env):
    from warehouse import postgres
    postgres.setup()
    store = ArticleStore()
    store.save_companies([company("Q1", "Microsoft"), company("Q2", "Rio Tinto", ("GB", "AU")), company("Q3", "Quiet Bank", ("IN",))])
    store.save_feeds("Q1", [{"url": "https://q1.test/feed", "found_how": "known", "title": "t"}], "found from settings")
    store.save_feeds("Q2", [{"url": "https://q2.test/feed", "found_how": "guess", "title": "t"}], "1 feeds found")
    store.save_feeds("Q3", [], "no feed found in 14 tries; q3.test said HTTP 403")
    store.save_feed_result({"url": "https://q1.test/feed", "company_id": "Q1"}, {"status": "ok", "error": None, "items": 3, "skipped": 0, "new": [
        article("https://q1.test/cloud", "Azure cloud news", ["cloud"]), article("https://q1.test/ai?a=1&b=2", "Copilot and AI", ["ai", "cloud"], when="2026-11-06 10:00:00+00"),
        article("https://q1.test/old", "An old email story", ["email"], when="2020-01-01 10:00:00+00", text="Only here is the word zebra."),
        article("https://q1.test/nodate", "A cloud story with no date", ["cloud"], when=None, status="short", text=None)]}, 14)
    store.save_feed_result({"url": "https://q2.test/feed", "company_id": "Q2"}, {"status": "ok", "error": None, "items": 1, "skipped": 0, "new": [
        article("https://q2.test/mine", "Mining with AI", ["ai"], status="not_allowed", text=None)]}, 14)
    store.con.execute("update articles.feeds set last_ok = now()")
    yield store
    store.close()


@pytest.fixture
def admin(client, saved):
    client.force_login(make_user("ada", "Administrator"))
    return client


def text_of(res) -> str:
    assert res.status_code == 200, res.content[:300]
    return res.content.decode()


def test_menu_goes_to_whoever_has_datasets():
    from webapp.accounts.models import Menu
    assert set(Menu.objects.get(code="articles").roles.values_list("name", flat=True)) == set(Menu.objects.get(code="datasets").roles.values_list("name", flat=True))


def test_the_page_follows_the_rights_of_the_dataset(client, saved):
    client.force_login(make_user("vera", "Viewer"))
    assert client.get(reverse("articles")).status_code == 403                                   # no right to the dataset yet
    assert client.get(reverse("articles_rows")).status_code == 403
    assert client.get(reverse("articles_read"), {"url": "https://q1.test/cloud"}).status_code == 403
    assert client.get(reverse("articles_companies")).status_code == 403
    dataset = Dataset.objects.create(schema="articles", name="v_articles", kind="view", label="Tech articles")
    right = DatasetRight.objects.create(dataset=dataset, group=Group.objects.get(name="Viewer"), can_view=True)
    assert "Azure cloud news" in text_of(client.get(reverse("articles")))
    right.delete()
    assert client.get(reverse("articles")).status_code == 403


def test_someone_without_the_menu_cant_open_it(client, saved):
    from webapp.accounts.models import Menu
    Menu.objects.get(code="articles").roles.remove(Group.objects.get(name="Viewer"))
    client.force_login(make_user("vera", "Viewer"))
    assert client.get(reverse("articles")).status_code == 403
    assert "/articles/" not in text_of(client.get(reverse("home")))


def test_page_shows_counts_newest_first_and_the_honest_gap(admin):
    html = text_of(admin.get(reverse("articles")))
    assert "5" in html and "2 of 3" in html and "Companies with articles" in html
    assert html.index("Copilot and AI") < html.index("Azure cloud news") < html.index("An old email story") < html.index("A cloud story with no date")
    assert "no date" in html
    assert "What we can reach" in html and "isn&#x27;t the whole picture" in html or "isn't the whole picture" in html
    assert "never republished" in html


def test_filters(admin):
    rows = reverse("articles_rows")
    assert "Copilot and AI" in text_of(admin.get(rows, {"tag": "ai"})) and "Azure cloud news" not in text_of(admin.get(rows, {"tag": "ai"}))
    assert "Mining with AI" in text_of(admin.get(rows, {"country": "AU"})) and "Copilot" not in text_of(admin.get(rows, {"country": "AU"}))
    assert "Mining with AI" not in text_of(admin.get(rows, {"company": "Q1"}))
    assert "An old email story" in text_of(admin.get(rows, {"q": "zebra"}))                     # in the article's text, not its title
    assert "Azure" not in text_of(admin.get(rows, {"q": "zebra"}))
    recent = text_of(admin.get(rows, {"days": "365"}))
    assert "An old email story" not in recent and "A cloud story with no date" not in recent
    assert "No articles match" in text_of(admin.get(rows, {"q": "100%_nothing"}))
    res = admin.get(rows, {"tag": "ai", "country": "US"})
    assert '"listCount"' in res["HX-Trigger"] and "tag=ai" in res["HX-Push-Url"] and res["HX-Push-Url"].startswith("/articles/")
    # odd values just don't filter
    assert text_of(admin.get(rows, {"days": "abc", "country": "XX"}))


def test_load_more(admin, monkeypatch):
    import webapp.articles.views as v
    monkeypatch.setattr(v, "PAGE", 2)
    first = text_of(admin.get(reverse("articles_rows")))
    assert first.count('class="news-item"') == 2 and "Load more" in first and "offset=2" in first
    last = text_of(admin.get(reverse("articles_rows"), {"offset": 4}))
    assert last.count('class="news-item"') == 1 and "Load more" not in last


def test_reading_an_article(admin):
    html = text_of(admin.get(reverse("articles_read"), {"url": "https://q1.test/ai?a=1&b=2"}))
    assert "Copilot and AI" in html and "<p>First paragraph.</p>" in html and "<p>Second paragraph.</p>" in html
    assert "AI" in html and "Cloud" in html and "Read the original" in html and 'href="https://q1.test/ai?a=1&amp;b=2"' in html
    assert 'target="_blank"' in html and "noopener" in html
    assert "More from Microsoft" in html and "Azure cloud news" in html and "United States" in html
    assert "Full text, from the page" in html
    assert 'class="ar-nav' in html and "All articles from Microsoft" in html and "?company=Q1" in html          # a page of its own
    assert 'hx-target="#drawer-body"' not in text_of(admin.get(reverse("articles_rows")))
    # a row's link survives its own query string
    assert quote("https://q1.test/ai?a=1&b=2", safe="") in text_of(admin.get(reverse("articles_rows")))
    assert admin.get(reverse("articles_read"), {"url": "https://nowhere.test/"}).status_code == 404


def test_article_text_gets_its_subheadings():
    from webapp.articles.views import article_blocks
    got = article_blocks("Oscar brings AI to health insurance.\nThe industry is complex, they said.\n"
                         "Doubling productivity with automated documentation\nOscar has used AI to speed things up.\nThe end")
    assert [b["kind"] for b in got] == ["p", "p", "h", "p", "p"]          # a short last line isn't a heading: nothing follows it


def test_an_article_without_text_says_why_and_shows_its_summary(admin):
    html = text_of(admin.get(reverse("articles_read"), {"url": "https://q2.test/mine"}))
    assert "robots.txt doesn" in html and "A summary" in html and "Rio Tinto" in html
    assert "no date given" in text_of(admin.get(reverse("articles_read"), {"url": "https://q1.test/nodate"}))


def test_collected_text_cant_do_anything_on_the_page(admin, saved):
    nasty = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    saved.save_feed_result({"url": "https://q1.test/feed", "company_id": "Q1"}, {"status": "ok", "error": None, "items": 1, "skipped": 0, "new": [
        article("javascript:alert(3)", f"T {nasty}", ["ai"], text=f"Body {nasty}", summary=f"S {nasty}")]}, 14)
    for html in (text_of(admin.get(reverse("articles"))), text_of(admin.get(reverse("articles_read"), {"url": "javascript:alert(3)"}))):
        assert "<script>alert" not in html and "<img src=x" not in html
        assert 'href="javascript:' not in html


def test_companies_tab_shows_who_we_can_and_cant_reach(admin):
    html = text_of(admin.get(reverse("articles_companies")))
    assert html.index("Microsoft") < html.index("Quiet Bank") and "q3.test said HTTP 403" in html
    assert "/articles/?company=Q1" in html
    assert ">q1.test<" in html and 'href="https://q1.test/"' in html
    rows = reverse("articles_company_rows")
    assert "Quiet Bank" in text_of(admin.get(rows, {"status": "none"})) and "Microsoft" not in text_of(admin.get(rows, {"status": "none"}))
    assert "Microsoft" in text_of(admin.get(rows, {"status": "articles"})) and "Quiet Bank" not in text_of(admin.get(rows, {"status": "articles"}))
    assert "Rio Tinto" in text_of(admin.get(rows, {"country": "AU"})) and "Microsoft" not in text_of(admin.get(rows, {"country": "AU"}))
    assert "Microsoft" in text_of(admin.get(rows, {"q": "micro"}))


def test_before_anything_is_saved(client, pg_env):
    from warehouse import postgres
    postgres.setup()
    client.force_login(make_user("ada", "Administrator"))
    assert "No articles yet" in text_of(client.get(reverse("articles")))
    assert "No companies yet" in text_of(client.get(reverse("articles_companies")))


def test_reading_changes_nothing(admin, saved):
    before = saved.con.execute("select (select count(*) from articles.articles), (select count(*) from articles.runs)").fetchone()
    admin.get(reverse("articles"))
    admin.get(reverse("articles_read"), {"url": "https://q1.test/cloud"})
    assert saved.con.execute("select (select count(*) from articles.articles), (select count(*) from articles.runs)").fetchone() == before
