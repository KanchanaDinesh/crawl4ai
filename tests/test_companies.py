"""Company directory (python -m companies): collect from the sources, resume, refusals, update, dashboard.

Every source is faked on localhost (Wikidata SPARQL, GLEIF, cse.lk, Companies House) and so are the
company websites; no internet.
"""

import asyncio
import csv
import io
import json
import threading
import zipfile
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from companies.collect import collect
from companies.sources import Rotation, company, website
from companies.sources import companies_house as ch
from companies.sources import wikidata
from companies.sources.cse import parse_list
from companies.store import CompanyStore
from companies.update import update
from test_pipeline_e2e import page


class Server:
    """routes: path -> fn(method, query: dict, body: dict) -> str | dict | (status, str|dict)."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, str]] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, self))
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


class _Handler(BaseHTTPRequestHandler):
    def __init__(self, srv, *args, **kwargs):
        self.srv = srv
        super().__init__(*args, **kwargs)

    def _answer(self, method):
        parts = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()} if length else {}
        self.srv.calls.append((method, self.path))
        fn = self.srv.routes.get(parts.path)
        out = fn(method, {k: v[0] for k, v in parse_qs(parts.query).items()}, body) if fn else (404, "not found")
        status, out = out if isinstance(out, tuple) else (200, out)
        data = (json.dumps(out) if isinstance(out, (dict, list)) else out).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json" if isinstance(out, (dict, list)) else "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._answer("GET")

    def do_POST(self):
        self._answer("POST")

    def log_message(self, *args):
        pass


def sparql(items):
    """A SPARQL JSON answer: items = [(qid, name, website, industry)]."""
    rows = []
    for qid, name, site, industry in items:
        b = {"item": {"value": f"http://www.wikidata.org/entity/{qid}"}, "website": {"value": site}}
        if name:
            b["name"] = {"value": name}
        if industry:
            b["industry"] = {"value": industry}
        rows.append(b)
    return {"results": {"bindings": rows}}


def lei(n, name, country):
    return {"type": "lei-records", "id": n, "attributes": {"lei": n, "entity": {
        "legalName": {"name": name}, "legalAddress": {"country": country, "city": "COLOMBO"},
        "legalForm": {"id": "9999", "other": "Private Limited Company"}, "registeredAs": "PV1",
        "creationDate": "2013-07-30T00:00:00Z"}}}


@pytest.fixture
def store(tmp_path, pg_env):
    """The companies store on the test PostgreSQL (a database of this test's own), downloads in tmp_path."""
    from warehouse import postgres
    postgres.setup()
    s = CompanyStore(tmp_path)
    yield s
    s.close()


def settings_for(base: str, **sources) -> dict:
    cfg = {
        "wikidata": {"enabled": True, "endpoint": f"{base}/sparql", "min_wait_seconds": 0, "timeout_seconds": 10,
                     "types": [{"id": "Q783794", "name": "company"}, {"id": "Q22687", "name": "bank"}]},
        "gleif": {"enabled": True, "api": f"{base}/api/v1", "delay_seconds": 0, "page_size": 2},
        "cse": {"enabled": True, "api": f"{base}/cse", "delay_seconds": 0, "per_batch": 2},
    }
    for name, extra in sources.items():
        cfg.setdefault(name, {}).update(extra)
    return {"user_agent": "test-agent/1.0", "priority_countries": ["US", "GB", "AU", "IN", "LK"], "sources": cfg,
            "update": {"concurrency": 4, "max_pages": 4, "key_pages": {"about": ["about"], "team": ["team"],
                                                                           "contact": ["contact"]}},
            "crawl": {"delay_seconds": 0, "page_timeout_ms": 10000, "check_robots_txt": True}}


def fake_sources(gleif_page2=lambda: None):
    """Wikidata: US and LK companies; GLEIF: two pages for LK, one for every other country; cse.lk: 3 listings."""
    def sparql_route(method, q, body):
        query = q["query"]
        if "wd:Q30 " in query and "Q783794" in query:
            return sparql([("Q1", "Acme Inc", "https://acme.com", "retail"), ("Q1", "Acme Inc", "http://acme.com/us", None),
                           ("Q2", None, "https://noname.com", None)])
        if "wd:Q854 " in query and "Q22687" in query:
            return sparql([("Q3", "Bank of Ceylon", "https://www.boc.lk/", "banking")])
        return sparql([])

    def gleif_route(method, q, body):
        country = q.get("filter[entity.legalAddress.country]", "")
        if q.get("page[cursor]") == "p2":
            refused = gleif_page2()
            if refused:
                return refused
            return {"data": [lei("LEI3", "THIRD (PRIVATE) LIMITED", "LK")], "links": {}}
        if country == "LK":
            return {"data": [lei("LEI1", "FIRST (PRIVATE) LIMITED", "LK"), lei("LEI2", "SECOND PLC", "LK")],
                    "links": {"next": f"{srv.base}/api/v1/lei-records?filter%5Bentity.legalAddress.country%5D=LK&page%5Bcursor%5D=p2"}}
        return {"data": [], "links": {}}

    def trade(method, q, body):
        return {"reqTradeSummery": [{"symbol": "JKH.N0000", "name": "JOHN KEELLS HOLDINGS PLC"},
                                    {"symbol": "AGST.N0000", "name": "AGSTAR PLC"}, {"symbol": "AGST.X0000", "name": "AGSTAR PLC"}]}

    def profile(method, q, body):
        if body.get("symbol") == "JKH.N0000":
            return {"reqComSumInfo": [{"name": "JOHN KEELLS HOLDINGS PLC", "web": "www.keells.com", "sector": "Capital Goods",
                                       "established": "1979", "boardType": "Main Board"}]}
        return (500, "oops")

    srv = Server({"/sparql": sparql_route, "/api/v1/lei-records": gleif_route, "/cse/tradeSummary": trade,
                  "/cse/companyProfile": profile})
    return srv


def test_collect_saves_name_website_country_and_continues_where_it_stopped(store):
    state = {"refuse": True}
    srv = fake_sources(lambda: (429, "slow down") if state["refuse"] else None)
    settings = settings_for(srv.base)
    s1 = asyncio.run(collect(settings, store, countries=["US", "LK"], max_minutes=1))

    rows = {r[0]: r for r in store.con.execute("select id, name, country, website, domain, industry from companies.companies").fetchall()}
    assert rows["wikidata:Q1"][1:] == ("Acme Inc", "US", "https://acme.com", "acme.com", "retail")   # https first
    assert "wikidata:Q2" not in rows                              # no English name: skipped
    assert rows["wikidata:Q3"][1:5] == ("Bank of Ceylon", "LK", "https://www.boc.lk/", "boc.lk")
    assert rows["cse:JKH.N0000"][1:5] == ("JOHN KEELLS HOLDINGS PLC", "LK", "https://www.keells.com", "keells.com")
    assert rows["cse:AGST.N0000"][3] is None and "cse:AGST.X0000" not in rows   # profile failed: name only; one per company
    assert {"gleif:LEI1", "gleif:LEI2"} <= set(rows) and "gleif:LEI3" not in rows
    assert s1["sources"]["gleif"]["refused"].startswith("GLEIF refused the request (HTTP 429")
    assert s1["sources"]["wikidata"]["added"] == 2 and s1["sources"]["cse"]["added"] == 2

    cursors = store.cursors("gleif")
    assert cursors["LK"]["next"].endswith("page%5Bcursor%5D=p2") and not cursors["LK"]["done_at"]
    assert cursors["US"]["done_at"] and store.cursors("cse")["listed"]["done_at"]
    assert store.cursors("wikidata")["LK"]["next_type"] == 2

    # Second run: GLEIF continues on page 2 (page 1 isn't asked again); finished sources aren't asked at all
    state["refuse"] = False
    srv.calls.clear()
    s2 = asyncio.run(collect(settings, store, countries=["US", "LK"], max_minutes=1))
    assert store.count("id = 'gleif:LEI3'") == 1 and s2["sources"]["gleif"]["added"] == 1
    paths = [p for _, p in srv.calls]
    assert all("p2" in p for p in paths if "lei-records" in p) and len(paths) == 1
    first = store.con.execute("select first_seen, last_seen from companies.companies where id = 'gleif:LEI1'").fetchone()
    assert first[0] == first[1]                                  # not seen again, nothing changed


def test_upsert_keeps_first_seen_and_counts(store):
    a = company("gleif", "L1", "Old Name", "lk")
    assert store.upsert_companies([a, a], "2026-11-01T00:00:00Z") == (1, 0)
    b = company("gleif", "L1", "New Name", "LK", "acme.lk")
    assert store.upsert_companies([b, company("gleif", "L2", "", "LK")], "2026-11-05T00:00:00Z") == (0, 1)
    name, website_, first, last = store.con.execute(
        "select name, website, to_char(first_seen, 'DD'), to_char(last_seen, 'DD') from companies.companies").fetchone()
    assert (name, website_, first, last) == ("New Name", "https://acme.lk", "01", "05")


def test_helpers():
    assert website("www.keells.com") == "https://www.keells.com" and website("not a site") is None
    assert website("ftp://x.com") is None and website("") is None
    assert parse_list({"reqTradeSummery": [{"symbol": "B.N0000", "name": "B"}, {"symbol": "A.X0000", "name": "A"},
                                           {"symbol": "A.N0000", "name": "A"}]}) == [["A.N0000", "A"], ["B.N0000", "B"]]
    rot = Rotation(["US", "LK", "AD", "AE", "AF"], ["US", "LK"])
    seen = []
    for code in rot:
        seen.append(code)
        if len(seen) == 6:
            rot.drop("US")
        if len(seen) == 9:
            break
    assert seen == ["US", "LK", "AD", "US", "LK", "AE", "LK", "AF", "LK"]
    rows = wikidata.parse(sparql([("Q9", "X", "http://x.com/long/path", "a"), ("Q9", "X", "https://x.com", "b")]), "GB", "company")
    assert rows[0]["website"] == "https://x.com" and rows[0]["industry"] == "a, b" and rows[0]["country"] == "GB"


def test_a_refused_source_saves_nothing_and_others_still_run(store):
    srv = Server({"/sparql": lambda m, q, b: (429, "Too Many Requests"),
                  "/cse/tradeSummary": lambda m, q, b: {"reqTradeSummery": [{"symbol": "X.N0000", "name": "X PLC"}]},
                  "/cse/companyProfile": lambda m, q, b: {"reqComSumInfo": [{"name": "X PLC", "web": "x.lk"}]}})
    settings = settings_for(srv.base)
    settings["sources"]["gleif"]["enabled"] = False
    summary = asyncio.run(collect(settings, store, countries=["LK"], max_minutes=1))
    assert "refused" in summary["sources"]["wikidata"] and store.cursors("wikidata") == {}
    assert store.count("source = 'cse'") == 1 and store.count("source = 'wikidata'") == 0


def test_sec_needs_a_contact_email(store):
    settings = settings_for("http://127.0.0.1:9", sec={"enabled": True, "url": "http://127.0.0.1:9/x.json"})
    for name in ("wikidata", "gleif", "cse"):
        settings["sources"][name]["enabled"] = False
    summary = asyncio.run(collect(settings, store, max_minutes=1))
    assert "contact_email" in summary["sources"]["sec"]["error"]


def ch_zip(path, rows):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["CompanyName", " CompanyNumber", "CompanyCategory", "CompanyStatus", "SICCode.SicText_1",
                "IncorporationDate", "RegAddress.PostTown"])
    w.writerows(rows)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("BasicCompanyData.csv", out.getvalue())


