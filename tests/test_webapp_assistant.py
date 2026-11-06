"""The web app, phase 7 (docs/user-platform-plan.md): the AI assistant. Plan's test: "Sri Lankan companies in the news this month with LinkedIn" gives a
correct, saved, updating dataset. The AI itself is a stand-in here, except that the real SDK builds the real request; what it's told, what it may run and
what comes back are all real."""

import json
from contextlib import contextmanager
from datetime import datetime, timezone as tz
from decimal import Decimal
from types import SimpleNamespace

import anthropic
import httpx2
import psycopg
import pytest
from django.contrib.auth.models import Group, User
from django.db import connection
from django.urls import reverse

from warehouse import postgres
from webapp.assistant import agent, prompt, running, sqlcheck
from webapp.assistant.models import Question
from webapp.audit.models import AuditEvent
from webapp.catalog import rights
from webapp.catalog.models import Dataset, DatasetRight, Field
from webapp.catalog.scan import scan

pytestmark = pytest.mark.django_db
PLAN_QUESTION = "Sri Lankan companies in the news this month with LinkedIn"
PLAN_SQL = """select distinct c.name as company, c.website, c.linkedin
from companies.v_companies c join news.news_items n on n.title ilike '%' || c.name || '%'
where c.country = 'LK' and c.linkedin is not null and length(c.name) >= 4 and n.first_seen_at >= date_trunc('month', now()) order by company"""
SECRET_HEADLINE = "Acme Holdings wins secret headline contract"
NOW = datetime.now(tz.utc)


def make_user(name, *roles):
    u = User.objects.create_user(name, password="correct-horse-battery-9")
    u.groups.set(Group.objects.filter(name__in=roles))
    return u


def add_headline(title, url, when=None):
    with connection.cursor() as cur:
        cur.execute("insert into news.news_items (url, title, group_code, first_seen_at) values (%s, %s, 'LK', %s)", [url, title, when or NOW])


def add_company(id_, name, domain, linkedin=None, country="LK"):
    with connection.cursor() as cur:
        cur.execute("insert into companies.companies (id, source, source_id, name, country, website, domain) values (%s, 'cse', %s, %s, %s, %s, %s)", [id_, id_, name, country, f"https://{domain}", domain])
        if linkedin:
            cur.execute("insert into companies.sites (domain, status, social) values (%s, 'ok', %s::jsonb)", [domain, json.dumps({"linkedin": linkedin})])


@pytest.fixture
def world(monkeypatch):
    """Real tables and a real scan on the test's connection. Queries run on it too, undone afterwards, read-only as they'd be on the AI's own login."""
    connection.ensure_connection()
    con = connection.connection
    with connection.cursor() as cur:
        cur.execute("create schema if not exists companies; create schema if not exists vehicles; create schema if not exists news")
    postgres.migrate(con, ("companies", "vehicles", "news"))
    add_company("a", "Acme Holdings", "acme.lk", "https://linkedin.com/company/acme")
    add_company("b", "Beta Bank", "beta.lk")                                      # in the news, but no LinkedIn
    add_company("c", "Gamma Ltd", "gamma.lk", "https://linkedin.com/company/gamma")   # LinkedIn, but not in the news
    add_company("d", "Delta Corp", "delta.com", "https://linkedin.com/company/delta", "US")   # in the news, not Sri Lankan
    add_headline(SECRET_HEADLINE, "u1")
    add_headline("Beta Bank opens a branch", "u2")
    add_headline("Delta Corp in the US", "u3")
    add_headline("Gamma Ltd long ago", "u4", datetime(2020, 1, 1, tzinfo=tz.utc))
    scan()

    @contextmanager
    def session():
        with con.transaction(force_rollback=True):
            yield con
    monkeypatch.setattr(running, "_session", session)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    return con


def reply(sql="", title="Answer", explanation="What it does", clarification="", stop_reason="end_turn"):
    text = json.dumps({"sql": sql, "title": title, "explanation": explanation, "clarification": clarification})
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="thinking", thinking="..."), SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=5000, cache_creation_input_tokens=0))


