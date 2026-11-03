"""Offline tests for the building blocks: no network, no browser."""

from datetime import datetime, timedelta, timezone

from techintel.classify import RuleClassifier
from techintel.domains import DomainFilter, root_domain
from techintel.pages import parse_page, top_keywords
from techintel.sources import certstream, crtsh, hackernews, outlinks, seeds
from techintel.stages.monitor import diff_profiles
from techintel.techstack import TechDetector


# ── domains ────────────────────────────────────────────────────────────────
def test_root_domain():
    assert root_domain("https://api.shop.foo.com.lk/x?y=1") == "foo.com.lk"
    assert root_domain("*.example.ai") == "example.ai"
    assert root_domain("WWW.Example.COM") == "example.com"
    assert root_domain("http://127.0.0.1:8701/a") == "127.0.0.1:8701"
    assert root_domain("localhost") == "localhost"
    assert root_domain("") is None


def test_domain_filter(settings):
    f = DomainFilter(settings.section("domain_filter"))
    assert f.is_ignored("https://github.com/foo/bar")
    assert f.is_ignored("my-demo.vercel.app")
    assert not f.is_ignored("newstartup.ai")
    assert f.is_interesting_name("shop.lk")             # matched TLD
    assert f.is_interesting_name("getcloudstack.com")   # keyword in name
    assert not f.is_interesting_name("grandmasbakery.com")


# ── classification ────────────────────────────────────────────────────────
def _page(title="", description="", body="", headings=""):
    h = "".join(f"<h2>{x}</h2>" for x in headings.split("|") if x)
    return parse_page(f"<html><head><title>{title}</title><meta name='description' content='{description}'></head>"
                      f"<body>{h}<p>{body}</p></body></html>")


def test_classifier_qualifies_tech_site(settings):
    c = RuleClassifier(settings.section("categories"), settings.section("qualify"))
    filler = "We help teams ship faster with reliable tooling and clear dashboards for every workflow. " * 8
    page = _page("Acme AI: LLM inference platform", "Generative AI and machine learning infrastructure",
                 filler + "Deploy large language model inference with our API and SDK. Fine-tuning and RAG.",
                 "AI agents for developers|Serverless inference")
    v = c.assess(page)
    assert v.status == "qualified" and v.category == "ai_ml" and v.score >= 6


def test_classifier_rejects_parked_and_watches_placeholder(settings):
    c = RuleClassifier(settings.section("categories"), settings.section("qualify"))
    assert c.assess(_page("foo.ai", body="This domain is for sale! Buy this domain today.")).status == "rejected"
    assert c.assess(_page("Foo", body="Coming soon. Something big is launching.")).status == "watch"
    assert c.assess(_page("x", body="hello world")).status == "watch"              # too little content
    assert c.assess(_page("x"), final_host="www.sedo.com").status == "rejected"


def test_classifier_rejects_non_tech(settings):
    c = RuleClassifier(settings.section("categories"), settings.section("qualify"))
    body = "Fresh bread, cakes and pastries baked every morning in our family bakery in Kandy. " * 10
    assert c.assess(_page("Grandma's Bakery", "Cakes and bread", body)).status == "rejected"


# ── tech stack ────────────────────────────────────────────────────────────
def test_tech_detector(settings):
    d = TechDetector(settings.load_json("fingerprints.json"))
    html = ('<script id="__NEXT_DATA__"></script><script src="https://js.stripe.com/v3/"></script>'
            '<script src="https://www.googletagmanager.com/gtm.js?id=GTM-ABC123"></script>')
    names = {t["name"] for t in d.detect(html, {"Server": "cloudflare", "x-vercel-id": "abc"})}
    assert {"Next.js", "Stripe", "Google Tag Manager", "Cloudflare", "Vercel"} <= names
    assert "WordPress" not in names
    assert {t["name"] for t in d.detect("", {}, generator="WordPress 6.5")} == {"WordPress"}


