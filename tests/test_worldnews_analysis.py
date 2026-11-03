"""News analysis: stories across sources, topics, names, countries, rising words, tone, and the report files."""

import json
from datetime import datetime, timedelta, timezone

from worldnews import analysis
from worldnews.settings import load_settings

NOW = datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc)


def item(title, source, group, summary="", hours_ago=1):
    when = NOW - timedelta(hours=hours_ago)
    return {"url": f"https://{source.lower().replace(' ', '')}.example/{abs(hash(title))}", "title": title,
            "summary": summary, "source": source, "group_code": group, "published_at": when, "first_seen_at": when}


ITEMS = [
    item("Learjet breaks up before deadly Florida jet crash, NTSB says", "NBC News", "US",
         "Investigators said the Learjet lost parts in the air. Donald Trump called the families."),
    item("Florida jet crash: Learjet broke up in mid-air, investigators say", "BBC World", "GLOBAL"),
    item("NTSB: Learjet in Florida crash broke up before impact", "CBC News", "CA"),
    item("Midterm elections: what voters need to know", "CNN", "US", "Voters in Michigan go to the polls. Trump spoke."),
    item("Sri Lanka cricket team wins the T20 series against India", "Daily Mirror", "LK",
         "The Sri Lanka Cricket board praised the captain."),
    item("Colombo stock market rises to a record high", "EconomyNext", "LK", "The Central Bank of Sri Lanka kept rates."),
    item("Police arrest suspect after shooting in Colombo", "Ada Derana", "LK", "Police said the suspect was arrested."),
    item("Binance hack: North Korea denies role in record theft", "Decrypt", "CRYPTO",
         "Crypto exchange Binance said withdrawals reopened after the hack."),
    item("Binance hack loot laundered by North Korea, analysts say", "The Block", "CRYPTO",
         "Analysts traced funds taken from Binance."),
    item("A Right-Wing Think Tank Has A Plan For Congress", "Wired", "TECH"),
]


def config():
    return load_settings().section("analysis")


def test_stories_group_the_same_event_from_different_sources():
    found = analysis.stories(ITEMS)
    crash = next(s for s in found if "florida" in s["key_words"])
    assert set(crash["sources"]) == {"NBC News", "BBC World", "CBC News"} and len(crash["groups"]) == 3
    binance = next(s for s in found if "binance" in s["key_words"])
    assert set(binance["sources"]) == {"Decrypt", "The Block"}
    assert not any("Midterm elections: what voters need to know" in [h["title"] for h in s["headlines"]] for s in found)


def test_names_countries_and_title_case():
    assert "Donald Trump" in analysis.names_in("Investigators said the Learjet lost parts. Donald Trump called.")
    assert analysis.names_in("The People's Bank of China cut rates.") == ["People's Bank of China"]
    assert analysis._title_case("A Right-Wing Think Tank Has A Plan For Congress")
    names = {n["name"]: n["headlines"] for n in analysis.top_names(ITEMS)}
    assert names.get("Binance") == 2 and names.get("Florida") == 3     # found mid-sentence, counted in every headline
    assert "Right-Wing Think Tank" not in names and "NBC News" not in names and "Police" not in names
    countries = {c["country"]: c for c in analysis.countries(ITEMS)}
    assert countries["Sri Lanka"]["headlines"] == 3 and countries["Sri Lanka"]["from_other_countries"] == 0
    assert countries["North Korea"]["headlines"] == 2 and countries["India"]["headlines"] == 1


def test_rising_words_need_an_earlier_period():
    before = [item(f"Quiet day number {n} in parliament", "X", "US", hours_ago=30) for n in range(10)]
    now = [item(f"Binance hack update {n}", "X", "CRYPTO") for n in range(6)] + before[:4]
    words = {r["word"]: r for r in analysis.rising(now, before)}
    assert words["binance"]["now"] == 6 and words["binance"]["before"] == 0 and "parliament" not in words
    assert analysis.rising(now, []) == []


def test_analyze_topics_tone_and_sri_lanka():
    r = analysis.analyze(ITEMS, [], config(), {"US": "United States", "LK": "Sri Lanka"})
    topics = {t["topic"]: t for t in r["topics"]}
    assert topics["sports"]["headlines"] >= 1 and topics["crypto"]["headlines"] == 2 and topics["crime_justice"]["headlines"] >= 1
    assert r["topics"][-1]["topic"] == "other" or "other" not in topics
    lk = r["sri_lanka"]
    assert lk["headlines"] == 3 and lk["from_lk_sources"] == 3 and "colombo" in {w["word"] for w in lk["words"]}
    tone = r["tone"]["all"]
    assert tone["headlines"] == len(ITEMS) and tone["negative"] >= 2 and tone["positive"] >= 2
    assert analysis.balance_label(-40) == "mostly negative" and analysis.balance_label(0) == "mixed"


def test_analyze_command_writes_the_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WORLDNEWS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WORLDNEWS_OUTPUT_DIR", str(tmp_path / "out"))
    from worldnews import cli
    from worldnews.store import NewsStore
    store = NewsStore(tmp_path / "data")
    now = datetime.now(timezone.utc)
    store.append_items([{**i, "group": i["group_code"], "published_at": (now - timedelta(hours=1)).isoformat(),
                         "first_seen_at": (now - timedelta(hours=1)).isoformat(), "topics": []} for i in ITEMS])
    store.close()
    cli.main(["analyze", "--days", "1"])
    out = capsys.readouterr().out
    assert "10 headlines from" in out and "Biggest stories" in out and "Report:" in out
    html = (tmp_path / "out" / "analysis" / "analysis-latest.html").read_text()
    assert "Florida" in html and "Sri Lanka" in html
    data = json.loads(next((tmp_path / "out" / "analysis").glob("analysis-*.json")).read_text())
    assert data["headlines"] == 10 and data["stories"]
