"""Names in other scripts (Kanchana, 2026-11-13): the Companies tab shows names in English letters only, unless switched on.

Nothing is deleted: PostgreSQL works out which letters each name is written in (companies.name_script), and the English
name a source gives (GLEIF's other names) is shown instead where there is one."""

import asyncio
import json

import pytest

from companies.sources import company
from companies.sources import gleif
from test_companies import Server, lei, settings_for, store  # noqa: F401 - store is a fixture

SCRIPTS = {
    "Nestlé S.A.": "latin", "Société Générale": "latin", "ZZM ÇELİK TİCARET ANONİM ŞİRKETİ": "latin",
    "CÔNG TY TNHH QUANTEX": "latin", "ʻIkenākea Piʻikea, LP": "latin", "ＪＯＥ": "latin",
    "ACCIONES Y VALORES S.A COMISIONISTA DE BOLSА": "latin",          # one Cyrillic А among 35 Latin letters
    "国泰金融控股股份有限公司": "other", "ネスレ日本株式会社": "other", "조인에셋글로벌자산운용 주식회사": "other",
    "Кургандормаш": "other", "العلمين المتحدة للتجارة": "other", "גולם ריסרץ' בע\"מ": "other",
    "บริษัท ราช กรุ๊ป จำกัด (มหาชน)": "other", "ΑΦΟΙ ΧΑΛΑΡΗ ΕΠΕ": "other", "𠀋𠀍": "other",
    "SAS Institute Japan株式会社": "mixed", "News Tomato (뉴스토마토)": "mixed", "ALPHA ΕΠΕΝΔΥΤΙΚΗΣ Α.Ε.": "mixed",
    "1776": "none", "84.51°": "none",
}


def test_name_script_in_postgresql(store):
    got = {n: store.con.execute("select companies.name_script(%s)", [n]).fetchone()[0] for n in SCRIPTS}
    assert got == SCRIPTS
    assert store.con.execute("select companies.name_script(null)").fetchone()[0] is None


def gleif_entity(name, *, english=None, latin=None, previous=None, auto=None):
    other = []
    if previous:
        other.append({"name": previous, "language": "en", "type": "PREVIOUS_LEGAL_NAME"})
    if english:
        other.append({"name": english, "language": "en", "type": "ALTERNATIVE_LANGUAGE_LEGAL_NAME"})
    tr = [{"name": latin, "type": "PREFERRED_ASCII_TRANSLITERATED_LEGAL_NAME"}] if latin else []
    tr += [{"name": auto, "type": "AUTO_ASCII_TRANSLITERATED_LEGAL_NAME"}] if auto else []
    return {"legalName": {"name": name}, "otherNames": other, "transliteratedOtherNames": tr}


def test_gleif_other_names():
    e = gleif_entity("Общество \"ИнвестНова\"", english=' "InvestNova"   Limited ', previous="Old Name", auto="Obshchestvo")
    assert gleif.other_names(e) == {"english_name": '"InvestNova" Limited'}            # never an old name or a machine one
    assert gleif.other_names(gleif_entity("國泰金融", latin="CATHAY FINANCIAL HOLDING CO., LTD.")) == {
        "latin_name": "CATHAY FINANCIAL HOLDING CO., LTD."}
    assert gleif.other_names(gleif_entity("ACME LTD", latin="Acme Ltd")) == {}           # the same as the legal name
    trading = {"legalName": {"name": "株式会社X"}, "otherNames": [
        {"name": "X Trading", "language": "en", "type": "TRADING_OR_OPERATING_NAME"},
        {"name": "X Inc.", "language": "en-US", "type": "ALTERNATIVE_LANGUAGE_LEGAL_NAME"},
        {"name": "X GmbH", "language": "de", "type": "ALTERNATIVE_LANGUAGE_LEGAL_NAME"}]}
    assert gleif.other_names(trading) == {"english_name": "X Inc."}                      # the legal name first


