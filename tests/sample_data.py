"""Realistic sample data written through the real stores (for dashboard tests and screenshots)."""

import random
from datetime import datetime, timedelta, timezone

from techintel.store import TechStore
from worldnews.store import NewsStore
from worldnews.tagging import TechTagger, TopicTagger


def iso(dt: datetime, micro: bool = False) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ" if micro else "%Y-%m-%dT%H:%M:%SZ")


NEWS = {
    ("CNBC", "US"): ["Stocks climb as investors weigh Fed rate path", "Nvidia shares jump after new AI chip unveiled",
                     "Oil prices slip on weaker demand outlook", "Retail sales beat expectations in October"],
    ("NBC News", "US"): ["Storm system moves across the Midwest", "Senate passes funding bill late Sunday",
                         "Apple faces new antitrust case in federal court"],
    ("Times of India", "IN"): ["Parliament winter session to begin next week", "Infosys signs multi-year cloud deal",
                               "Mumbai metro line opens to commuters"],
    ("The Hindu", "IN"): ["ISRO readies next satellite launch", "Monsoon rainfall ends above normal"],
    ("Ada Derana", "LK"): ["Cabinet approves new tax measures", "Colombo port expansion enters second phase",
                           "Heavy rain warning for Western Province"],
    ("Daily Mirror", "LK"): ["CBSL keeps policy rates unchanged", "Tourist arrivals cross two million mark",
                             "Digital ID rollout begins in Colombo"],
    ("ABC News Australia", "AU"): ["Reserve Bank holds cash rate steady", "Bushfire season outlook released"],
    ("CBC News", "CA"): ["Ottawa unveils housing plan", "Wildfire smoke advisory lifted in Alberta"],
    ("South China Morning Post", "CN"): ["Huawei launches new smartphone chip", "Beijing announces stimulus package"],
    ("BBC World", "GLOBAL"): ["UN climate talks open in Brazil", "Ceasefire talks resume in Cairo"],
    ("TechCrunch", "TECH"): ["AI startup raises $500M for reasoning models", "Quantum-safe messaging app launches",
                             "EV maker cuts prices across its lineup"],
    ("The Verge", "TECH"): ["Hands-on with the new foldable phone", "Streaming service adds live sports tier"],
}


