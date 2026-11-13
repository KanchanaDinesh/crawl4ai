"""Annual reports (annualreports/): small PDFs made here with WeasyPrint, read offline; saving on the throwaway PostgreSQL."""

import argparse
import base64
import io
import logging
import os
import shutil

import pytest

from annualreports import figures as fig
from annualreports import identify as ident
from annualreports.cli import cmd_run, load_settings
from annualreports.document import build
from annualreports.reading import tesseract_path
from annualreports.tables import is_number, parse_number

HEADER = "TEST HOLDINGS PLC | ANNUAL REPORT 2024/25"


def _weasyprint():
    """WeasyPrint needs Pango; Homebrew's library folder isn't searched by itself (the same trick as webapp/reports/making.py)."""
    import cffi
    original = cffi.FFI.dlopen

    def dlopen(self, name, flags=0):
        try:
            return original(self, name, flags)
        except OSError:
            for folder in ("/opt/homebrew/lib", "/usr/local/lib"):
                path = os.path.join(folder, str(name))
                if os.path.exists(path):
                    return original(self, path, flags)
            raise
    cffi.FFI.dlopen = dlopen
    try:
        import weasyprint
    except OSError:
        pytest.skip("WeasyPrint's libraries (Pango) aren't installed")
    finally:
        cffi.FFI.dlopen = original
    return weasyprint


def _picture_of_text(text: str) -> str:
    """A PNG with words drawn in it, as a data address: a page with no text layer, like a scan."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1400, 300), "white")
    ImageDraw.Draw(img).text((40, 100), text, fill="black", font=ImageFont.load_default(size=64))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def report_html(revenue="5,537,934", with_picture=True) -> str:
    rows = [("Revenue", "3", revenue, "5,411,506", "-", "906,286"), ("Cost of Sales", "", "(4,420,119)", "(4,338,321)", "-", "(763,255)"),
            ("Gross Profit", "", "1,117,815", "1,073,185", "-", "143,031"), ("Profit Before Tax", "", "(12,407)", "96,383", "50,082", "89,165"),
            ("Income Tax Expense", "4", "(42,198)", "198", "(15,510)", "(61,465)"),
            ("Profit for the Year", "", "(54,605)", "96,581", "34,572", "27,700"), ("Earnings Per Share", "5", "(0.18)", "0.31", "0.11", "0.09")]
    body = "".join(f"<tr><td class=l>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td><td>{r[4]}</td><td>{r[5]}</td></tr>" for r in rows)
    balance = "".join(f"{'<tr><td colspan=4 style=height:110px></td></tr>' if a == 'Stated Capital' else ''}"
                      f"<tr><td class=l>{a}</td><td></td><td>{b}</td><td>{c}</td></tr>" for a, b, c in
                      [("Property, Plant and Equipment", "4,549,104", "4,736,271"), ("Inventories", "1,391,200", "1,220,061"),
                       ("Total Assets", "8,056,384", "7,753,389"), ("Stated Capital", "100,372", "100,372"), ("Retained Earnings", "4,152,273", "3,934,141"),
                       ("Total Equity", "4,252,645", "4,034,513")])
    picture = (f'<section><img src="{_picture_of_text("SHAREHOLDER INFORMATION")}" style="width:16cm"></section>' if with_picture else "")
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
        @page {{ size: A4; margin: 2cm; @top-left {{ content: "{HEADER}"; font-size: 7pt; }} }}
        body {{ font-family: Helvetica, Arial, sans-serif; font-size: 9pt; }}
        h1 {{ font-size: 16pt; font-weight: bold; }} h2 {{ font-size: 13pt; font-weight: bold; }}
        section {{ page-break-after: always; }}
        table.plain td {{ text-align: right; padding: 1px 0 1px 14px; }} table.plain td.l {{ text-align: left; padding-left: 0; width: 7cm; }}
        table.ruled {{ border-collapse: collapse; }} table.ruled td {{ border: 1px solid black; padding: 3px 8px; }}
        </style></head><body>
        <section><h1>TEST HOLDINGS PLC</h1><p>Annual Report 2024/25</p><p>We make plastic brushes from what others throw away.</p></section>
        <section><h1>STATEMENT OF PROFIT OR LOSS</h1><p>For the year ended 31 March 2025</p>
          <table class=plain>
            <tr><td class=l></td><td></td><td colspan=2 style="text-align:center">Group</td><td colspan=2 style="text-align:center">Company</td></tr>
            <tr><td class=l></td><td>Note</td><td>2025</td><td>2024</td><td>2025</td><td>2024</td></tr>
            <tr><td class=l></td><td></td><td>Rs. '000</td><td>Rs. '000</td><td>Rs. '000</td><td>Rs. '000</td></tr>
            {body}
            <tr><td class=l>Dividend Per Share</td><td></td><td>0.35</td><td>0.18</td><td>0.35</td><td>0.18</td></tr>
          </table></section>
        <section><h1>STATEMENT OF FINANCIAL POSITION</h1><p>As at 31 March 2025</p>
          <table class=plain>
            <tr><td class=l></td><td>Note</td><td>2025</td><td>2024</td></tr>
            <tr><td class=l></td><td></td><td>Rs. '000</td><td>Rs. '000</td></tr>
            {balance}
          </table></section>
        <section><h1>BOARD OF DIRECTORS</h1>
          <p>Mr. A. B. Perera<br>Chairman</p><p>Dr. Nimal Silva<br>Independent Non-Executive Director</p>
          <p>Mr. A. B. Perera has led the company since 2010, and Mr. Perera said it was a good year.</p></section>
        <section><h2>Attendance at board meetings</h2>
          <table class=ruled><tr><td>Director</td><td>Meetings</td><td>Attended</td></tr>
            <tr><td>Mr. A. B. Perera</td><td>12</td><td>12</td></tr><tr><td>Dr. Nimal Silva</td><td>12</td><td>11</td></tr></table></section>
        {picture}
        </body></html>"""