def test_companies_house_imports_in_chunks_and_resumes(store, tmp_path):
    files = {}
    for part in (1, 2):
        name = f"BasicCompanyData-2026-11-01-part{part}_2.zip"
        ch_zip(tmp_path / name, [[f"CO {part}-{i} LTD", f"0{part}{i:05d}", "Private Limited Company",
                                  "Active" if i != 1 else "Dissolved", "62020 - Information technology consultancy activities",
                                  "01/01/2020", "LONDON"] for i in range(5)])
        files[f"/{name}"] = (tmp_path / name).read_bytes()

    srv = Server({"/en_output.html": lambda m, q, b: "".join(f'<a href="{n[1:]}">x</a>' for n in files)})
    settings = settings_for(srv.base, companies_house={"enabled": True, "index_url": f"{srv.base}/en_output.html",
                                                      "chunk_rows": 2})
    for name in ("wikidata", "gleif", "cse"):
        settings["sources"][name]["enabled"] = False
    for name in files:                     # as if already downloaded (the fake server only serves text)
        (store.download_dir / name.lstrip("/")).write_bytes(files[name])
    summary = asyncio.run(collect(settings, store, max_minutes=1))
    assert summary["sources"]["companies_house"]["added"] == 8          # 2 files x 4 active companies
    row = store.con.execute("select name, country, industry from companies.companies where id = 'companies_house:0100000'").fetchone()
    assert row == ("CO 1-0 LTD", "GB", "Information technology consultancy activities")
    assert store.cursors("companies_house")["basic"]["done_at"] and not list(store.download_dir.glob("*.zip"))
    assert ch.latest_files('<a href="BasicCompanyData-2026-10-01-part1_7.zip"> <a href="BasicCompanyData-2026-11-01-part3_7.zip">') \
        == ("2026-11-01", 7)