# ── pages ─────────────────────────────────────────────────────────────────
def test_parse_page_and_keywords():
    p = parse_page("<html><head><title>T</title><meta property='og:site_name' content='Acme'></head>"
                   "<body><h1>Vector database</h1><script>var x=1</script><p>vector database for search</p></body></html>")
    assert p.site_name == "Acme" and p.headings == ["Vector database"] and "var x" not in p.text
    assert "vector database" in top_keywords([p.text, p.text])


# ── sources ───────────────────────────────────────────────────────────────
def test_seeds_parse():
    cands = seeds.parse_seeds("# comment\nhttps://www.foo.ai/about\nbar.lk  # note\n\n")
    assert [(c.domain, c.url) for c in cands] == [("foo.ai", "https://www.foo.ai/about"), ("bar.lk", "https://bar.lk")]


def test_hackernews_parse():
    hits = [{"objectID": "1", "title": "Show HN: Foo", "url": "https://app.foo.dev/x", "created_at": "2026-10-31T10:00:00Z",
             "points": 42, "num_comments": 7}, {"objectID": "2", "title": "Show HN: no url", "url": None}]
    cands = hackernews.parse_hits(hits)
    assert len(cands) == 1 and cands[0].domain == "foo.dev" and cands[0].detail["points"] == 42


def test_crtsh_parse(settings):
    f = DomainFilter(settings.section("domain_filter"))
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S.%f")
    old = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    entries = [
        {"id": 1, "entry_timestamp": recent, "common_name": "shop.newco.lk", "name_value": "shop.newco.lk\nwww.newco.lk",
         "issuer_name": "C=US, O=Let's Encrypt"},
        {"id": 2, "entry_timestamp": old, "common_name": "old.lk", "name_value": "old.lk"},
        {"id": 3, "entry_timestamp": recent, "common_name": "x.vercel.app", "name_value": "x.vercel.app"},
    ]
    cands = crtsh.parse_entries(entries, now - timedelta(days=3), f, "%.lk")
    assert [c.domain for c in cands] == ["newco.lk"]


def test_certstream_parse(settings):
    f = DomainFilter(settings.section("domain_filter"))
    msg = {"message_type": "certificate_update", "data": {
        "seen": 1761900000.0, "source": {"name": "Google Argon"},
        "leaf_cert": {"all_domains": ["*.agentlabs.io", "www.agentlabs.io", "grandmasbakery.com", "a.netlify.app"],
                      "issuer": {"O": "Let's Encrypt"}}}}
    assert [c.domain for c in certstream.parse_message(msg, f)] == ["agentlabs.io"]
    assert certstream.parse_message({"message_type": "heartbeat"}, f) == []


def test_outlinks(settings):
    f = DomainFilter(settings.section("domain_filter"))
    profiles = {"a.ai": {"domain": "a.ai", "external_domains": ["b.ai", "github.com", "c.ai"]}}
    cands = outlinks.collect_from_profiles(profiles, known={"c.ai"}, domain_filter=f, limit=10)
    assert [c.domain for c in cands] == ["b.ai"] and cands[0].detail["linked_from"] == "a.ai"


# ── monitor ───────────────────────────────────────────────────────────────
def test_diff_profiles():
    old = {"domain": "a.ai", "status": "ok", "description": "Old", "key_pages": {"about": "u1"},
           "tech_stack": ["React"], "signals": {"hiring": False, "has_pricing": False},
           "hiring": {"job_links": 0}, "pages": [{"url": "u0", "type": "home", "content_hash": "aaa"}]}
    new = {**old, "description": "New", "key_pages": {"about": "u1", "pricing": "u2"}, "tech_stack": ["Next.js", "React"],
           "signals": {"hiring": True, "has_pricing": True}, "hiring": {"job_links": 3},
           "pages": [{"url": "u0", "type": "home", "content_hash": "bbb"}]}
    types = sorted(c.change_type for c in diff_profiles(old, new))
    assert types == ["content_changed", "description_changed", "job_links_changed", "page_added",
                     "signal_on", "signal_on", "tech_added"]
    down = diff_profiles(old, {"domain": "a.ai", "status": "unreachable", "error": "timeout"})
    assert [c.change_type for c in down] == ["site_down"]