def make_pdf(path, **kw):
    _weasyprint().HTML(string=report_html(**kw)).write_pdf(str(path))
    return path


@pytest.fixture
def settings():
    s = load_settings()
    s["ocr"] = dict(s["ocr"], enabled=False)
    s["documents"], s["links"] = {}, {}
    return s


@pytest.fixture(scope="module")
def report_pdf(tmp_path_factory):
    return make_pdf(tmp_path_factory.mktemp("ar") / "Test Holdings 2024-25.pdf")


# --- the small parts ------------------------------------------------------------------------------------------

def test_numbers_are_kept_as_printed_and_read_right():
    assert parse_number("(366,495)") == -366495 and parse_number("4,549,104") == 4549104 and parse_number("12.5%") == 12.5
    assert parse_number("-") is None and parse_number("—") is None and parse_number("") is None
    assert is_number("(1,234.5)") and is_number("2025") and is_number("-") and not is_number("Rs.") and not is_number("1,2")


def test_fiscal_year_and_names():
    assert ident.fiscal_year("2024/2025") == ("2024/25", 2025) and ident.fiscal_year("2024/25") == ("2024/25", 2025)
    assert ident.fiscal_year("2024") == ("2024", 2024)
    assert ident.name_key("B P P L Holdings PLC") == ident.name_key("BPPL HOLDINGS PLC")
    assert ident.name_key("The Ceylon Chamber of Commerce") == ident.name_key("CEYLON CHAMBER OF COMMERCE")


def test_figure_labels_must_match_exactly():
    assert fig.metric_of("Profit/ (Loss) Before Tax") == "profit_before_tax" and fig.metric_of("– Profit for the year 4") == "profit_for_year"
    assert fig.metric_of("Revenue from contracts with customers") == "revenue" and fig.metric_of("Basic earnings per ordinary share") == "eps_basic"
    assert fig.metric_of("Revenue reserve") is None and fig.metric_of("Profit before tax from discontinued operations") is None
    assert fig.column_year("Group 2024/25 Rs. '000") == 2025 and fig.column_entity("Company 2025") == "company"
    assert fig.plain_column("Group 2025 Rs. '000") and fig.plain_column("At 31 Dec 2024 $m") and fig.plain_column(None, need_year=False)
    assert not fig.plain_column("2024 Commercial Banking $m") and not fig.plain_column("Change %") and not fig.plain_column("Note")
    assert not fig.plain_column("service centres and intra-Group eliminations $m", need_year=False) and not fig.plain_column(None)