def build_news(root, tech_keywords: list[str], now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    rnd = random.Random(7)
    tagger = TopicTagger(tech_keywords) if isinstance(tech_keywords, dict) else TechTagger(tech_keywords)
    store = NewsStore(root)
    for run_no, run_time in enumerate([now - timedelta(days=1, hours=2), now - timedelta(minutes=30)]):
        sources = []
        new_items = []
        known = store.known_urls()
        for (name, group), titles in NEWS.items():
            items = []
            subset = titles if run_no else titles[1:]          # first run misses the newest story of each source
            for n, title in enumerate(subset):
                published = run_time - timedelta(minutes=20 + n * rnd.randint(40, 140))
                item = {"url": f"https://example.com/{name.lower().replace(' ', '-')}/{abs(hash(title)) % 10**8}",
                        "title": title, "summary": f"{title}. Short summary from the source.", "published_at": iso(published),
                        "source": name, "group": group, "rank": n + 1, "collected_at": iso(run_time + timedelta(seconds=30))}
                tagger.tag(item, group)
                items.append(item)
                if item["url"] not in known:
                    new_items.append(item)
            sources.append({"name": name, "group": group, "homepage": "https://example.com/", "status": "ok",
                            "via": "feed", "items": items})
        sources.append({"name": "Reuters", "group": "GLOBAL", "homepage": "https://www.reuters.com/", "status": "failed",
                        "via": None, "error": "homepage: HTTP 401", "items": []})
        store.append_items(new_items)
        store.save_snapshot({"run_at": iso(run_time, micro=True), "finished_at": iso(run_time + timedelta(seconds=95)),
                             "limit": 10, "sources": sources,
                             "totals": {"sources": len(sources), "ok": len(sources) - 1,
                                        "headlines": sum(len(s["items"]) for s in sources), "new": len(new_items)}})
    store.close()


SITES = [
    ("vectorlake.ai", "hackernews", "qualified", "data_analytics", 52.0, "Serverless vector database for RAG apps", ["Next.js", "Vercel", "Stripe"]),
    ("shieldops.lk", "crtsh", "qualified", "cybersecurity", 18.0, "Managed SOC and zero-trust security for SMEs", ["WordPress", "Cloudflare"]),
    ("payflow.io", "hackernews", "qualified", "fintech", 26.0, "Payment gateway API for South Asian merchants", ["React", "Stripe", "Cloudflare"]),
    ("agentforge.dev", "certstream", "qualified", "ai_ml", 61.5, "Framework for building reliable AI agents", ["Next.js", "Vercel", "PostHog"]),
    ("quantix.app", "certstream", "watch", None, 0.0, None, []),
    ("kandy-bakery.lk", "crtsh", "rejected", None, 0.0, None, []),
    ("buy-this-domain.ai", "certstream", "rejected", None, 0.0, None, []),
    ("cloudnest.lk", "crtsh", "qualified", "cloud_infra", 14.2, "Local cloud hosting and Kubernetes for Sri Lanka", ["Nginx", "Bootstrap"]),
    ("newproject.io", "hackernews", None, None, None, None, []),
]
REASONS = {"watch": "placeholder: 'coming soon'", "rejected": "no tech keywords", "qualified": "tech score"}


def build_tech(root, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    store = TechStore(root)
    for i, (domain, source, status, category, score, desc, tech) in enumerate(SITES):
        found = now - timedelta(hours=3)
        seen = now - timedelta(hours=5 + i * 9)
        store.append("candidates", [{"domain": domain, "url": f"https://{domain}/", "source": source,
                                     "discovered_at": iso(found), "signal_at": iso(seen),
                                     "detail": {"title": f"Show HN: {domain.split('.')[0].title()}"} if source == "hackernews" else {}}])
        if status:
            reason = "parked: 'domain is for sale'" if domain.startswith("buy-") else REASONS[status]
            store.append("qualifications", [{"domain": domain, "url": f"https://{domain}/", "final_url": f"https://www.{domain}/",
                                             "status": status, "reason": reason, "category": category, "score": score,
                                             "title": domain.split(".")[0].title(), "description": desc, "checked_at": iso(found)}])
        if status == "qualified":
            if not domain.endswith(".lk"):
                store.append("registrations", [{"domain": domain, "registered_at": iso(now - timedelta(days=20 + i * 40)),
                                                "registrar": "Example Registrar", "checked_at": iso(found)}])
            store.append("profiles", [{"domain": domain, "url": f"https://{domain}/", "status": "ok", "kind": "initial",
                                       "name": domain.split(".")[0].title(), "description": desc, "category": category,
                                       "tech_stack": tech, "signals": {"has_pricing": True, "has_docs": i % 2 == 0, "hiring": i % 3 == 0,
                                                                       "open_source": "Vercel" in tech},
                                       "key_pages": {"pricing": f"https://{domain}/pricing", "about": f"https://{domain}/about"},
                                       "keywords": ["platform", "api", "developers"], "profiled_at": iso(found)}])
    store.append("changes", [
        {"domain": "agentforge.dev", "change_type": "tech_added", "field": "tech_stack", "after": "Stripe", "detected_at": iso(now - timedelta(hours=1))},
        {"domain": "payflow.io", "change_type": "signal_on", "field": "hiring", "before": False, "after": True, "detected_at": iso(now - timedelta(hours=2))}])
    store.append("companies", [{"domain": "agentforge.dev", "status": "ok", "checked_at": iso(now), "name": "AgentForge",
                                "social": {"linkedin": "https://www.linkedin.com/company/agentforge/", "x": "https://x.com/agentforge",
                                           "github": "https://github.com/agentforge"},
                                "people": [{"name": "Nimal Perera", "role": "Co-founder & CEO"}], "employees": "11-50",
                                "emails": ["hello@agentforge.dev"], "phones": [], "linkedin_people": [],
                                "github": {"login": "agentforge", "public_repos": 12, "followers": 40}},
                               {"domain": "payflow.io", "status": "ok", "checked_at": iso(now), "name": "Payflow",
                                "social": {"facebook": "https://facebook.com/payflow"}, "people": [], "emails": [],
                                "phones": [], "linkedin_people": []}])
    store.append("runs", [{"stage": s, "status": "ok", "finished_at": iso(now - timedelta(hours=1)), "summary": {"ok": True}}
                          for s in ("discover", "qualify", "enrich", "profile", "monitor", "report")])
    store.close()


def build_markets(root, now: datetime | None = None, runs: int = 6) -> None:
    """Several runs of realistic prices, so history and sparklines have points."""
    from markets.collect import lkr_metal_prices
    from markets.providers import quote
    from markets.store import MarketStore
    now = now or datetime.now(timezone.utc)
    rnd = random.Random(3)
    base = {("index", "^GSPC", "S&P 500", "USD"): 5728.8, ("index", "^IXIC", "Nasdaq Composite", "USD"): 18200.4,
            ("index", "^DJI", "Dow Jones", "USD"): 42114.4, ("index", "^VIX", "VIX (volatility)", "USD"): 16.2,
            ("index", "ASPI", "All Share Price Index (ASPI)", "LKR"): 20364.97, ("index", "S&P SL20", "S&P Sri Lanka 20", "LKR"): 5686.82,
            ("stock_us", "AAPL", "Apple Inc.", "USD"): 225.1, ("stock_us", "NVDA", "NVIDIA Corporation", "USD"): 138.4,
            ("stock_us", "MSFT", "Microsoft Corporation", "USD"): 410.3, ("stock_us", "TSLA", "Tesla, Inc.", "USD"): 248.9,
            ("stock_lk", "JKH.N0000", "John Keells Holdings PLC", "LKR"): 21.4, ("stock_lk", "COMB.N0000", "Commercial Bank Of Ceylon PLC", "LKR"): 140.25,
            ("stock_lk", "DIAL.N0000", "Dialog Axiata PLC", "LKR"): 13.6, ("stock_lk", "ABAN.N0000", "Abans Electricals PLC", "LKR"): 1020.5,
            ("commodity", "GC=F", "Gold", "USD"): 2745.6, ("commodity", "SI=F", "Silver", "USD"): 32.6,
            ("commodity", "CL=F", "Crude oil (WTI)", "USD"): 69.5, ("fx", "LKR=X", "USD/LKR", "LKR"): 293.4,
            ("fx", "EURUSD=X", "EUR/USD", "USD"): 1.083}
    coins = [("BTC", "Bitcoin", 101234.5, 2.0e12), ("ETH", "Ethereum", 3900.1, 4.7e11), ("USDT", "Tether", 1.0, 1.2e11),
             ("SOL", "Solana", 212.3, 1.0e11), ("BNB", "BNB", 690.2, 9.9e10), ("XRP", "XRP", 2.31, 1.3e11)]
    store = MarketStore(root)
    prev = {k: v for k, v in base.items()}
    for n in range(runs):
        run_at = now - timedelta(hours=(runs - 1 - n) * 4)
        quotes = []
        for (cls, sym, name, cur), _ in base.items():
            p0 = prev[(cls, sym, name, cur)]
            p1 = p0 * (1 + rnd.uniform(-0.015, 0.015))
            extra = {"metal": "gold" if sym == "GC=F" else "silver"} if sym in ("GC=F", "SI=F") else ({"lkr_rate": True} if sym == "LKR=X" else {})
            if sym == "^GSPC":                        # Yahoo's daily closes: 120 days, rising 0.1% a day
                extra["daily"] = [[(now - timedelta(days=120 - d)).strftime("%Y-%m-%d"), round(5000 * 1.001 ** d, 2)]
                                  for d in range(120)]
            quotes.append(quote(cls, sym, name, p1, prev_close=p0, currency=cur, volume=rnd.randint(1000, 9_000_000),
                                market_cap=p1 * 1e9 if cls.startswith("stock") else None, as_of=iso(run_at), source="sample", extra=extra))
            prev[(cls, sym, name, cur)] = p1
        for rank, (sym, name, px, cap) in enumerate(coins, 1):
            quotes.append(quote("crypto", sym, name, px * (1 + rnd.uniform(-0.03, 0.03)), change_pct=rnd.uniform(-5, 5),
                                currency="USD", volume=cap / 40, market_cap=cap, rank=rank, as_of=iso(run_at), source="sample",
                                extra={"pct_1h": rnd.uniform(-1, 1), "pct_7d": rnd.uniform(-10, 10)}))
        quotes += lkr_metal_prices(quotes)
        sources = [{"source": "yahoo", "group": "us", "ok": True, "count": 10, "seconds": 1.2},
                   {"source": "cse companies", "group": "lk", "ok": True, "count": 4, "seconds": 0.8},
                   {"source": "crypto top", "group": "crypto", "ok": True, "count": 6, "seconds": 0.9},
                   {"source": "yahoo BRK-B", "group": "us", "ok": False, "count": 0, "error": "BRK-B: HTTP 404", "seconds": 0.3}]
        store.save_run(iso(run_at, micro=True), iso(run_at + timedelta(seconds=20)), quotes, sources)
    store.close()


def build_vehicles(runs: int = 3) -> None:
    """Saved through the vehicles database (as the real scraper does): a few runs, new ads, a price drop.
    Uses PROJECT_DATA_DIR from the environment."""
    import importlib
    import time as _time
    import project_paths
    import warehouse
    for mod in (project_paths, warehouse):
        importlib.reload(mod)
    from vehicles.store import VehicleStore
    ikman = [("Toyota Aqua 2015", "Colombo", 6_450_000), ("Honda Vezel 2016", "Gampaha", 9_350_000),
             ("Suzuki Alto 2012", "Kandy", 2_650_000), ("Nissan Leaf 2014", "Colombo", 4_150_000)]
    riya = [("Toyota Vitz KSP90 2008", "Nugegoda", 4_890_000), ("Suzuki Wagon R FX 2014", "Ambalangoda", 4_200_000),
            ("Mitsubishi Montero 2010", "Kurunegala", 21_500_000)]
    for n in range(runs):
        cars_i = ikman[: 2 + n]                      # a new ad appears on each run
        ads_i = [{"rank": i + 1, "title": t, "url": f"https://ikman.lk/en/ad/{t.lower().replace(' ', '-')}",
                  "price": f"Rs {p:,}", "price_lkr": p - (250_000 if (t.startswith("Honda") and n == runs - 1) else 0),
                  "location": loc, "posted": f"{5 + i * 7} mins", "image": None} for i, (t, loc, p) in enumerate(cars_i)]
        ads_r = [{"rank": i + 1, "title": t, "url": f"https://riyasewana.com/buy/{t.lower().replace(' ', '-')}-{1000 + i}",
                  "price": f"Rs. {p:,}", "price_lkr": p, "year": t.split()[-1], "mileage": f"{80 + i * 15},000 km",
                  "posted": "2026-11-02", "image": None} for i, (t, _, p) in enumerate(riya[: 1 + n])]
        store = VehicleStore()
        store.save_report({"site": "ikman", "success": True, "count": len(ads_i), "ads": ads_i})
        store.save_report({"site": "riyasewana", "success": True, "count": len(ads_r), "ads": ads_r})
        store.close()
        _time.sleep(0.02)
