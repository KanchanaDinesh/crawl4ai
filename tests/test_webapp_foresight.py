"""The Foresight page: what it shows from what's saved, who may see it, and that it never changes anything."""

import json
from datetime import date, timedelta

import pytest
from django.contrib.auth.models import Group, User
from django.urls import reverse

from webapp.catalog.models import Dataset, DatasetRight
from webapp.catalog.scan import scan

pytestmark = pytest.mark.django_db
TODAY = date(2026, 11, 6)


def make_user(name, *roles):
    u = User.objects.create_user(name, password="correct-horse-battery-9")
    u.groups.set(Group.objects.filter(name__in=roles))
    return u


def _market_row(symbol="BTC", grp="crypto", zone="oversold", as_of=TODAY, up=0.40, flat=0.40, down=0.20):
    return {"section": "markets", "grp": grp, "subject": f"{grp}:{symbol}", "subject_name": symbol.title(), "question": "price_7d", "as_of": as_of, "check_on": as_of + timedelta(days=7),
            "start_value": 100.0, "scenarios": [{"key": "up", "label": "Up more than 3.0%", "probability": up, "normal": 0.25}, {"key": "flat", "label": "Within 3.0%", "probability": flat, "normal": 0.5},
                                                {"key": "down", "label": "Down more than 3.0%", "probability": down, "normal": 0.25}],
            "reasons": ["%K is 12.0: oversold.", "From 500 past days of coins."], "signals": {"pct_k": 12.0, "zone": zone, "band_pct": 3.0}, "method": "stochrsi_zone", "method_version": 1, "cases": 500, "separate_weeks": 20}


def _story_row(as_of=TODAY):
    return {"section": "news", "grp": "stories", "subject": "story:abc", "subject_name": "Volcano erupts in Iceland", "question": "story_7d", "as_of": as_of, "check_on": as_of + timedelta(days=7), "start_value": 8,
            "scenarios": [{"key": "bigger", "label": "Bigger: 8 or more sources", "probability": 0.1, "normal": 0.1}, {"key": "going", "label": "Still going: 2 to 7 sources", "probability": 0.3, "normal": 0.3},
                          {"key": "faded", "label": "Faded: under 2 sources", "probability": 0.6, "normal": 0.6}],
            "reasons": ["Told by 8 sources."], "signals": {"key_words": ["volcano", "iceland", "flights"], "sources": 8, "countries": 3, "zone": "8+ sources"}, "method": "story_size", "method_version": 1, "cases": 80, "separate_weeks": 4}


def _test_row(grp="crypto", verdict="no_difference"):
    return {"section": "markets", "grp": grp, "question": "price_7d", "method": "stochrsi_zone", "method_version": 1, "first_day": date(2026, 2, 1), "last_day": date(2026, 10, 30), "cases": 16165, "weeks": 39, "score": 0.6, "normal_score": 0.6,
            "skill": -0.006, "skill_low": -0.011, "skill_high": 0.001, "verdict": verdict,
            "details": {"zones": {z: {"cases": 100, "up": 0.2, "flat": 0.5, "down": 0.3} for z in ("oversold", "middle", "overbought", "all")}, "labels": {"up": "up", "flat": "within", "down": "down"}, "note": "76 instruments"}}


@pytest.fixture
def saved(pg_env):
    from foresight.store import ForesightStore
    from warehouse import postgres
    postgres.setup()
    store = ForesightStore()
    store.save_forecasts([_market_row(), _market_row("ETH", zone="overbought", up=0.25, flat=0.5, down=0.25), _market_row("JKH.N0000", "stock_lk", "middle", up=0.3, flat=0.5, down=0.2), _story_row()])
    store.save_backtest(_test_row())
    store.save_run("markets", 3, 0, {"hardly moves": 24})
    store.save_run("news", 1, 0, {}, None, ["Watching 40 stories on 2026-11-05.", "Stories in 7 days: no odds yet, 0 cases settled."])
    store.save_signals("news", [("story", TODAY, "abc", {"key_words": ["volcano", "iceland"], "headline": "Volcano erupts in Iceland", "sources": 8, "countries": 3, "groups": ["US"]}),
                                ("topic", TODAY, "politics", {"count": 40, "total": 100})])
    yield store
    store.close()