class FakeClaude:
    """Answers in turn, and keeps everything it was sent, as sent."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=lambda o: {"block": vars(o)})))
        return self.replies.pop(0)

    def everything_sent(self) -> str:
        return json.dumps(self.requests)


def use_claude(monkeypatch, *replies) -> FakeClaude:
    fake = FakeClaude(*replies)
    monkeypatch.setattr(agent, "client", lambda: fake)
    return fake


def signed_in(client, user):
    client.force_login(user)
    return client


def ask(client, text=PLAN_QUESTION):
    return client.post(reverse("assistant_ask"), {"question": text}, HTTP_HOST="localhost")


def admin():
    return make_user("boss", "Administrator")


# ── the plan's test ──────────────────────────────────────────────────
def test_plan_example_gives_a_correct_saved_updating_dataset(world, client, monkeypatch):
    fake = use_claude(monkeypatch, reply(PLAN_SQL, "LK companies in the news with LinkedIn", "Sri Lankan companies named in a headline this month that link to LinkedIn."))
    signed_in(client, admin())

    answer = ask(client)
    assert answer.status_code == 200
    page = answer.content.decode()
    assert "Acme Holdings" in page and "1 row" in page and "linkedin.com/company/acme" in page
    assert "Beta Bank" not in page and "Gamma Ltd" not in page and "Delta Corp" not in page
    q = Question.objects.get()
    assert (q.status, q.rows, q.calls) == ("answered", 1, 1)
    assert sorted(q.sources) == ["companies.v_companies", "news.news_items"]
    assert q.cost_usd == Decimal("0.009") and q.input_tokens == 1000
    assert fake.requests[0]["model"] == "claude-opus-5-5"

    saved = client.post(reverse("assistant_save", args=[q.pk]), {"label": "LK companies with LinkedIn"}, HTTP_HOST="localhost")
    ds = Dataset.objects.get(label="LK companies with LinkedIn")
    assert saved.status_code == 302 and saved.url == reverse("dataset", args=[ds.ref])
    assert ds.is_query and not ds.is_own and ds.record_key is None and ds.hide_target is None
    assert {s.ref for s in ds.query_sources.all()} == {"companies.v_companies", "news.news_items"}
    rows_url = reverse("dataset_rows", args=[ds.ref])
    names = [r["company"] for r in client.get(rows_url, HTTP_HOST="localhost").json()["rows"]]
    assert names == ["Acme Holdings"]
    assert client.get(reverse("dataset", args=[ds.ref]), HTTP_HOST="localhost").status_code == 200

    # new data arrives: it's a view, so it's simply there
    add_headline("Gamma Ltd expands", "u9")
    names = [r["company"] for r in client.get(rows_url, HTTP_HOST="localhost").json()["rows"]]
    assert names == ["Acme Holdings", "Gamma Ltd"]
    # and the next scan brings its count up to date
    scan()
    ds.refresh_from_db()
    assert ds.row_count == 2 and not ds.missing
    # it can be exported like any other dataset
    csv = client.get(reverse("dataset_export", args=[ds.ref, "csv"]), HTTP_HOST="localhost")
    assert b"Gamma Ltd" in b"".join(csv.streaming_content if csv.streaming else [csv.content])


def test_no_rows_ever_reach_the_ai(world, client, monkeypatch):
    """It's told how many rows there were, and that's all: not a value, even when asked to fix a query or when a query fails."""
    fake = use_claude(monkeypatch,
                      reply("select c.nope as x from companies.v_companies c"),      # fails: the error goes back
                      reply("select n.title as title from news.news_items n where n.title = 'nothing like this'"),      # 0 rows: said so
                      reply(PLAN_SQL.replace("c.linkedin is not null", "true")))
    signed_in(client, admin())
    page = ask(client).content.decode()
    assert "Acme Holdings" in page            # the person sees it...
    sent = fake.everything_sent()
    assert SECRET_HEADLINE not in sent        # ...the AI never does
    assert "linkedin.com/company" not in sent and "acme.lk" not in sent
    assert "0 rows" in sent and "nope" in sent
    assert len(fake.requests) == 3


def test_what_the_ai_is_told_is_the_catalog_only(world, monkeypatch):
    fake = use_claude(monkeypatch, reply(clarification="Which year?"))
    agent.ask("How many?", admin(), "2026-11-06")
    request = fake.requests[0]
    system = request["system"][0]["text"] + request["system"][1]["text"]
    assert "companies.v_companies" in system and "news.news_items" in system and "linkedin" in system
    assert "Today is 2026-11-06" in request["messages"][0]["content"]
    assert "2026-11-06" not in system          # the date would stop the catalog being reused from the cache
    assert request["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert "app." not in prompt.describe([]) and "audit." not in system.replace("audit.hidden", "")


def test_value_lists_are_categories_not_rows():
    def field(values, fill=100, base="text"):
        return SimpleNamespace(values=values, fill_pct=fill, base=base)
    assert prompt.values_to_tell(field(["LK", "US"]), 100) == ["LK", "US"]
    assert not prompt.values_to_tell(field(["LK", "US"]), 4)                     # in a small table, a list of values is nearly the rows
    assert not prompt.values_to_tell(field(["LK", "US"], fill=2), 100)           # the same in a thinly filled field
    for address in ("https://linkedin.com/company/x", "a@b.lk", "acme.lk", "www.acme.lk", "acme.lk/about"):
        assert not prompt.values_to_tell(field(["LK", address]), 1000)
    assert not prompt.values_to_tell(field(["https://a.lk"], base="link"), 1000)
    assert prompt.values_to_tell(field(["Rs 1,045.50", "St. Lucia", "No. 5"]), 100)


def test_the_real_sdk_builds_the_request_we_mean(world):
    """The real SDK and its real request, with the network swapped out: the options are what the API documents."""
    seen = {}

    def answer(request):
        seen["headers"], seen["body"] = request.headers, json.loads(request.content)
        return httpx2.Response(200, json={"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "stop_reason": "end_turn", "stop_sequence": None,
                                          "content": [{"type": "text", "text": json.dumps({"sql": "", "title": "", "explanation": "", "clarification": "Which?"})}],
                                          "usage": {"input_tokens": 10, "output_tokens": 5}})
    real = anthropic.Anthropic(api_key="test-key-not-real", http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(answer)))
    response = agent._call(real, [{"type": "text", "text": "rules"}], [{"role": "user", "content": "hello"}])
    assert json.loads(agent._text_of(response))["clarification"] == "Which?"
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5" and body["fallbacks"] == "default" and body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "high"
    assert body["output_config"]["format"]["type"] == "json_schema" and body["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert agent.FALLBACK_BETA in seen["headers"]["anthropic-beta"]


# ── the SQL check ──────────────────────────────────────────────────
ALLOWED = {"companies.v_companies": {"columns": {"name", "country", "social", "linkedin", "website"}, "blocked_columns": {"legal_name"}, "blocked_keys": set()},
           "news.news_items": {"columns": {"title", "first_seen_at", "topics"}, "blocked_columns": set(), "blocked_keys": set()}}


@pytest.mark.parametrize("sql", [
    "delete from companies.v_companies",
    "update companies.v_companies set name = 'x'",
    "insert into companies.v_companies (name) values ('x')",
    "drop table companies.v_companies",
    "select name as n from companies.v_companies; drop table news.news_items",
    "with gone as (delete from companies.v_companies returning name) select name as n from gone",
    "select name as n into new_table from companies.v_companies",
    "select name as n from companies.v_companies for update",
    "copy companies.v_companies to '/tmp/x'",
    "set role postgres",
    "select id as i from app.auth_user",
    "select record as r from audit.hidden_records",
    "select rolname as r from pg_catalog.pg_roles",
    "select table_name as t from information_schema.tables",
    "select id as i from userdata.records",
    "select name as n from v_companies",
    "select name as n from crawl4ai.companies.v_companies",
    "select id as i from companies.companies",               # not on this person's list
    "select pg_read_file('/etc/passwd') as f from companies.v_companies",
    "select pg_sleep(100) as s from companies.v_companies",
    "select pg_catalog.lower(name) as n from companies.v_companies",
    "select current_setting('data_directory') as s from companies.v_companies",
    "select set_config('a.b', 'c', false) as s from companies.v_companies",
    "select query_to_xml('select 1', true, true, '') as x from companies.v_companies",
    "select dblink('x', 'y') as d from companies.v_companies",
    "select current_user as u from companies.v_companies",
    "select version() as v from companies.v_companies",
    "select nextval('s') as n from companies.v_companies",
    "select 1 as one",                                       # reads nothing
    "select * from companies.v_companies",
    "select c.* from companies.v_companies c",
    "select c as whole_row from companies.v_companies c",
    "select legal_name as l from companies.v_companies",     # switched off
    "select name || 'x' from companies.v_companies",         # no name for the column
    "select name as a, country as a from companies.v_companies",
    "",
    "not a query at all (",
])
def test_refused(sql):
    with pytest.raises(sqlcheck.SqlRefused):
        sqlcheck.check(sql, ALLOWED)


@pytest.mark.parametrize("sql", [
    PLAN_SQL,
    "select count(*) as n from companies.v_companies",
    "with lk as (select name from companies.v_companies where country = 'LK') select name as company from lk",
    "select country, count(*) as n from companies.v_companies group by country order by n desc",
    "select social ->> 'twitter' as t from companies.v_companies",
    "select name as a from companies.v_companies union all select title as a from news.news_items",
    "select t.topic from news.news_items n, lateral unnest(n.topics) as t(topic)",
    "select lower(name) as n, date_trunc('day', now()) as d, coalesce(website, '') as w from companies.v_companies",
    "select name as n from companies.v_companies c where exists (select 1 from news.news_items i where i.title ilike '%' || c.name || '%')",
])
def test_allowed(sql):
    assert sqlcheck.check(sql, ALLOWED).sql


def test_what_runs_is_the_parsed_query_not_the_text_that_came_in():
    checked = sqlcheck.check("select name as n /* drop everything */ from companies.v_companies -- and this", ALLOWED)
    assert "drop" not in checked.sql.lower() and "and this" not in checked.sql
    assert checked.sources == ["companies.v_companies"]
    assert "%" in sqlcheck.check("select name as n from companies.v_companies where name like 'a%'", ALLOWED).sql


def test_a_json_field_switched_off_cant_be_reached_any_way():
    allowed = {"companies.v_companies": {"columns": {"name", "social"}, "blocked_columns": set(), "blocked_keys": {("social", "phone")}}}
    assert sqlcheck.check("select social ->> 'twitter' as t from companies.v_companies", allowed).sql
    for sql in ("select social ->> 'phone' as t from companies.v_companies", "select social -> 'phone' as t from companies.v_companies",
                "select social as s from companies.v_companies", "select jsonb_extract_path_text(social, 'phone') as t from companies.v_companies"):
        with pytest.raises(sqlcheck.SqlRefused):
            sqlcheck.check(sql, allowed)


def test_a_refused_query_goes_back_and_the_second_try_counts(world, client, monkeypatch):
    fake = use_claude(monkeypatch, reply("select id as i from app.auth_user"), reply(PLAN_SQL))
    signed_in(client, admin())
    assert "Acme Holdings" in ask(client).content.decode()
    assert len(fake.requests) == 2
    assert "app.auth_user isn't one of the datasets" in fake.requests[1]["messages"][-1]["content"]
    q = Question.objects.get()
    assert q.status == "answered" and q.problem == "" and q.calls == 2


def test_three_tries_at_most_then_it_says_so(world, client, monkeypatch):
    fake = use_claude(monkeypatch, *[reply("select id as i from app.auth_user") for _ in range(5)])
    signed_in(client, admin())
    page = ask(client).content.decode()
    assert len(fake.requests) == agent.MAX_CALLS
    assert "wasn&#x27;t allowed" in page
    assert Question.objects.get().status == "failed" and Question.objects.get().sql == ""


def test_an_empty_answer_stands_when_trying_again_goes_wrong(world, client, monkeypatch):
    empty = "select title as t from news.news_items where title = 'nothing'"
    use_claude(monkeypatch, reply(empty, "Nothing"), reply("select id as i from app.auth_user"), reply(stop_reason="refusal"))
    signed_in(client, admin())
    assert "No rows match" in ask(client).content.decode()
    q = Question.objects.get()
    assert (q.status, q.rows, q.problem, q.title) == ("answered", 0, "", "Nothing")


def test_clarification_refusal_long_and_garbled_answers(world, client, monkeypatch):
    signed_in(client, admin())
    use_claude(monkeypatch, reply(clarification="Which country?"))
    assert "Which country?" in ask(client).content.decode()
    assert Question.objects.first().status == "clarify"
    use_claude(monkeypatch, reply(stop_reason="refusal"))
    assert "declined" in ask(client).content.decode()
    assert Question.objects.first().status == "refused"
    use_claude(monkeypatch, reply(stop_reason="max_tokens"))
    assert "too long" in ask(client).content.decode()
    garbled = reply()
    garbled.content[1].text = "not json"
    use_claude(monkeypatch, garbled)
    assert "couldn&#x27;t be read" in ask(client).content.decode()


@pytest.mark.parametrize("error, words", [
    (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")), "Can&#x27;t reach Claude"),
    (anthropic.AuthenticationError("no", response=httpx2.Response(401, request=httpx2.Request("POST", "https://x")), body=None), "key was refused"),
    (anthropic.RateLimitError("no", response=httpx2.Response(429, request=httpx2.Request("POST", "https://x")), body=None), "Try again in a minute"),
    (anthropic.InternalServerError("no", response=httpx2.Response(500, request=httpx2.Request("POST", "https://x")), body=None), "Try again in a minute"),
])
def test_api_errors_are_said_plainly_and_logged(world, client, monkeypatch, error, words):
    fake = FakeClaude()
    fake.beta.messages.create = lambda **_: (_ for _ in ()).throw(error)
    monkeypatch.setattr(agent, "client", lambda: fake)
    signed_in(client, admin())
    assert words in ask(client).content.decode()
    assert Question.objects.get().status == "failed"


# ── limits and logging ──────────────────────────────────────────────
def test_no_key_says_what_to_do(world, client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    signed_in(client, admin())
    assert "ANTHROPIC_API_KEY" in client.get(reverse("assistant"), HTTP_HOST="localhost").content.decode()
    assert "ANTHROPIC_API_KEY" in ask(client).content.decode()


def test_daily_limit_per_person(world, client, monkeypatch):
    monkeypatch.setenv("ASSISTANT_DAILY_LIMIT", "2")
    fake = use_claude(monkeypatch, reply(clarification="?"), reply(clarification="?"), reply(clarification="?"))
    me = admin()
    signed_in(client, me)
    ask(client), ask(client)
    assert "2 questions for today" in ask(client).content.decode()
    assert len(fake.requests) == 2 and Question.objects.count() == 2
    client.force_login(make_user("other", "Analyst"))     # someone else still can
    ask(client)
    assert len(fake.requests) == 3


def test_monthly_budget_stops_everyone(world, client, monkeypatch):
    monkeypatch.setenv("ASSISTANT_MONTHLY_USD", "1")
    me = admin()
    Question.objects.create(user=me, text="earlier", cost_usd=Decimal("1.5"))
    fake = use_claude(monkeypatch, reply(clarification="?"))
    signed_in(client, me)
    assert "budget" in ask(client).content.decode()
    assert not fake.requests


def test_the_page_moves_on_with_the_answer(world, client, monkeypatch):
    use_claude(monkeypatch, reply(PLAN_SQL))
    signed_in(client, admin())
    page = ask(client).content.decode()
    assert 'id="left" hx-swap-oob="true">19 of 20' in page
    assert 'id="recent" hx-swap-oob="true"' in page and "1 rows" in page


def test_question_length_and_empty(world, client, monkeypatch):
    fake = use_claude(monkeypatch)
    signed_in(client, admin())
    assert "Type a question" in ask(client, "   ").content.decode()
    assert "over 1,000" in ask(client, "x" * 1001).content.decode()
    assert not fake.requests and not Question.objects.exists()


def test_each_question_is_in_the_audit_log_and_usage_page(world, client, monkeypatch):
    use_claude(monkeypatch, reply(PLAN_SQL))
    me = admin()
    signed_in(client, me)
    ask(client)
    event = AuditEvent.objects.get(action="ai_question")
    assert PLAN_QUESTION in event.summary and "v_companies" in str(event.detail)
    assert Question.objects.get().rows == 1
    page = client.get(reverse("assistant_usage"), HTTP_HOST="localhost")
    assert page.status_code == 200 and PLAN_QUESTION in page.content.decode() and me.username in page.content.decode()
    client.force_login(make_user("plain", "Analyst"))
    assert client.get(reverse("assistant_usage"), HTTP_HOST="localhost").status_code == 403


# ── who may do what ──────────────────────────────────────────────────
def grant(ref, role):
    schema, name = ref.split(".")
    DatasetRight.objects.update_or_create(dataset=Dataset.objects.get(schema=schema, name=name), group=Group.objects.get(name=role), defaults={"can_view": True})


def test_viewer_has_no_assistant(world, client):
    signed_in(client, make_user("viewer", "Viewer"))
    assert client.get(reverse("assistant"), HTTP_HOST="localhost").status_code == 403


def test_the_ai_only_knows_what_the_person_may_see(world, monkeypatch):
    DatasetRight.objects.all().delete()
    grant("companies.v_companies", "Analyst")
    me = make_user("analyst", "Analyst")
    fake = use_claude(monkeypatch, reply("select title as t from news.news_items"), reply(clarification="Hmm"), reply(clarification="Hmm"))
    a = agent.ask("Headlines", me, "2026-11-06")
    catalog = fake.requests[0]["system"][1]["text"]
    assert "companies.v_companies" in catalog and "news.news_items" not in catalog
    assert "news.news_items isn't one of the datasets" in fake.requests[1]["messages"][-1]["content"]
    assert a.status == "clarify"


def test_what_the_ai_may_use_can_be_switched_off(world, client, monkeypatch):
    me = admin()
    signed_in(client, me)
    ds = Dataset.objects.get(schema="news", name="news_items")
    page = client.get(reverse("catalog_dataset", args=[ds.pk]), HTTP_HOST="localhost").content.decode()
    assert 'name="use_in_ai"' in page
    data = {"label": ds.label, "enabled": "on", "description": ""}
    assert client.post(reverse("catalog_dataset", args=[ds.pk]), data, HTTP_HOST="localhost").status_code == 302
    ds.refresh_from_db()
    assert ds.use_in_ai is False
    assert ds not in prompt.usable_datasets(me)
    assert "news.news_items" not in prompt.describe(prompt.usable_datasets(me))


def test_catalog_leaves_out_what_shouldnt_be_used(world):
    """Tables whose hidden rows only the view leaves out, fields switched off, and people's own tables."""
    me = admin()
    Field.objects.filter(dataset__name="v_companies", column="website").update(available=False)
    used = prompt.usable_datasets(me)
    refs = {d.ref for d in used}
    assert "companies.companies" not in refs and "companies.v_companies" in refs
    assert not [r for r in refs if r.startswith("userdata.")]
    text = prompt.describe(used)
    start = text.index("companies.v_companies")
    assert "  website" not in text[start:text.index("\n\n", start)]
    allowed = prompt.allowed_map(used)
    assert "website" in allowed["companies.v_companies"]["blocked_columns"]
    with pytest.raises(sqlcheck.SqlRefused):
        sqlcheck.check("select website as w from companies.v_companies", allowed)