def test_asic_reads_a_local_file_by_byte_offset(store, tmp_path):
    lines = ["﻿Company Name\tACN\tType\tClass\tSub Class\tStatus\tDate of Registration\tDate of Deregistration\t"
             "Previous State of Registration\tState Registration number\tModified since last report\tCurrent Name Indicator\tABN\t"
             "Current Name\tCurrent Name Start Date"]
    for i in range(30):
        lines.append(f"OLD NAME {i} PTY LTD\t{i:09d}\tAPTY\tLMSH\tPROP\tREGD\t08/01/1990\t\tNSW\t1\t\t\t1{i}\tNEW\t01/01/2000")
        lines.append(f"COMPANY {i} PTY LTD\t{i:09d}\tAPTY\tLMSH\tPROP\t{'DRGD' if i == 3 else 'REGD'}\t08/01/1990\t\tNSW\t1\t\tY\t1{i}\t\t")
    path = tmp_path / "company_202611.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    settings = settings_for("http://127.0.0.1:9", asic={"enabled": True, "file": str(path), "chunk_bytes": 900})
    for name in ("wikidata", "gleif", "cse"):
        settings["sources"][name]["enabled"] = False
    summary = asyncio.run(collect(settings, store, max_minutes=1))
    assert summary["sources"]["asic"]["batches"] > 3 and store.count("source = 'asic'") == 29
    assert store.con.execute("select name, country from companies.companies where id = 'asic:000000007'").fetchone() == (
        "COMPANY 7 PTY LTD", "AU")
    assert store.cursors("asic")["file"]["offset"] == path.stat().st_size
    again = asyncio.run(collect(settings, store, max_minutes=1))           # same file: nothing to do
    assert again["sources"]["asic"]["batches"] == 0