def test_running_header_names_the_company_and_year():
    pages = [f"{HEADER}\n{n}\nSome text for the year ended 31 March 2025." for n in range(1, 8)]
    out = ident.identify("x.pdf", pages, {})
    assert (out["company"], out["fiscal_year"], out["year"], out["period_end"]) == ("TEST HOLDINGS PLC", "2024/25", 2025, "2025-03-31")
    assert ident.identify("x.pdf", ["of the Annual Report and Accounts 2024"] * 3 + ["HSBC Holdings plc Annual Report and Accounts 2024"],
                          {})["company"] == "HSBC Holdings plc"        # a sentence isn't a company; a name ending in plc wins
    chamber = ["THE CEYLON CHAMBER OF COMMERCE ANNUAL REPORT 2023/24", "Members", "Talawakelle Tea Estates PLC Annual Report 2023/24"]
    assert ident.identify("x.pdf", chamber, {})["company"] == "THE CEYLON CHAMBER OF COMMERCE"     # the cover, not one member's name
    assert ident.identify("x.pdf", pages, {}, {"company": "By Hand PLC", "fiscal_year": "2023"})["company"] == "By Hand PLC"
    assert ident.identify("x.pdf", ["ABANS ELECTRICALS PLC", "12 Annual Report 2024/25", "Annual Report 2024/25 13"], {})["fiscal_year"] == "2024/25"


def test_names_and_roles_from_tables_of_directors():
    from annualreports.people import people_on_page
    from annualreports.reading import Page
    text = ("Board of Directors\nMr. Sumal Joseph\nSanjiva Perera Executive - 01 43\nMr. Suresh Dilhan Perera Non-Executive\n"
            "Mrs. N.C. Madanayake Non-Independent Non\nExecutive Director 6\nMr. Nissanka B Weerasekara - Director, Mr.U. G.Madanayake - Chairman,\n"
            "Mr. C V Kulatilaka\nThe Board considered the annual declaration made by the Non-\n")
    page = Page(1, 1, 1, text + "Chairperson's Responsibilities\nThe Chairperson leads the Board and is\n", "pdf",
                headings=[{"text": "Chairperson's Responsibilities", "size": 14, "top": 1}])
    found = {p["name"]: p["role"] for p in people_on_page(page)}
    assert found == {"Sumal Joseph Sanjiva Perera": "Executive", "Suresh Dilhan Perera": "Non-Executive",       # a name over two lines
                     "N.C. Madanayake": "Non-Independent Non Executive Director",                             # no attendance count
                     "Nissanka B Weerasekara": None, "C V Kulatilaka": None}                                  # a list, a sentence: no role


# --- a whole PDF ---------------------------------------------------------------------------------------------

def test_a_report_is_read_completely(report_pdf, settings):
    r = build(report_pdf, settings)
    d = r["document"]
    assert (d["company"], d["fiscal_year"], d["year"], d["period_end"], d["page_count"]) == ("TEST HOLDINGS PLC", "2024/25", 2025, "2025-03-31", 6)
    assert len(d["sha256"]) == 64 and d["company_from"] == "running header"
    assert "We make plastic brushes" in r["pages"][0].text                      # every page's text is kept
    assert any("STATEMENT OF PROFIT OR LOSS" in s["title"] for s in r["sections"])
    assert any("BOARD OF DIRECTORS" in h["text"] for h in r["pages"][3].headings)

    pl = next(t for t in r["tables"] if t["page_no"] == 2)
    assert pl["method"] == "aligned" and pl["statement"] == "income" and pl["unit_text"] == "Rs. '000" and pl["multiplier"] == 1000
    assert pl["columns"][0].startswith("Note") and pl["columns"][1] == "Group 2025 Rs. '000" and pl["columns"][4] == "Company 2024 Rs. '000"
    data = [row["cells"] for row in pl["rows"] if row["kind"] == "data"]
    assert data[0] == ["Revenue", "3", "5,537,934", "5,411,506", "-", "906,286"]       # exactly as printed
    assert ["Cost of Sales", "", "(4,420,119)", "(4,338,321)", "-", "(763,255)"] in data

    figs = {(f["metric"], f["column_header"]): f for f in r["figures"]}
    rev = figs[("revenue", "Group 2025 Rs. '000")]
    assert (rev["value"], rev["amount"], rev["currency"], rev["year"], rev["entity"], rev["page_no"]) == (5537934, 5537934000, "LKR", 2025, "group", 2)
    assert figs[("profit_before_tax", "Group 2025 Rs. '000")]["value"] == -12407
    eps = figs[("eps_basic", "Group 2025 Rs. '000")]
    assert eps["amount"] == -0.18 and eps["unit_text"] is None                      # per share: not multiplied by 1,000
    assert figs[("total_assets", "2025 Rs. '000")]["amount"] == 8056384000 and figs[("total_assets", "2025 Rs. '000")]["page_no"] == 3
    assert not any(f["column_header"] and "Note" in f["column_header"] for f in r["figures"])      # note numbers aren't values
    equity = figs[("total_equity", "2025 Rs. '000")]                               # below a gap: a second table, with the first one's headings
    second = next(t for t in r["tables"] if t["table_no"] == equity["table_no"])
    assert equity["amount"] == 4252645000 and second["columns_from"] == figs[("total_assets", "2025 Rs. '000")]["table_no"]

    ruled = [t for t in r["tables"] if t["method"] == "ruled"]
    assert ruled and ruled[0]["rows"][1]["cells"] == ["Mr. A. B. Perera", "12", "12"]

    people = {p["name"]: p for p in r["people"]}
    assert people["A. B. Perera"]["roles"] == ["Chairman"] and people["A. B. Perera"]["honorific"] == "Mr."
    assert people["Nimal Silva"]["roles"] == ["Independent Non-Executive Director"] and 4 in people["Nimal Silva"]["pages"]
    assert "Perera said it was a good year" not in " ".join(people)                 # a sentence isn't a person