def test_hidden_rows_stay_hidden_in_answers(world, client, monkeypatch):
    with connection.cursor() as cur:
        cur.execute("insert into audit.hidden_records (dataset, record, hidden_by) values ('companies.companies', 'a', 'x')")
    use_claude(monkeypatch, reply(PLAN_SQL), reply(PLAN_SQL))
    signed_in(client, admin())
    assert "No rows match" in ask(client).content.decode()


def saved_query(client, monkeypatch, user, label="Mine"):
    use_claude(monkeypatch, reply(PLAN_SQL))
    client.force_login(user)
    ask(client)
    q = Question.objects.filter(user=user).first()
    client.post(reverse("assistant_save", args=[q.pk]), {"label": label}, HTTP_HOST="localhost")
    return q, Dataset.objects.get(label=label)


def test_saved_answer_is_private_then_shared_but_only_to_those_who_see_its_sources(world, client, monkeypatch):
    owner, colleague, outsider = make_user("owner", "Analyst"), make_user("colleague", "Editor"), make_user("outsider", "Viewer")
    DatasetRight.objects.all().delete()
    for role in ("Analyst", "Editor"):
        grant("companies.v_companies", role)
        grant("news.news_items", role)
    q, ds = saved_query(client, monkeypatch, owner)
    page = reverse("dataset", args=[ds.ref])
    assert client.get(page, HTTP_HOST="localhost").status_code == 200

    client.force_login(colleague)                         # private at first
    assert client.get(page, HTTP_HOST="localhost").status_code == 403
    assert client.get(reverse("assistant_question", args=[q.pk]), HTTP_HOST="localhost").status_code == 403

    client.force_login(owner)
    groups = Group.objects.filter(name__in=["Editor", "Viewer"])
    done = client.post(reverse("dataset_share", args=[ds.ref]), {f"view_{g.pk}": "on" for g in groups}, HTTP_HOST="localhost")
    assert done.url == reverse("assistant_saved", args=[ds.pk])
    assert PLAN_SQL.split()[0] in client.get(done.url, HTTP_HOST="localhost").content.decode().lower()

    client.force_login(colleague)                         # shared, and can see what it reads
    assert client.get(page, HTTP_HOST="localhost").status_code == 200
    assert ds in rights.visible_datasets(colleague)
    assert client.post(reverse("dataset_share", args=[ds.ref]), {}, HTTP_HOST="localhost").status_code == 403

    # sharing with a role that can't see the sources gives it nothing (and it can't see the rows through the export either)
    outsider.groups.set(Group.objects.filter(name="Viewer"))
    client.force_login(outsider)
    assert client.get(page, HTTP_HOST="localhost").status_code == 403
    assert client.get(reverse("dataset_export", args=[ds.ref, "csv"]), HTTP_HOST="localhost").status_code == 403
    assert client.get(reverse("assistant_saved", args=[ds.pk]), HTTP_HOST="localhost").status_code in (403,)


