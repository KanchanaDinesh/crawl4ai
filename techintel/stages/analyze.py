"""Stage 5: ANALYZE everything collected so far into one summary.

Produces output/techintel/analysis-<date>.json, which the report stage renders.
`days` sets the reporting period ("new this week" etc.); totals are all-time.
"""

import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from ..domains import _extract, _is_local, homepage_url
from ..models import utc_now
from . import latest_ok_profiles, parse_time

logger = logging.getLogger(__name__)


def _in_period(value: str | None, since: datetime) -> bool:
    when = parse_time(value)
    return bool(when and when >= since)


def _tld(domain: str) -> str:
    if _is_local(domain):
        return "(local)"
    suffix = _extract(domain).suffix
    return f".{suffix}" if suffix else "(unknown)"


def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def analyze(store, days: int = 7, max_listed: int = 200, max_domains: int = 5000) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    candidates = list(store.read("candidates"))
    qualifications = store.latest("qualifications")
    profiles = latest_ok_profiles(store)
    changes = list(store.read("changes"))
    runs = list(store.read("runs"))
    cand_by_domain = {c["domain"]: c for c in candidates}
    registrations = store.latest("registrations")

    def dates(domain: str) -> dict:
        """The three dates we know for a domain.

        seen_at        when the source saw it: HN post time, certificate logged time
                       (falls back to found_at for sources without a date, e.g. seeds)
        registered_at  domain registration date (RDAP), if the registry provides it
        found_at       when this pipeline discovered it
        """
        c = cand_by_domain.get(domain, {})
        return {
            "seen_at": c.get("signal_at") or c.get("discovered_at"),
            "registered_at": registrations.get(domain, {}).get("registered_at"),
            "found_at": c.get("discovered_at"),
        }

    status_counts = Counter(q["status"] for q in qualifications.values())
    new_candidates = [c for c in candidates if _in_period(c["discovered_at"], since)]
    qualified = {d: q for d, q in qualifications.items() if q["status"] == "qualified"}
    new_qualified = {d: q for d, q in qualified.items() if _in_period(cand_by_domain.get(d, {}).get("discovered_at"), since)}

    # Rejection reasons, grouped by their first word(s) ("parked", "low tech score", ...)
    reasons = Counter(q["reason"].split(":")[0].split("(")[0].strip()
                      for q in qualifications.values() if q["status"] != "qualified")

    tech = Counter(t for p in profiles.values() for t in p.get("tech_stack", []))
    tech_groups = {t: g for p in profiles.values() for t, g in (p.get("tech_groups") or {}).items()}
    signals = Counter(name for p in profiles.values() for name, on in (p.get("signals") or {}).items() if on)
    new_profile_keywords = Counter(k for d, p in profiles.items() if d in new_qualified for k in p.get("keywords", []))

    def site_row(domain: str) -> dict:
        q, p, c = qualifications.get(domain, {}), profiles.get(domain, {}), cand_by_domain.get(domain, {})
        return {
            "domain": domain,
            "url": p.get("final_url") or q.get("final_url") or homepage_url(domain),
            "name": p.get("name") or q.get("title") or domain,
            "category": q.get("category"),
            "score": q.get("score"),
            "description": (p.get("description") or q.get("description") or "")[:300],
            "tech_stack": p.get("tech_stack", []),
            "signals": [k for k, v in (p.get("signals") or {}).items() if v],
            "keywords": p.get("keywords", [])[:8],
            "source": c.get("source"),
            "source_detail": c.get("detail", {}),
            "discovered_at": c.get("discovered_at"),
            **dates(domain),
            "profiled": domain in profiles,
        }

    # Latest first by when the site was seen online; ties broken by higher score.
    all_new_sites = sorted((site_row(d) for d in new_qualified),
                           key=lambda r: (r["seen_at"] or "", r["score"] or 0), reverse=True)
    new_sites = all_new_sites[:max_listed]

    # Every domain discovered in the period, whatever happened to it.
    def domain_row(c: dict) -> dict:
        q = qualifications.get(c["domain"], {})
        return {
            "domain": c["domain"],
            "url": q.get("final_url") or c.get("url") or homepage_url(c["domain"]),
            "status": q.get("status", "not checked"),
            "reason": q.get("reason", "waiting for the qualify stage"),
            "category": q.get("category"),
            "score": q.get("score"),
            "title": q.get("title"),
            "source": c.get("source"),
            "source_title": (c.get("detail") or {}).get("title"),
            **dates(c["domain"]),
        }
    all_domains = sorted((domain_row(c) for c in new_candidates), key=lambda r: r["seen_at"] or "", reverse=True)
    period_changes = [c for c in changes if _in_period(c["detected_at"], since)]
    change_types = Counter(c["change_type"] for c in period_changes)
    notable = sorted((c for c in period_changes if c["change_type"] != "content_changed"),
                     key=lambda c: c["detected_at"], reverse=True)[:max_listed]   # latest first

    last_runs: dict[str, dict] = {}
    source_health: dict[str, tuple[str, str]] = {}   # source -> (state, detail) from the last run that used it
    for r in runs:
        last_runs[r["stage"]] = r
        if r["stage"] == "discover":
            summary = r.get("summary") or {}
            for name, stats in (summary.get("sources") or {}).items():
                source_health[name] = ("empty", "") if stats.get("found", 0) == 0 else ("ok", "")
            for name, err in (summary.get("errors") or {}).items():
                source_health[name] = ("error", err)
    warnings = []
    for name, (state, detail) in sorted(source_health.items()):
        if state == "error":
            warnings.append(f"Source '{name}' failed in its last run: {detail[:150]}")
        elif state == "empty":
            warnings.append(f"Source '{name}' found nothing in its last run; check it still works.")
    for stage, r in last_runs.items():
        if r.get("status") == "failed":
            warnings.append(f"Stage '{stage}' failed in its last run: {(r.get('summary') or {}).get('error', '')[:150]}")

    total_checked = len(qualifications)
    return {
        "generated_at": utc_now(),
        "period_days": days,
        "period_start": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "totals": {
            "candidates": len(cand_by_domain),
            "checked": total_checked,
            "qualified": status_counts.get("qualified", 0),
            "watch": status_counts.get("watch", 0),
            "rejected": status_counts.get("rejected", 0),
            "profiled": len(profiles),
            "changes": len(changes),
            "qualification_rate_pct": _pct(status_counts.get("qualified", 0), total_checked),
        },
        "period": {
            "new_candidates": len(new_candidates),
            "new_qualified": len(new_qualified),
            "changes": len(period_changes),
            "candidates_by_source": dict(Counter(c["source"] for c in new_candidates).most_common()),
            "candidates_by_tld": dict(Counter(_tld(c["domain"]) for c in new_candidates).most_common(15)),
            "new_qualified_by_category": dict(Counter(q["category"] for q in new_qualified.values()).most_common()),
            "change_types": dict(change_types.most_common()),
            "top_keywords_new_sites": [k for k, _ in new_profile_keywords.most_common(25)],
        },
        "categories_all_time": dict(Counter(q["category"] for q in qualified.values()).most_common()),
        "not_qualified_reasons": dict(reasons.most_common(10)),
        "tech_stack": [{"name": t, "group": tech_groups.get(t), "sites": n, "pct": _pct(n, len(profiles))}
                       for t, n in tech.most_common(25)],
        "signals": {name: {"sites": n, "pct": _pct(n, len(profiles))} for name, n in signals.most_common()},
        "new_sites": new_sites,
        "new_sites_total": len(all_new_sites),
        "all_domains": all_domains[:max_domains],
        "all_domains_total": len(all_domains),
        "all_domains_by_status": dict(Counter(r["status"] for r in all_domains).most_common()),
        "notable_changes": notable,
        "pipeline": {stage: {"finished_at": r.get("finished_at"), "summary": r.get("summary")}
                     for stage, r in last_runs.items()},
        "warnings": warnings,
    }


async def run(settings, store, days: int | None = None, **_) -> dict:
    report_cfg = settings.section("report")
    days = days or int(report_cfg.get("default_days", 7))
    result = analyze(store, days, int(report_cfg.get("max_sites_listed", 200)),
                     int(report_cfg.get("max_domains_listed", 5000)))
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    path = settings.output_dir / f"analysis-{datetime.now(timezone.utc):%Y-%m-%d}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("analysis saved to %s", path)
    return {"file": str(path), "new_qualified": result["period"]["new_qualified"],
            "warnings": len(result["warnings"])}