@pytest.fixture
def admin(client, saved):
    client.force_login(make_user("ada", "Administrator"))
    return client


def text_of(res) -> str:
    assert res.status_code == 200, res.content[:300]
    return res.content.decode()


def test_only_administrators_get_the_menu_at_first(client, saved):
    client.force_login(make_user("vera", "Viewer"))
    assert client.get(reverse("foresight")).status_code == 403
    assert client.get(reverse("foresight_markets")).status_code == 403
    assert client.get(reverse("foresight_forecast", args=[1])).status_code == 403
    assert "Foresight" not in text_of(client.get(reverse("home")))


def test_a_role_given_the_menu_still_needs_the_data_behind_each_tab(client, saved):
    from webapp.accounts.models import Menu
    Menu.objects.get(code="foresight").roles.add(Group.objects.get(name="Viewer"))
    scan()
    DatasetRight.objects.filter(group__name="Viewer", dataset__schema="markets").delete()
    DatasetRight.objects.filter(group__name="Viewer", dataset__schema="news").delete()
    Dataset.objects.update_or_create(schema="news", name="news_items", defaults=dict(kind="table", label="Headlines"))
    DatasetRight.objects.update_or_create(dataset=Dataset.objects.get(schema="news", name="news_items"), group=Group.objects.get(name="Viewer"), defaults=dict(can_view=True))
    client.force_login(make_user("vera", "Viewer"))
    assert client.get(reverse("foresight_markets")).status_code == 403
    assert client.get(reverse("foresight_rows", args=["markets"])).status_code == 403
    html = text_of(client.get(reverse("foresight_news")))
    assert "Volcano erupts in Iceland" in html and reverse("foresight_markets") not in html
    fid = saved.con.execute("select id from foresight.forecasts where subject = 'crypto:BTC'").fetchone()[0]
    assert client.get(reverse("foresight_forecast", args=[fid])).status_code == 403    # a direct address can't get around it
    assert client.get(reverse("foresight")).status_code == 302


def test_markets_tab_shows_odds_and_says_when_a_method_has_no_edge(admin):
    html = text_of(admin.get(reverse("foresight_markets")))
    assert "Bitcoin" not in html and "Btc" in html and "Jkh.N0000" in html
    assert "hasn&#x27;t shown any edge" in html or "hasn't shown any edge" in html
    assert "No forecast has reached its date yet" in html and "2026-11-13" in html
    assert "not advice" in html.lower() and "powered by CoinPaprika" in html
    assert "buy" not in html.lower().replace("overbought", "") and " sell" not in html.lower()
    assert "oversold" in html and "Usual move" in html
    assert "No clear difference from the simple guess" in html
    # saved as JSONB, which sorts keys: the page must still read oversold, middle, overbought, all; up, within, down
    table = html[html.index("Test on the past"):]
    assert table.index(">up<") < table.index(">within<") < table.index(">down<")
    assert table.index(">oversold<") < table.index(">middle<") < table.index(">overbought<") < table.index(">all<")


def test_no_warning_if_the_method_really_beats_the_guess(admin, saved):
    saved.save_backtest({**_test_row(), "verdict": "better", "skill": 0.05, "skill_low": 0.01, "skill_high": 0.09})
    html = text_of(admin.get(reverse("foresight_markets")))
    assert "shown any edge" not in html and "Better than the simple guess" in html