def test_saving_is_checked_again_against_what_the_person_may_see_now(world, client, monkeypatch):
    owner = make_user("owner", "Analyst")
    DatasetRight.objects.all().delete()
    grant("companies.v_companies", "Analyst")
    grant("news.news_items", "Analyst")
    use_claude(monkeypatch, reply(PLAN_SQL))
    client.force_login(owner)
    ask(client)
    q = Question.objects.get()
    DatasetRight.objects.filter(dataset__name="news_items").delete()
    response = client.post(reverse("assistant_save", args=[q.pk]), {"label": "Too late"}, HTTP_HOST="localhost")
    assert response.url == reverse("assistant_question", args=[q.pk])
    assert not Dataset.objects.filter(label="Too late").exists()
    page = client.get(response.url, HTTP_HOST="localhost").content.decode()     # nor does looking at the old answer show it
    assert "Acme Holdings" not in page and "news.news_items" in page


def test_saving_needs_a_name_of_its_own(world, client, monkeypatch):
    me = admin()
    q, _ = saved_query(client, monkeypatch, me, "Taken")
    use_claude(monkeypatch, reply(PLAN_SQL))
    ask(client)
    q2 = Question.objects.first()
    for label, words in (("", "Give it a name"), ("taken", "already have")):
        r = client.post(reverse("assistant_save", args=[q2.pk]), {"label": label}, HTTP_HOST="localhost", follow=True)
        assert words in r.content.decode()
    assert Dataset.objects.filter(schema="userdata", name__startswith="a_").count() == 1