def test_english_name_is_worked_out_by_postgresql_and_follows_corrections(store):
    store.upsert_companies([
        company("gleif", "L1", "国泰金融控股股份有限公司", "TW", latin_name="CATHAY FINANCIAL HOLDING CO., LTD."),
        company("gleif", "L2", "Кургандормаш", "RU", english_name="Курган"),            # "English" name not in Latin letters
        company("gleif", "L3", "Nestlé S.A.", "CH", english_name="Nestle Ltd"),          # Latin already: its own name
        company("wikidata", "Q1", "1776", "US")])
    rows = {r[0]: r[1:] for r in store.con.execute(
        "select id, v.name_script, c.english_name, v.name_en from companies.companies c join companies.v_companies v using (id)")}
    assert rows == {"gleif:L1": ("other", "CATHAY FINANCIAL HOLDING CO., LTD.", "CATHAY FINANCIAL HOLDING CO., LTD."),
                    "gleif:L2": ("other", None, None), "gleif:L3": ("latin", None, "Nestlé S.A."),
                    "wikidata:Q1": ("none", None, "1776")}
    store.con.execute("update companies.companies set name = 'Kurgandormash' where id = 'gleif:L2'")   # a person's correction
    assert store.con.execute("select name_script, name_en from companies.v_companies where id = 'gleif:L2'").fetchone() == (
        "latin", "Kurgandormash")
    assert store.count() == 4                                                              # nothing deleted, ever
    assert store.count("name_script is null") == 2                                         # Latin is left empty in the table


def test_migration_fills_in_rows_saved_before_the_trigger(store):
    """Rows saved before this change get their values when the schema file runs (only the rows that aren't Latin)."""
    from warehouse import postgres
    store.upsert_companies([company("gleif", "L1", "国泰金融", "TW", latin_name="CATHAY"), company("gleif", "L2", "Acme", "US")])
    store.con.execute("alter table companies.companies disable trigger companies_name_script")
    store.con.execute("update companies.companies set name_script = null, english_name = null")
    store.con.execute("alter table companies.companies enable trigger companies_name_script")
    store.con.execute("delete from public.schema_versions where schema = 'companies'")
    assert postgres.migrate(store.con, ("companies",)) == ["companies"]
    assert store.con.execute("select id, name_script, english_name from companies.companies order by id").fetchall() == [
        ("gleif:L1", "other", "CATHAY"), ("gleif:L2", None, None)]


def test_fill_english_names_asks_gleif_again_and_stops_when_refused(store):
    store.upsert_companies([company("gleif", f"L{i}", f"株式会社{i}", "JP") for i in range(1, 4)]
                           + [company("gleif", "L9", "ACME LTD", "GB")])
    state = {"refuse": False}

    def route(method, q, body):
        if state["refuse"]:
            return (429, "slow down")
        out = []
        for n in q["filter[lei]"].split(","):
            rec = lei(n, f"株式会社{n[1:]}", "JP")
            if n != "L3":                                    # L3 has no other names at GLEIF
                rec["attributes"]["entity"]["transliteratedOtherNames"] = [
                    {"name": f"Company {n[1:]} K.K.", "type": "PREFERRED_ASCII_TRANSLITERATED_LEGAL_NAME"}]
            out.append(rec)
        return {"data": out}

    srv = Server({"/api/v1/lei-records": route})
    settings = settings_for(srv.base)
    assert gleif.needs_english_name(store) == ["L1", "L2", "L3"]                           # never the Latin one
    s = asyncio.run(gleif.fill_english_names(settings, store))
    assert (s["asked"], s["found"], s["requests"], s["left"], s["refused"]) == (3, 2, 2, 0, None)   # page_size 2
    assert store.con.execute("select english_name, extra->>'city' from companies.companies where id = 'gleif:L1'").fetchone() == (
        "Company 1 K.K.", "Colombo")
    assert gleif.needs_english_name(store) == ["L3"]
    state["refuse"] = True
    s = asyncio.run(gleif.fill_english_names(settings, store))
    assert s["refused"].startswith("GLEIF refused the request (HTTP 429") and s["asked"] == 0 and s["left"] == 1
    assert store.count() == 4