def test_filters_and_sorting(admin):
    rows = reverse("foresight_rows", args=["markets"])
    res = admin.get(rows, {"grp": "stock_lk"})
    assert "Jkh.N0000" in text_of(res) and "Btc" not in text_of(res)
    assert '"listCount"' in res["HX-Trigger"] and "/foresight/markets/" in res["HX-Push-Url"]
    assert "Btc" in text_of(admin.get(rows, {"zone": "oversold"})) and "Eth" not in text_of(admin.get(rows, {"zone": "oversold"}))
    assert "Eth" in text_of(admin.get(rows, {"q": "eth"})) and "Btc" not in text_of(admin.get(rows, {"q": "eth"}))
    assert "No open forecasts match" in text_of(admin.get(rows, {"q": "nothing like this"}))
    down = text_of(admin.get(rows, {"sort": "p_up", "dir": "-1"}))
    assert down.index("Btc") < down.index("Jkh.N0000") < down.index("Eth")
    up = text_of(admin.get(rows, {"sort": "p_up", "dir": "1"}))
    assert up.index("Eth") < up.index("Btc")
    assert text_of(admin.get(rows, {"sort": "'; drop table x; --"}))                    # an unknown column just sorts the normal way


def test_side_panel_gives_reasons_and_the_outcome_once_checked(admin, saved):
    fid = saved.con.execute("select id from foresight.forecasts where subject = 'crypto:BTC'").fetchone()[0]
    html = text_of(admin.get(reverse("foresight_forecast", args=[fid])))
    assert "Up more than 3.0%" in html and "40%" in html and "25%" in html
    assert "%K is 12.0: oversold." in html and "once the data reaches that day" in html
    saved.save_outcomes([{"forecast_id": fid, "end_value": 110, "end_day": TODAY + timedelta(days=7), "move_pct": 10.0, "actual": "up", "score": 0.5, "normal_score": 0.6}])
    html = text_of(admin.get(reverse("foresight_forecast", args=[fid])))
    assert "Up" in html and "10.0%" in html and "0.500" in html
    page = text_of(admin.get(reverse("foresight_markets")))
    assert "Recently checked" in page and "1 checked" in page and "better than the simple guess" in page
    assert admin.get(reverse("foresight_forecast", args=[999999])).status_code == 404


def test_a_forecast_that_couldnt_be_checked_says_so(admin, saved):
    fid = saved.con.execute("select id from foresight.forecasts where subject = 'story:abc'").fetchone()[0]
    saved.save_outcomes([{"forecast_id": fid, "end_value": None, "end_day": TODAY + timedelta(days=7), "move_pct": None, "actual": "no_data", "score": None, "normal_score": None}])
    assert "Couldn't be checked" in text_of(admin.get(reverse("foresight_forecast", args=[fid])))
    page = text_of(admin.get(reverse("foresight_news")))
    assert "1 couldn't be checked" in page and "Recently checked" in page


def test_not_ready_goes_away_once_odds_are_ready(admin, saved):
    saved.save_run("news", 0, 0, {}, None, ["Watching 40 stories on 2026-11-20.", "Stories in 7 days: ready, 500 cases settled."])
    assert "Not ready yet" not in text_of(admin.get(reverse("foresight_news")))


def test_news_tab_shows_progress_stories_and_topics(admin):
    html = text_of(admin.get(reverse("foresight_news")))
    assert "Not ready yet" in html and "no odds yet" in html and "Watching 40 stories" in html
    assert "Stories being watched" in html and "volcano, iceland" in html and "Topics now" in html and "40.0%" in html
    assert "Faded" in html and "Volcano erupts in Iceland" in html
    assert "shown any edge" not in html


def test_tabs_without_an_analyst_say_why(admin):
    assert "a directory" in text_of(admin.get(reverse("foresight_companies")))
    assert "more weeks of ads" in text_of(admin.get(reverse("foresight_vehicles")))
    assert "more weeks of saved sites" in text_of(admin.get(reverse("foresight_tech")))
    assert admin.get(reverse("foresight_rows", args=["nope"])).status_code == 404


def test_page_before_anything_is_saved(client, pg_env):
    from warehouse import postgres
    postgres.setup()
    client.force_login(make_user("ada", "Administrator"))
    html = text_of(client.get(reverse("foresight_markets")))                            # the schema exists, with no tables yet
    assert "No forecasts yet" in html and "python -m foresight run" in html