def test_a_page_without_a_text_layer_is_flagged_not_guessed(report_pdf, settings):
    r = build(report_pdf, settings)                                                # OCR off
    last = r["pages"][-1]
    assert last.text_source == "needs_ocr" and not last.ocr_text and "Tesseract" in last.note
    assert r["document"]["pages_needs_ocr"] == 1


@pytest.mark.skipif(not tesseract_path(), reason="Tesseract isn't installed")
def test_ocr_reads_a_page_that_is_only_a_picture(report_pdf, settings):
    settings["ocr"]["enabled"] = True
    last = build(report_pdf, settings)["pages"][-1]
    assert last.text_source == "ocr" and "SHAREHOLDER" in last.ocr_text and last.ocr_confidence > 50
    assert "SHAREHOLDER" not in last.text                                          # the PDF's own text (only its header) stays apart from OCR


# --- saving --------------------------------------------------------------------------------------------------

@pytest.fixture
def pg(pg_env):
    from warehouse import postgres
    postgres.setup()
    return pg_env


def _run(folder, settings, **kw):
    args = argparse.Namespace(file=None, folder=str(folder), dry_run=False, again=False)
    vars(args).update(kw)
    return cmd_run(args, settings)


def _count(con, table):
    return con.execute(f"select count(*) from annualreports.{table}").fetchone()[0]


def test_runs_are_safe_to_repeat_and_dedupe_by_content(pg, tmp_path, settings, report_pdf):
    from warehouse import postgres
    folder = tmp_path / "reports"
    folder.mkdir()
    shutil.copy(report_pdf, folder / "a.pdf")
    assert _run(folder, settings) == 0
    con = postgres.connect("reader")
    counts = {t: _count(con, t) for t in ("documents", "files", "pages", "tables", "table_rows", "figures", "people", "sections")}
    assert counts["documents"] == 1 and counts["pages"] == 6 and counts["tables"] >= 3 and counts["figures"] > 10 and counts["people"] == 2

    assert _run(folder, settings) == 0                                             # again: nothing new, nothing doubled
    assert {t: _count(con, t) for t in counts} == counts

    shutil.copy(report_pdf, folder / "same report, other name.pdf")               # the same PDF under another name counts once
    assert _run(folder, settings) == 0
    assert _count(con, "documents") == 1 and _count(con, "files") == 2 and _count(con, "pages") == 6

    make_pdf(folder / "a.pdf", revenue="6,000,000", with_picture=False)            # a.pdf changed
    assert _run(folder, settings, file="a.pdf") == 0
    assert _count(con, "documents") == 2                                           # the old content stays: the other file still has it
    rev = con.execute("""select f.value_text from annualreports.v_key_figures f join annualreports.documents d on d.id = f.document_id
                         join annualreports.files fl on fl.sha256 = d.sha256 where fl.path like %s and f.metric = 'revenue'""",
                      ["%/a.pdf"]).fetchall()
    assert rev == [("6,000,000",)]

    (folder / "same report, other name.pdf").unlink()                              # gone from the folder: noted, its rows stay
    assert _run(folder, settings) == 0
    assert con.execute("select count(*) from annualreports.files where missing_since is not null").fetchone()[0] == 1
    assert _count(con, "documents") == 2

    make_pdf(folder / "a.pdf", revenue="7,000,000", with_picture=False)            # changed again: its previous content had only it
    assert _run(folder, settings) == 0
    assert _count(con, "documents") == 2 and _count(con, "pages") == 6 + 5
    con.close()


