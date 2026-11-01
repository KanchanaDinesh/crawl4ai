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