@pytest.mark.django_db
def test_companies_tab_hides_names_in_other_scripts_unless_switched_on(store, client):
    from django.contrib.auth.models import Group, User
    from django.urls import reverse
    from webapp.insights import queries
    from webapp.insights.common import Reader
    store.upsert_companies([
        company("wikidata", "Q1", "Acme Inc", "US", "https://acme.com"),
        company("gleif", "L1", "国泰金融控股股份有限公司", "TW", latin_name="CATHAY FINANCIAL HOLDING CO., LTD."),
        company("gleif", "L2", "深圳市源兴果品股份有限公司", "CN"),
        company("gleif", "L3", "SAS Institute Japan株式会社", "JP"),
        company("gleif", "L4", "Société Générale", "FR")])
    with Reader() as db:
        summary = db.get(queries.companies_summary)
        everything = db.get(queries.companies_summary, everything=True)
        shown = db.get(queries.companies_list, sort="name")
        all_ = db.get(queries.companies_list, sort="name", everything=True)
        by_original = db.get(queries.companies_list, q="国泰")
        by_english = db.get(queries.companies_list, q="cathay")
        hidden_search = db.get(queries.companies_list, q="深圳")
        d = db.get(queries.company_detail, "gleif:L1")
    c = summary["counts"]
    assert (c["companies"], c["countries"], c["other_scripts"], c["english_given"]) == (3, 3, 2, 1)
    assert sorted(r["country"] for r in summary["by_country"]) == ["FR", "TW", "US"]
    assert everything["counts"]["companies"] == 5 and everything["counts"]["other_scripts"] == 2
    assert [(r["name"], r["original_name"]) for r in shown["items"]] == [
        ("Acme Inc", None), ("CATHAY FINANCIAL HOLDING CO., LTD.", "国泰金融控股股份有限公司"), ("Société Générale", None)]
    assert all_["total"] == 5 and "深圳市源兴果品股份有限公司" in [r["name"] for r in all_["items"]]
    assert by_original["total"] == 1 == by_english["total"] and hidden_search["total"] == 0
    assert (d["company"]["name"], d["company"]["original_name"]) == ("CATHAY FINANCIAL HOLDING CO., LTD.", "国泰金融控股股份有限公司")

    user = User.objects.create_user("ada", password="correct-horse-battery-9")
    user.groups.set(Group.objects.filter(name="Administrator"))
    client.force_login(user)
    html = client.get(reverse("insights_companies")).content.decode()
    assert "CATHAY FINANCIAL" in html and "深圳" not in html and "SAS Institute" not in html and "国泰" not in html
    assert "☐ Show names in other scripts" in html and "?names=all" in html and 'name="names"' not in html
    html = client.get(reverse("insights_companies"), {"names": "all"}).content.decode()
    assert "深圳" in html and "国泰" in html and "☑ Show names in other scripts" in html and '<input type="hidden" name="names" value="all">' in html
    rows = client.get(reverse("insights_company_rows"), {"names": "all", "country": "cn"})
    assert rows.content.decode().count('<tr class="click"') == 1 and rows["HX-Push-Url"].endswith("names=all")
    rows = client.get(reverse("insights_company_rows"), {"country": "cn"})
    assert rows.content.decode().count('<tr class="click"') == 0 and json.loads(rows["HX-Trigger"])["listCount"].startswith("0")
    panel = client.get(reverse("insights_company", args=["gleif:L1"])).content.decode()
    assert "CATHAY FINANCIAL" in panel and "the name as registered" in panel and "Name in Latin letters" in panel
    assert store.count() == 5