def test_strip_shared_lines_and_tld():
    from techintel.pages import strip_shared_lines
    from techintel.stages.analyze import _tld
    out = strip_shared_lines(["Menu\nVector search for RAG", "Menu\nPricing plans"])
    assert out == ["Vector search for RAG", "Pricing plans"]
    assert _tld("foo.com.lk") == ".com.lk" and _tld("bar.ai") == ".ai" and _tld("127.0.0.1:8080") == "(local)"


def test_report_lists_latest_first_with_url_and_new_tab_links(store):
    from techintel.stages.analyze import analyze
    from techintel.stages.report import render_html, render_markdown
    from techintel.models import utc_now
    now = utc_now()
    times = {"old.ai": "2026-01-01T08:00:00Z", "mid.ai": "2026-01-02T08:00:00Z", "new.ai": now}
    # discovered in mixed order; "old.ai" has the highest score
    for domain, score in (("mid.ai", 10), ("new.ai", 8), ("old.ai", 50)):
        when = now if domain == "new.ai" else times[domain].replace("2026-01", now[:7])
        times[domain] = when
        store.append("candidates", [{"domain": domain, "url": f"https://{domain}/", "source": "seeds",
                                     "discovered_at": when, "detail": {}}])
        store.append("qualifications", [{"domain": domain, "url": f"https://{domain}/", "status": "qualified",
                                         "reason": "ok", "checked_at": when, "category": "ai_ml", "score": score,
                                         "final_url": f"https://www.{domain}/"}])
    store.append("changes", [
        {"domain": "mid.ai", "change_type": "tech_added", "field": "tech_stack", "after": "Stripe", "detected_at": times["mid.ai"]},
        {"domain": "new.ai", "change_type": "signal_on", "field": "hiring", "after": True, "detected_at": now}])
    a = analyze(store, days=400)
    order = [s["domain"] for s in a["new_sites"]]
    assert order == sorted(order, key=lambda d: times[d], reverse=True)          # latest first, not by score
    assert [c["domain"] for c in a["notable_changes"]] == ["new.ai", "mid.ai"]
    page, md = render_html(a), render_markdown(a)
    first = order[0]
    assert f">https://www.{first}</a>" in page                                     # URL shown as the label
    assert page.count("target='_blank'") == page.count("<a href='http")             # every website link opens a new tab
    assert f"| 1 | " in md and f"[https://www.{first}](https://www.{first}/)" in md


def test_parse_rdap():
    from techintel.rdap import parse_rdap
    data = {"events": [{"eventAction": "registration", "eventDate": "2026-10-20T08:15:00Z"},
                       {"eventAction": "expiration", "eventDate": "2027-10-20T08:15:00Z"}],
            "entities": [{"roles": ["registrar"], "vcardArray": ["vcard", [["version", {}, "text", "4.0"],
                                                                           ["fn", {}, "text", "Namecheap, Inc."]]]}]}
    assert parse_rdap(data) == {"registered_at": "2026-10-20T08:15:00Z", "expires_at": "2027-10-20T08:15:00Z",
                                "updated_at": None, "registrar": "Namecheap, Inc."}