def test_deleting_a_saved_answer_drops_only_its_view(world, client, monkeypatch):
    me = admin()
    _, ds = saved_query(client, monkeypatch, me, "Gone soon")
    response = client.post(reverse("dataset_delete_table", args=[ds.ref]), {"confirm": "wrong"}, HTTP_HOST="localhost")
    assert response.url == reverse("assistant_saved", args=[ds.pk])
    response = client.post(reverse("dataset_delete_table", args=[ds.ref]), {"confirm": "Gone soon"}, HTTP_HOST="localhost")
    assert response.url == reverse("assistant")
    ds.refresh_from_db()
    assert ds.missing
    with connection.cursor() as cur:
        cur.execute("select to_regclass(%s)", [ds.ref])
        assert cur.fetchone()[0] is None
        cur.execute("select count(*) from companies.companies")
        assert cur.fetchone()[0] == 4
    client.get(reverse("assistant"), HTTP_HOST="localhost")     # the "deleted" message, once
    assert b"Gone soon" not in client.get(reverse("assistant"), HTTP_HOST="localhost").content
    scan()      # the scan doesn't bring it back, or fail on it
    ds.refresh_from_db()
    assert ds.missing


def test_saved_answers_arent_listed_as_own_tables(world, client, monkeypatch):
    saved_query(client, monkeypatch, admin(), "A saved answer")
    client.get(reverse("assistant"), HTTP_HOST="localhost")     # the "saved" message, once
    assert b"A saved answer" not in client.get(reverse("my_tables"), HTTP_HOST="localhost").content
    assert b"A saved answer" in client.get(reverse("assistant"), HTTP_HOST="localhost").content


