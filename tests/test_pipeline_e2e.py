"""End-to-end: the whole pipeline against fake websites served on localhost.

Sites (each on its own port):
  tech     an AI startup with key pages, Next.js + Stripe, links to GitHub/X and a partner site
  partner  a data-analytics site, only linked from `tech` (found later via outlinks)
  parked   "this domain is for sale"
  bakery   a real site, but not tech
  soon     "coming soon" placeholder
  blocked  tech content, but robots.txt disallows crawlers
Then `tech` changes (starts hiring, adds Intercom, new description) and monitor must notice.
"""

import asyncio
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from techintel.cli import run_stage

FILLER = ("Our team builds reliable products for engineering teams across Asia, with clear documentation, "
          "responsive support and transparent roadmaps that customers can follow week by week. ")


def page(title, body, head="", description=""):
    return (f"<html><head><title>{title}</title><meta name='description' content='{description}'>{head}</head>"
            f"<body>{body}</body></html>")


class Site:
    def __init__(self, pages: dict, headers: dict | None = None):
        self.pages = pages            # path -> html (mutable: tests change it)
        self.headers = headers or {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, self))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def domain(self):
        return f"127.0.0.1:{self.port}"


class Handler(BaseHTTPRequestHandler):
    def __init__(self, site, *args, **kwargs):
        self.site = site
        super().__init__(*args, **kwargs)

    def do_GET(self):
        body = self.site.pages.get(self.path.split("?")[0])
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain" if self.path == "/robots.txt" else "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in self.site.headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def sites():
    partner = Site({"/": page("DataLoom: real-time analytics", FILLER * 3 +
                              "<h2>Data platform</h2>Real-time data pipeline, data warehouse and business intelligence "
                              "dashboards. Analytics for every team, with SQL and ETL built in.",
                              description="Analytics and data platform")})
    tech_home = lambda extra_head="", desc="LLM inference platform for developers": page(  # noqa: E731
        "NeuraStack | AI inference platform",
        "<h1>Run large language model inference at scale</h1><h2>AI agents for developers</h2>"
        "<p>" + FILLER * 2 + "Machine learning infrastructure with an API and SDK. Fine-tuning, RAG and embeddings. "
        "Generative AI for every product.</p>"
        "<a href='/about'>About</a> <a href='/pricing'>Pricing</a> <a href='/docs'>Docs</a> "
        "<a href='/blog'>Blog</a> <a href='/careers'>Careers</a> "
        "<a href='https://github.com/neurastack'>GitHub</a> <a href='https://x.com/neurastack'>X</a> "
        f"<a href='http://localhost:{partner.port}/'>Our analytics partner</a>",
        head="<script id='__NEXT_DATA__' type='application/json'>{}</script>"
             "<script src='https://js.stripe.com/v3/'></script>" + extra_head,
        description=desc)
    tech = Site({
        "/": tech_home(),
        "/about": page("About NeuraStack", "<h1>About</h1>" + FILLER * 2),
        "/pricing": page("Pricing", "<h1>Plans</h1>Free, Pro and Enterprise." + FILLER),
        "/docs": page("Docs", "<h1>API reference</h1>" + FILLER),
        "/blog": page("Blog", "<h1>Updates</h1>" + FILLER),
        "/careers": page("Careers", "<h1>Join us</h1>No open roles right now." + FILLER),
    }, headers={"Server": "cloudflare", "CF-Ray": "abc123"})
    tech.home = tech_home
    parked = Site({"/": page("neura.ai", "This domain is for sale! Buy this domain now. Related searches: AI, cloud.")})
    bakery = Site({"/": page("Grandma's Bakery", "<h1>Fresh bread daily</h1>" +
                             "Cakes, bread and pastries baked every morning in our family bakery in Kandy. " * 12)})
    soon = Site({"/": page("Quantix", "<h1>Coming soon</h1>Something new is launching. Stay tuned.")})
    blocked = Site({"/robots.txt": "User-agent: *\nDisallow: /\n",
                    "/": page("SecretAI", "<h1>AI platform</h1>" + FILLER * 3 + "machine learning llm api sdk")})
    yield {"tech": tech, "partner": partner, "parked": parked, "bakery": bakery, "soon": soon, "blocked": blocked}
    for s in (tech, partner, parked, bakery, soon, blocked):
        s.server.shutdown()