def test_reading_the_page_changes_nothing(admin, saved):
    before = saved.con.execute("select (select count(*) from foresight.forecasts), (select count(*) from foresight.outcomes), (select count(*) from foresight.runs)").fetchone()
    admin.get(reverse("foresight_markets"))
    admin.get(reverse("foresight_news"))
    assert saved.con.execute("select (select count(*) from foresight.forecasts), (select count(*) from foresight.outcomes), (select count(*) from foresight.runs)").fetchone() == before


@pytest.fixture
def scanned(client):
    """The catalog scan and the grid read through the web app's own connection, so the tables go in its test database here."""
    from django.db import connection
    from warehouse import postgres
    connection.ensure_connection()
    with connection.cursor() as cur:
        cur.execute("create schema if not exists foresight; create schema if not exists markets")
    postgres.migrate(connection.connection, ("foresight", "markets"))
    with connection.cursor() as cur:
        cur.execute("""insert into foresight.forecasts (section, grp, subject, subject_name, question, as_of, check_on, start_value, scenarios, reasons, signals, method, method_version, cases, separate_weeks)
            values ('markets', 'crypto', 'crypto:BTC', 'Bitcoin', 'price_7d', '2026-11-06', '2026-11-13', 100, %s, %s, %s, 'stochrsi_zone', 1, 500, 20)""",
                    [json.dumps(_market_row()["scenarios"]), json.dumps(_market_row()["reasons"]), json.dumps(_market_row()["signals"])])
    scan()
    return client


def test_catalog_keeps_forecasts_with_administrators_and_hides_the_raw_tables(scanned):
    forecasts = Dataset.objects.get(schema="foresight", name="v_forecasts")
    assert forecasts.enabled and forecasts.label == "Forecasts" and not DatasetRight.objects.filter(dataset=forecasts).exists()
    assert not DatasetRight.objects.filter(dataset__schema="foresight").exists()
    for name in ("forecasts", "outcomes", "signals", "runs"):
        assert not Dataset.objects.get(schema="foresight", name=name).enabled
    assert DatasetRight.objects.filter(dataset__schema="markets", dataset__name="v_latest", group__name="Viewer").exists()    # others keep their starting rights


def test_forecasts_in_the_grid_for_administrators_only(scanned):
    ref = "foresight.v_forecasts"
    scanned.force_login(make_user("ada", "Administrator"))
    html = scanned.get(reverse("dataset_rows", args=[ref]))
    assert html.status_code == 200 and "Up more than 3.0%: 40% (normal 25%)" in html.content.decode() and "Bitcoin" in html.content.decode()
    scanned.force_login(make_user("vera", "Viewer"))
    assert scanned.get(reverse("dataset_rows", args=[ref])).status_code in (403, 404)


def test_flat_view_reads_in_plain_words(scanned):
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute("select odds, reasons, zone, status, as_of::text from foresight.v_forecasts where subject = 'crypto:BTC'")
        row = cur.fetchone()
    assert row[0] == "Up more than 3.0%: 40% (normal 25%); Within 3.0%: 40% (normal 50%); Down more than 3.0%: 20% (normal 25%)"
    assert row[1] == "%K is 12.0: oversold. From 500 past days of coins." and row[2:] == ("oversold", "open", TODAY.isoformat())


def test_a_report_can_have_a_forecasts_section(scanned):
    from webapp.reports import making
    from webapp.reports.models import Report
    ada = make_user("ada", "Administrator")
    scanned.force_login(ada)
    r = scanned.post(reverse("report_new_newsletter"), {"start": "dataset:foresight.v_forecasts", "schedule": ""}, HTTP_HOST="localhost")
    assert r.status_code == 302, r.content[:300]
    report = Report.objects.get(pk=int(r.url.rstrip("/").split("/")[-1]))
    html = making.render_html(report, ada)
    assert "Bitcoin" in html and "Up more than 3.0%: 40% (normal 25%)" in html      # the name and the odds, without choosing anything