# ── update: reading the company websites ───────────────────────────────
FILLER = "<p>" + "Acme designs and makes strong cardboard boxes for farms, shops and factories across the island. " * 4 + "</p>"
HOME = page("Acme | Home", "<h1>Acme makes boxes</h1>" + FILLER + "<a href='/about'>About</a> <a href='/team'>Team</a>"
            "<a href='/contact'>Contact</a> <a href='https://www.linkedin.com/company/acme/'>in</a>"
            "<a href='https://www.facebook.com/acme'>fb</a>")
TEAM = page("Team", "<div><h3>Nimal Perera</h3><p>Founder &amp; CEO</p></div> We are a team of 40 people." + FILLER)
CONTACT = page("Contact", "<a href='mailto:hello@acme.lk'>mail</a> <a href='tel:+94112345678'>call</a>" + FILLER)


def test_update_reads_websites_and_backs_off_on_refusals(store):
    site = Server({"/": lambda m, q, b: HOME, "/about": lambda m, q, b: page("About", "About Acme" + FILLER),
                   "/team": lambda m, q, b: TEAM, "/contact": lambda m, q, b: CONTACT})
    refuser = Server({"/": lambda m, q, b: (403, "Forbidden")})
    robots = Server({"/robots.txt": lambda m, q, b: "User-agent: *\nDisallow: /", "/": lambda m, q, b: HOME})
    store.upsert_companies([company("wikidata", "Q1", "Acme", "LK", site.base + "/"),
                            company("cse", "ACME.N0000", "ACME PLC", "LK", site.base),        # same website: read once
                            company("gleif", "L9", "Refuser", "US", refuser.base + "/"),
                            company("wikidata", "Q2", "Private", "GB", robots.base + "/"),
                            company("wikidata", "Q3", "On Facebook", "GB", "https://www.facebook.com/somepage"),
                            company("gleif", "L1", "No Site", "LK")])
    settings = settings_for("http://127.0.0.1:9")
    settings["update"]["skip_domains"] = ["facebook.com"]
    summary = asyncio.run(update(settings, store, max_minutes=1))
    assert (summary["checked"], summary["ok"], summary["refused"], summary["not_allowed"]) == (3, 1, 1, 1)

    domain = site.base.split("//")[1]
    s = store.con.execute("select status, social, people, emails, phones, employees_hint from companies.sites "
                          "where domain = %s", [domain]).fetchone()
    assert s[0] == "ok" and s[1] == {"linkedin": "https://www.linkedin.com/company/acme/",   # jsonb: already decoded
                                     "facebook": "https://www.facebook.com/acme"}
    assert s[2] == [{"name": "Nimal Perera", "role": "Founder & CEO"}]
    assert s[3] == ["hello@acme.lk"] and s[4] == ["+94112345678"] and s[5] == "team of 40"
    refused = refuser.base.split("//")[1]
    assert store.con.execute("select count(*) from companies.sites where domain = %s", [refused]).fetchone()[0] == 0
    assert store.con.execute("select error from companies.refusals where domain = %s",
                             [refused]).fetchone()[0] == "HTTP 403"
    assert store.con.execute("select status from companies.sites where domain = %s",
                             [robots.base.split("//")[1]]).fetchone()[0] == "not_allowed"
    # nothing is due again straight away (the refusal waits refused_wait_days)
    assert asyncio.run(update(settings, store, max_minutes=1))["checked"] == 0
    assert store.con.execute("select site_status from companies.v_companies "
                             "where id = 'cse:ACME.N0000'").fetchone()[0] == "ok"


