"""Realistic sample data written through the real stores (for dashboard tests and screenshots)."""

import random
from datetime import datetime, timedelta, timezone

from techintel.store import DuckStore
from worldnews.store import NewsStore
from worldnews.tagging import TechTagger


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
    tagger = TechTagger(tech_keywords)
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
    store = DuckStore(root)
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
    store.append("runs", [{"stage": s, "status": "ok", "finished_at": iso(now - timedelta(hours=1)), "summary": {"ok": True}}
                          for s in ("discover", "qualify", "enrich", "profile", "monitor", "report")])
    store.close()
