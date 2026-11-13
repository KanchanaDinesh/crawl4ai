"""The Annual reports page: what it shows from what's saved, who may see it, and that a PDF's text can't do anything on the page."""

import pytest
from django.contrib.auth.models import Group, User
from django.urls import reverse

from webapp.catalog.models import Dataset, DatasetRight

pytestmark = pytest.mark.django_db


def make_user(name, *roles):
    u = User.objects.create_user(name, password="correct-horse-battery-9")
    u.groups.set(Group.objects.filter(name__in=roles))
    return u


@pytest.fixture(scope="module")
def report_pdf(tmp_path_factory):
    from test_annualreports import make_pdf
    return make_pdf(tmp_path_factory.mktemp("arweb") / "Test Holdings 2024-25.pdf")


@pytest.fixture
def saved(pg_env, report_pdf):
    from annualreports.cli import load_settings
    from annualreports.document import build
    from annualreports.store import ReportStore
    from warehouse import postgres
    postgres.setup()
    settings = load_settings()
    settings["ocr"]["enabled"] = False
    store = ReportStore()
    doc_id = store.save(build(report_pdf, settings), "Anual reports/test.pdf", None,
                        {"company_id": "cse:TEST.N0000", "cse_symbol": "TEST.N0000", "cse_symbols": ["TEST.N0000"], "how": "same name"})
    store.con.execute("""update annualreports.pages set text = text || %s where document_id = %s and page_no = 1""",
                      ["\nOur zebra policy <script>alert(1)</script> is new.", doc_id])
    yield doc_id
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
    menu = Menu.objects.get(code="annualreports")
    assert set(menu.roles.values_list("name", flat=True)) == set(Menu.objects.get(code="datasets").roles.values_list("name", flat=True))


def test_the_page_follows_the_rights_of_the_dataset(client, saved):
    client.force_login(make_user("vera", "Viewer"))
    urls = [reverse("annualreports"), reverse("annualreports_rows"), reverse("annualreports_report", args=[saved]),
            reverse("annualreports_page", args=[saved, 1]), reverse("annualreports_section", args=[saved, 1]),
            reverse("annualreports_search"), reverse("annualreports_hits"),
            reverse("annualreports_people"), reverse("annualreports_people_rows")]
    assert all(client.get(u).status_code == 403 for u in urls)                              # no right to the dataset yet
    dataset = Dataset.objects.create(schema="annualreports", name="v_documents", kind="view", label="Annual reports")
    right = DatasetRight.objects.create(dataset=dataset, group=Group.objects.get(name="Viewer"), can_view=True)
    assert "TEST HOLDINGS PLC" in text_of(client.get(reverse("annualreports")))
    right.delete()
    assert client.get(reverse("annualreports")).status_code == 403


def test_someone_without_the_menu_cant_open_it(client, saved):
    from webapp.accounts.models import Menu
    Menu.objects.get(code="annualreports").roles.remove(Group.objects.get(name="Viewer"))
    client.force_login(make_user("vera", "Viewer"))
    assert client.get(reverse("annualreports")).status_code == 403
    assert "/annual-reports/" not in text_of(client.get(reverse("home")))


def test_reports_list_shows_the_figures_as_printed(admin, saved):
    html = text_of(admin.get(reverse("annualreports")))
    assert "TEST HOLDINGS PLC" in html and "2024/25" in html and "TEST.N0000" in html and "Key figures" in html
    assert '>5,537,934</span><br><span class="muted">Rs. &#x27;000</span>' in html                # the number, and its unit under it
    assert ">(12,407)</span>" in html and ">8,056,384</span>" in html and "Rs. &#x27;000" in html
    assert "No reports match" in text_of(admin.get(reverse("annualreports_rows"), {"q": "nothing like this"}))
    assert "TEST HOLDINGS PLC" in text_of(admin.get(reverse("annualreports_rows"), {"company": "TEST HOLDINGS PLC"}))