def test_a_dry_run_saves_nothing(pg, tmp_path, settings, report_pdf, caplog):
    from warehouse import postgres
    from annualreports.store import ReportStore
    ReportStore().close()                                                          # the schema exists, empty
    shutil.copy(report_pdf, tmp_path / "a.pdf")
    caplog.set_level(logging.INFO, logger="annualreports")
    assert _run(tmp_path, settings, dry_run=True) == 0
    out = caplog.text
    assert "TEST HOLDINGS PLC 2024/25: 6 pages" in out and "nothing saved" in out and "5,537,934" in out
    con = postgres.connect("reader")
    assert _count(con, "documents") == 0 and _count(con, "files") == 0 and _count(con, "runs") == 0
    con.close()


def test_a_broken_pdf_is_noted_and_the_others_still_read(pg, tmp_path, settings, report_pdf):
    from warehouse import postgres
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.4 this is not really a pdf")
    shutil.copy(report_pdf, tmp_path / "good.pdf")
    assert _run(tmp_path, settings) == 0
    con = postgres.connect("reader")
    assert _count(con, "documents") == 1
    assert con.execute("select error is not null from annualreports.files where path like %s", ["%broken.pdf"]).fetchone()[0]
    con.close()


def test_links_only_when_the_name_is_the_same(pg):
    from warehouse import postgres
    from annualreports.store import ReportStore
    postgres.migrate(schemas=("companies", "markets"))
    con = postgres.connect("writer", autocommit=True)
    con.execute("""insert into companies.companies (id, source, source_id, name, country) values
        ('cse:TEST.N0000', 'cse', 'TEST.N0000', 'TEST HOLDINGS PLC', 'LK'), ('wikidata:Q1', 'wikidata', 'Q1', 'Test Holdings PLC', 'LK'),
        ('wikidata:Q2', 'wikidata', 'Q2', 'Twin PLC', 'LK'), ('wikidata:Q3', 'wikidata', 'Q3', 'Twin PLC', 'GB')""")
    con.execute("""insert into markets.quotes (run_at, asset_class, symbol, name) values
        (now(), 'stock_lk', 'TEST.N0000', 'T E S T Holdings PLC'), (now(), 'stock_lk', 'TEST.X0000', 'T E S T Holdings PLC')""")
    con.close()
    store = ReportStore()
    link = store.link("TEST HOLDINGS PLC")
    assert (link["company_id"], link["cse_symbol"], link["cse_symbols"]) == ("cse:TEST.N0000", "TEST.N0000", ["TEST.N0000", "TEST.X0000"])
    twin = store.link("Twin PLC")
    assert twin["company_id"] is None and "2 wikidata companies share this name" in twin["how"]
    assert store.link("Testing Holdings PLC")["company_id"] is None                # close isn't the same
    assert store.link("HSBC Holdings plc", {"company_id": "wikidata:Q190464"})["how"] == "company by hand (settings)"
    store.close()


def test_cell_numbers_in_sql(pg):
    from warehouse import postgres
    from annualreports.store import ReportStore
    ReportStore().close()
    con = postgres.connect("reader")
    got = con.execute("""select annualreports.num('(366,495)'), annualreports.num('4,549,104'), annualreports.num('12.5%'),
                                annualreports.num('-'), annualreports.num('Note')""").fetchone()
    assert got == (-366495, 4549104, 12.5, None, None)
    con.close()


def test_a_sideways_page_keeps_its_text_but_no_cells(tmp_path, settings):
    rows = "".join(f"<tr><td>Crane {n}</td><td>{n},500</td><td>{n}33</td><td>{n}.5</td></tr>" for n in range(1, 7))
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>@page {{ size: A4; margin: 2cm }}
        td {{ text-align: right; padding: 2px 14px; }}</style></head><body>
        <div style="transform: rotate(90deg); transform-origin: left top; margin-left: 12cm; width: 20cm">
        <p>Property valuation of the dock cranes</p><table>{rows}</table></div></body></html>"""
    path = tmp_path / "sideways.pdf"
    _weasyprint().HTML(string=html).write_pdf(str(path))
    page = build(path, settings)["pages"][0]
    assert "printed sideways" in page.note and not page.tables
    assert "Crane 3" in page.text                                                   # PDFium's text is still the right way round