# ── dashboard ───────────────────────────────────────────────────────────────────
@pytest.mark.django_db
def test_dashboard_companies_tab(tmp_path, monkeypatch, pg_env, client):
    import json
    from django.contrib.auth.models import Group, User
    from django.urls import reverse
    from warehouse import postgres
    from webapp.insights import queries
    from webapp.insights.common import Reader
    postgres.setup()
    monkeypatch.setenv("PROJECT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("COMPANIES_DATA_DIR", raising=False)
    import importlib
    import project_paths
    import warehouse
    for mod in (project_paths, warehouse):
        importlib.reload(mod)
    s = CompanyStore()
    s.upsert_companies([company("wikidata", "Q1", "Acme Inc", "US", "https://acme.com", "retail", type="company"),
                        company("gleif", "L1", "ACME INC", "US", "acme.com"),
                        company("cse", "JKH.N0000", "JOHN KEELLS HOLDINGS PLC", "LK", "keells.com", "Capital Goods"),
                        company("gleif", "L2", "Ceylon Tea Ltd", "LK")])
    s.save_site({"domain": "acme.com", "url": "https://acme.com", "checked_at": "2026-11-05T00:00:00Z", "status": "ok",
                 "social": {"linkedin": "https://www.linkedin.com/company/acme/"}, "people": [{"name": "A B", "role": "CEO"}],
                 "emails": [], "phones": [], "linkedin_people": [], "pages": ["https://acme.com"]})
    s.set_cursor("gleif", "US", {"done_at": None})
    s.record_run("collect", "2026-11-05T00:00:00Z", 3.0, {"added": 4, "total": 4, "sources": {"gleif": {"refused": "GLEIF said 429"}}})
    s.close()
    with Reader() as db:
        summary = db.get(queries.companies_summary)
        all_ = db.get(queries.companies_list)
        lk = db.get(queries.companies_list, country="LK")
        linkedin = db.get(queries.companies_list, status="linkedin")
        search = db.get(queries.companies_list, q="keells")
        d = db.get(queries.company_detail, "wikidata:Q1")
    c = summary["counts"]
    assert (c["companies"], c["with_website"], c["sites_ok"], c["countries"]) == (4, 3, 1, 2)
    assert summary["by_country"][0]["name"] in ("United States", "Sri Lanka")
    assert summary["social"] == [{"platform": "linkedin", "n": 1}]
    assert summary["runs"][0]["problems"] == "gleif: GLEIF said 429"
    assert all_["total"] == 4 and all_["items"][0]["site_status"] == "ok"      # websites read first
    assert lk["total"] == 2 and linkedin["total"] == 2                         # both acme.com records
    assert search["items"][0]["name"] == "JOHN KEELLS HOLDINGS PLC"
    assert d["company"]["extra"] == {"type": "company"} and d["site"]["people"][0]["name"] == "A B"
    assert [x["id"] for x in d["same_website"]] == ["gleif:L1"]

    user = User.objects.create_user("ada", password="correct-horse-battery-9")
    user.groups.set(Group.objects.filter(name="Administrator"))
    client.force_login(user)
    html = client.get(reverse("insights_companies")).content.decode()
    assert "Acme Inc" in html and "United States" in html and "GLEIF said 429" in html and "4 · click a row for details" in html
    rows = client.get(reverse("insights_company_rows"), {"country": "lk"})
    assert rows.content.decode().count('<tr class="click"') == 2
    assert json.loads(rows["HX-Trigger"])["listCount"].startswith("2 · ") and rows["HX-Push-Url"].endswith("?country=LK")
    panel = client.get(reverse("insights_company", args=["wikidata:Q1"])).content.decode()
    assert "Acme Inc" in panel and "A B" in panel and "Same website in other records" in panel and "Type (Wikidata)" in panel
    assert "Not found." in client.get(reverse("insights_company", args=["nope"])).content.decode()


def test_a_403_is_reported_as_http_403_not_as_a_timeout():
    """crawl4ai's error for a 403 in plain-HTTP mode includes a code listing that mentions "Timeout"."""
    from techintel.fetch import friendly_error, status_from_error
    error = ("Unexpected error in _crawl_web at line 2869 in _handle_http (async_crawler_strategy.py):\n"
             "Error: HTTP request failed: HTTP 403: Unexpected status code for http://x/\n\nCode context:\n"
             '2865   raise self._wrap_error(ConnectionTimeoutError, "Request timed out", e, proxy_url)')
    assert status_from_error(error) == 403 and friendly_error(error) == "HTTP 403"
    assert status_from_error("timed out") is None and friendly_error("Error: timed out") == "timed out"


def test_update_takes_turns_between_countries(store):
    rows = [company("wikidata", f"US{i}", f"US {i}", "US", f"us{i}.com") for i in range(5)]
    rows += [company("wikidata", f"LK{i}", f"LK {i}", "LK", f"lk{i}.lk") for i in range(2)]
    rows += [company("wikidata", "AL0", "AL 0", "AL", "al0.al")]
    for i, r in enumerate(rows):
        store.upsert_companies([r], f"2026-11-01T00:00:{i:02d}Z")
    store.save_site({"domain": "us0.com", "url": "https://us0.com", "checked_at": "2026-11-05T00:00:00Z", "status": "ok"})
    due = [d["domain"] for d in store.due_sites({}, ["US", "LK"], 6)]
    assert due == ["us1.com", "lk0.lk", "al0.al", "us2.com", "lk1.lk", "us3.com"]
    assert [d["domain"] for d in store.due_sites({}, ["US", "LK"], 9, countries=["LK"])] == ["lk0.lk", "lk1.lk"]
    assert [d["domain"] for d in store.due_sites({}, [], 9, domains=["us0.com"])] == ["us0.com"]


def test_cli_show_and_stats(tmp_path, monkeypatch, capsys, pg_env):
    from warehouse import postgres
    postgres.setup()
    monkeypatch.setenv("PROJECT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("COMPANIES_DATA_DIR", raising=False)
    import importlib
    import project_paths
    import warehouse
    for mod in (project_paths, warehouse):
        importlib.reload(mod)
    s = CompanyStore()
    s.upsert_companies([company("cse", "JKH.N0000", "JOHN KEELLS HOLDINGS PLC", "LK", "keells.com"),
                        company("wikidata", "Q1", "Acme Inc", "US", "acme.com")])
    s.close()
    from companies.cli import main
    main(["show", "--country", "lk"])
    out = capsys.readouterr().out
    assert "JOHN KEELLS" in out and "Acme" not in out and "1 of 1 companies" in out
    main(["stats"])
    assert "2 companies, 2 with a website" in capsys.readouterr().out