def test_a_report_is_one_page_with_every_page_under_a_line(admin, saved):
    html = text_of(admin.get(reverse("annualreports_report", args=[saved])))
    assert "All reports" in html and "ar-nav" not in html                                  # no left-hand column
    assert "Revenue" in html and "Profit before tax" in html and "Earnings per share (basic)" in html
    assert "6 pages" in html and "2 people" in html                                       # the counts, not the lists' lengths
    assert all(f'id="page-{n}"' in html and f'<span class="ar-pno">Page {n}</span>' in html for n in range(1, 7))
    assert html.index('id="page-1"') < html.index('id="page-2"') < html.index('id="page-6"')
    assert 'href="#page-2"' in html                                                       # each figure links to its page here
    assert "Nimal Silva" in html and "Independent Non-Executive Director" in html and "Income statement" in html
    assert ">Group</th>" in html and ">Cost of Sales</td>" in html                       # tables cell by cell
    assert html.count("(4,420,119)") == 1                                                 # in the table, not again in the text
    assert "<h3>STATEMENT OF PROFIT OR LOSS</h3>" in html and "<p>For the year ended 31 March 2025</p>" in html
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html            # the PDF's text is only text
    assert admin.get(reverse("annualreports_report", args=[999999])).status_code == 404
    reports = text_of(admin.get(reverse("annualreports")))
    assert f'href="{reverse("annualreports_report", args=[saved])}"' in reports and "hx-target=\"#drawer-body\"" not in reports


def test_as_printed_and_marked_words(admin, saved):
    raw = text_of(admin.get(reverse("annualreports_report", args=[saved]), {"raw": "1"}))
    assert 'class="ar-raw"' in raw and raw.count("(4,420,119)") == 2                     # as printed: the text has the numbers too
    assert "<script>alert(1)</script>" not in raw
    marked = text_of(admin.get(reverse("annualreports_report", args=[saved]), {"q": "zebra"}))
    assert "<mark>zebra</mark>" in marked
    raw_marked = text_of(admin.get(reverse("annualreports_report", args=[saved]), {"q": "zebra", "raw": "1"}))
    assert "<mark>zebra</mark>" in raw_marked and "&lt;script&gt;" in raw_marked


def test_old_page_and_section_links_go_to_their_place(admin, saved):
    url = reverse("annualreports_report", args=[saved])
    res = admin.get(reverse("annualreports_page", args=[saved, 3]), {"q": "zebra"})
    assert res.status_code == 302 and res["Location"] == f"{url}?q=zebra#page-3"
    assert admin.get(url, {"page": "4"})["Location"] == f"{url}#page-4"                      # "Go to page"
    from warehouse import postgres
    con = postgres.connect("reader")
    no = con.execute("select section_no from annualreports.sections where document_id = %s and title = 'BOARD OF DIRECTORS'", [saved]).fetchone()[0]
    con.close()
    assert admin.get(reverse("annualreports_section", args=[saved, no]))["Location"] == f"{url}#section-{no}"
    assert f'id="section-{no}"' in text_of(admin.get(url)) and f'href="#section-{no}"' in text_of(admin.get(url))


def test_search_marks_the_words_and_escapes_the_rest(admin, saved):
    assert "Type words above" in text_of(admin.get(reverse("annualreports_search")))
    html = text_of(admin.get(reverse("annualreports_search"), {"q": "zebra policy"}))
    assert "<mark>zebra</mark>" in html and "<script>alert" not in html and "page 1" in html
    assert reverse("annualreports_page", args=[saved, 1]) + "?q=zebra%20policy" in html     # the report opens there, words marked
    assert "No page has all of those words" in text_of(admin.get(reverse("annualreports_hits"), {"q": "zebra giraffe"}))
    assert "page 2" in text_of(admin.get(reverse("annualreports_hits"), {"q": '"cost of sales"'}))      # a phrase
    assert "No page has all" in text_of(admin.get(reverse("annualreports_hits"), {"q": "zebra -policy"}))


def test_people_tab(admin, saved):
    html = text_of(admin.get(reverse("annualreports_people")))
    assert reverse("annualreports_report", args=[saved]) + "#people" in html
    assert "A. B. Perera" in html and "Chairman" in html and "Nimal Silva" in html
    assert "Nimal Silva" not in text_of(admin.get(reverse("annualreports_people_rows"), {"q": "chairman"}))


def test_no_reports_yet(client, pg_env):
    client.force_login(make_user("ada", "Administrator"))
    assert "No annual reports yet" in text_of(client.get(reverse("annualreports")))
    assert "Type words above" in text_of(client.get(reverse("annualreports_search")))