def test_someone_elses_question_stays_theirs(world, client, monkeypatch):
    use_claude(monkeypatch, reply(PLAN_SQL))
    owner = make_user("owner", "Analyst")
    client.force_login(owner)
    ask(client)
    q = Question.objects.get()
    client.force_login(make_user("nosy", "Editor"))
    assert client.get(reverse("assistant_question", args=[q.pk]), HTTP_HOST="localhost").status_code == 403
    assert client.post(reverse("assistant_save", args=[q.pk]), {"label": "Mine now"}, HTTP_HOST="localhost").status_code == 403
    assert PLAN_QUESTION.encode() not in client.get(reverse("assistant"), HTTP_HOST="localhost").content
    client.force_login(admin())
    assert client.get(reverse("assistant_question", args=[q.pk]), HTTP_HOST="localhost").status_code == 200


def test_page_shows_the_answer_with_html_in_values_escaped(world, client, monkeypatch):
    add_company("x", "<script>alert(1)</script> Co", "x.lk", "https://linkedin.com/company/x")
    add_headline("<script>alert(1)</script> Co in the news", "u7")
    use_claude(monkeypatch, reply(PLAN_SQL, "<b>Title</b>", "<i>Explained</i>"))
    signed_in(client, admin())
    page = ask(client).content.decode()
    assert "<script>alert" not in page and "&lt;script&gt;" in page and "<b>Title" not in page and "<i>Explained" not in page