def test_enrich_and_report_dates_and_all_domains(settings, store, monkeypatch):
    import asyncio
    from techintel import rdap
    from techintel.stages import enrich
    from techintel.stages.analyze import analyze
    from techintel.stages.report import render_html, render_markdown, write_domains_csv
    from techintel.models import utc_now

    async def fake_lookup(session, domain, url_template, timeout):
        if domain.endswith(".lk"):
            raise LookupError("no RDAP record (TLD may not support RDAP)")
        return {"registered_at": "2026-10-20T08:15:00Z", "expires_at": None, "updated_at": None, "registrar": "X"}
    monkeypatch.setattr(rdap, "lookup", fake_lookup)

    now = utc_now()
    found = now                                              # all found in the same run...
    rows = [  # ...but seen online at different times (HN post / certificate), and different outcomes
        ("hn-old.ai", "2026-10-30T09:00:00.000Z", "qualified"),
        ("cert-new.dev", now[:11] + "00:00:01Z", "qualified"),
        ("shop.lk", now[:11] + "00:00:00Z", "rejected"),
        ("pending.io", "2026-10-31T09:00:00Z", None),       # not checked yet
    ]
    for domain, seen, status in rows:
        store.append("candidates", [{"domain": domain, "url": f"https://{domain}/", "source": "hackernews",
                                     "discovered_at": found, "signal_at": seen, "detail": {"title": f"Show HN: {domain}"}}])
        if status:
            store.append("qualifications", [{"domain": domain, "url": f"https://{domain}/", "status": status,
                                             "reason": "parked: 'x'" if status == "rejected" else "ok", "checked_at": found,
                                             "category": "ai_ml" if status == "qualified" else None, "score": 9}])
    first = asyncio.run(enrich.run(settings, store))
    assert (first["looked_up"], first["with_date"], first["failed"], first["left_for_next_run"]) == (2, 2, 0, 0)
    assert asyncio.run(enrich.run(settings, store)) == {"looked_up": 0}             # nothing left to look up

    a = analyze(store, days=30)
    assert [s["domain"] for s in a["new_sites"]] == ["cert-new.dev", "hn-old.ai"]   # by seen date, not found date
    assert a["new_sites"][0]["registered_at"] == "2026-10-20T08:15:00Z"
    assert a["all_domains_total"] == 4
    assert [d["status"] for d in a["all_domains"]] == ["qualified", "rejected", "not checked", "qualified"]
    page, md = render_html(a), render_markdown(a)
    assert "All discovered domains (4)" in page and "id='q'" in page and "not checked" in page
    assert page.count("target='_blank'") == page.count("<a href='http")             # website links: new tab
    assert "<a class='kpi' href='#all-domains'>" in page                            # KPI jumps to the list
    assert "Registered" in md and "pending.io" in md
    csv_path = settings.output_dir / "d.csv"
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    write_domains_csv(a, csv_path)
    assert len(csv_path.read_text().splitlines()) == 5


def test_friendly_errors_and_content_beats_bot_flag(settings):
    from techintel.classify import RuleClassifier
    from techintel.domains import DomainFilter
    from techintel.fetch import FetchResult, friendly_error
    from techintel.stages.qualify import evaluate, needs_browser
    assert friendly_error("Unexpected error ... Error: Connection failed: Cannot connect to host x:80 ssl:default "
                          "[Name or service not known]") == "domain does not resolve (no website yet)"
    assert friendly_error("Error: Connection failed: ... [Connect call failed ('127.0.0.1', 1)]") == "connection refused"
    assert friendly_error("Unexpected error\nError: Request timed out: \n") == "timed out"
    assert friendly_error("nodename nor servname provided, or not known") == "domain does not resolve (no website yet)"
    assert friendly_error("Error: Something odd happened\nCode context: ...") == "Something odd happened"

    clf = RuleClassifier(settings.section("categories"), settings.section("qualify"))
    dfilter = DomainFilter(settings.section("domain_filter"))
    bakery = "<html><title>Bakery</title><body>" + "Fresh bread and cakes baked daily in Kandy. " * 30 + "</body></html>"
    flagged = FetchResult(url="http://b.lk/", mode="http", success=False, status=200, html=bakery,
                          error="possible bot protection")
    q = evaluate({"domain": "b.lk"}, flagged, clf, dfilter)
    assert (q.status, q.reason) == ("rejected", "no tech keywords")            # judged on content, not "unreachable"
    tiny = FetchResult(url="http://c.lk/", mode="http", success=False, status=200, html="<p>Just a moment...</p>",
                       error="possible bot protection")
    assert evaluate({"domain": "c.lk"}, tiny, clf, dfilter).status == "watch"

    dead = FetchResult(url="http://d.lk/", mode="http", success=False, error="domain does not resolve (no website yet)")
    assert not needs_browser(dead, 80)                                          # no pointless browser retry
    assert not needs_browser(FetchResult(url="u", mode="http", success=False, status=403), 80)   # a refusal: back off
    assert not needs_browser(FetchResult(url="u", mode="http", success=False, status=429), 80)
    assert needs_browser(FetchResult(url="u", mode="http", success=True, status=200, html="<div id=app></div>"), 80)