def test_full_pipeline(settings, store, sites):
    seeds = [f"http://{s.domain}/" for name, s in sites.items() if name != "partner"]
    (settings.config_dir / "seeds.txt").write_text("\n".join(seeds))

    async def go():
        # 1. discover (seeds only: no internet needed)
        d = await run_stage("discover", settings, store, sources=["seeds"])
        assert d["new_candidates"] == 5
        assert (await run_stage("discover", settings, store, sources=["seeds"]))["new_candidates"] == 0  # idempotent

        # 2. qualify
        q = await run_stage("qualify", settings, store)
        quals = store.latest("qualifications")
        status = {name: quals[s.domain]["status"] for name, s in sites.items() if name != "partner"}
        assert status == {"tech": "qualified", "parked": "rejected", "bakery": "rejected",
                          "soon": "watch", "blocked": "rejected"}, {n: quals[s.domain]["reason"] for n, s in sites.items() if s.domain in quals}
        assert quals[sites["tech"].domain]["category"] == "ai_ml"
        assert "robots" in quals[sites["blocked"].domain]["reason"]
        assert q["checked"] == 5 and (await run_stage("qualify", settings, store))["checked"] == 0

        # 3. profile
        p = await run_stage("profile", settings, store)
        assert p == {"profiled": 1, "ok": 1, "unreachable": 0}
        prof = store.latest("profiles")[sites["tech"].domain]
        assert set(prof["key_pages"]) == {"about", "pricing", "docs", "blog", "careers"}
        assert {"Next.js", "Stripe", "Cloudflare"} <= set(prof["tech_stack"])
        assert prof["signals"] == {"has_pricing": True, "has_docs": True, "has_blog": True,
                                   "has_careers": True, "hiring": False, "open_source": True}
        assert prof["social"].keys() >= {"github", "x"}
        assert sites["partner"].domain.replace("127.0.0.1", "localhost") in prof["external_domains"]
        assert len(prof["pages"]) == 6 and all(pg.get("raw_file") for pg in prof["pages"])

        # 4. monitor: nothing changed yet -> no changes; then the site changes
        assert (await run_stage("monitor", settings, store, force=True))["changes"] == 0
        tech = sites["tech"]
        tech.pages["/"] = tech.home("<script src='https://widget.intercom.io/widget/x'></script>",
                                    desc="AI agents platform for enterprises")
        tech.pages["/careers"] = page("Careers", "<h1>Open roles</h1>" + FILLER +
                                      "<a href='https://boards.greenhouse.io/neurastack/jobs/1'>ML Engineer</a>"
                                      "<a href='https://boards.greenhouse.io/neurastack/jobs/2'>Backend Engineer</a>")
        m = await run_stage("monitor", settings, store, force=True)
        changes = {(c["change_type"], c["field"]) for c in store.read("changes")}
        assert {("tech_added", "tech_stack"), ("signal_on", "hiring"), ("description_changed", "description"),
                ("job_links_changed", "hiring.job_links")} <= changes, changes
        assert m["changes"] >= 4

        # 5. outlinks: the partner site linked from `tech` becomes a candidate, and qualifies
        o = await run_stage("discover", settings, store, sources=["outlinks"])
        assert o["new_candidates"] == 1
        await run_stage("qualify", settings, store)
        partner_domain = sites["partner"].domain.replace("127.0.0.1", "localhost")
        assert store.latest("qualifications")[partner_domain]["category"] == "data_analytics"

        # 6. analyze + report
        await run_stage("analyze", settings, store, days=7)
        r = await run_stage("report", settings, store, days=7)
        return r

    r = asyncio.run(go())
    md = open(r["markdown"], encoding="utf-8").read()
    html = open(r["html"], encoding="utf-8").read()
    assert "NeuraStack" in md and "ai_ml" in md and "started using Intercom" in md and "now has hiring" in md
    assert "DataLoom" in md and "<h1>Tech Intelligence Report</h1>" in html
    assert {r["stage"] for r in store.read("runs")} == {"discover", "qualify", "profile", "monitor", "analyze", "report"}