# ── the login itself, on a real server ──────────────────────────────
def test_the_ai_login_can_read_data_and_nothing_else(pg_env):
    postgres.setup()
    postgres.migrate(schemas=("companies", "news"))
    with psycopg.connect(pg_env["writer"], autocommit=True) as w:
        w.execute("insert into companies.companies (id, source, source_id, name) values ('a', 's', 'a', 'Acme')")
    with psycopg.connect(pg_env["app"], autocommit=True) as a:
        a.execute("create table app.secrets (x int)")
        a.execute("create table userdata.private_table (x int)")

    with postgres.connect("agent", autocommit=True) as g:
        assert g.execute("select count(*) from companies.v_companies").fetchone()[0] == 1
        assert g.execute("show default_transaction_read_only").fetchone()[0] == "on"
        assert g.execute("show statement_timeout").fetchone()[0] == "30s"
        for sql in ("select * from app.secrets", "select * from userdata.private_table", "select * from audit.hidden_records", "insert into companies.companies (id) values ('b')",
                    "update companies.companies set name = 'x'", "create table companies.mine (x int)", "select pg_read_file('/etc/hosts')"):
            with pytest.raises(psycopg.Error):
                g.execute(sql)

    # a view through which the AI's login could write to the database doesn't exist: turning read-only off doesn't give it rights
    with postgres.connect("agent", autocommit=True) as g:
        g.execute("set default_transaction_read_only = off")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            g.execute("delete from companies.companies")

    # and the real run step, on the real login: rows come back, and a slow query is cut short
    result = running.run("select name as n from companies.v_companies")
    assert (result.total, result.columns, result.rows) == (1, ["n"], [["Acme"]])
    with pytest.raises(running.QueryFailed):
        running.run("select missing_column as n from companies.v_companies")
    agent_check = next(c for c in postgres.check() if "AI assistant" in c.what)
    assert agent_check.ok, agent_check.detail


def test_the_ai_login_sees_tables_made_later(pg_env):
    """A collector can add a table after setup: the login reads it without another grant."""
    postgres.setup()
    postgres.migrate(schemas=("news",))
    with psycopg.connect(pg_env["writer"], autocommit=True) as w:
        w.execute("create table news.added_later (x int)")
    with postgres.connect("agent", autocommit=True) as g:
        assert g.execute("select count(*) from news.added_later").fetchone()[0] == 0


def test_no_database_login_is_said_plainly(world, monkeypatch):
    def broken():
        raise RuntimeError("The login doesn't exist yet. Create it:  uv run python -m warehouse setup")
    @contextmanager
    def session():
        broken()
        yield
    monkeypatch.setattr(running, "_session", session)
    use_claude(monkeypatch, reply(PLAN_SQL))
    a = agent.ask(PLAN_QUESTION, admin(), "2026-11-06")
    assert a.status == "failed" and "warehouse setup" in a.problem
