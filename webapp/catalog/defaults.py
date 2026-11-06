"""Starting labels for the datasets we know, so the first scan gives readable names. The scan copes without an entry:
an unknown table gets a label from its name and stays with administrators until they grant it to a role.

off: internal bookkeeping (cursors, run logs) or the raw table behind a typed view; an administrator can turn it on.
key: what identifies a row in a view (tables use their primary key).
sort: the first order people want.
hide: columns that are in the way at first (they stay under Columns).
private: known, but stays with administrators until a role is granted it.
"""

STARTING_ROLES_VIEW = ("Editor", "Analyst", "Viewer")
STARTING_ROLES_EXPORT = ("Editor", "Analyst")
STARTING_ROLES_EDIT = ("Editor",)

KNOWN = {
    "news.news_items": dict(label="Headlines", description="Every headline collected, once per link.",
                            sort=("first_seen_at", "desc"), hide=["id", "url", "image"]),
    "news.news_runs": dict(label="News runs", description="One row per news collection run.", sort=("run_at", "desc")),
    "news.news_source_runs": dict(label="News source health", description="How each news source did in each run.",
                                  sort=("run_at", "desc")),
    "news.news_run_items": dict(label="Headlines per run", off=True),
    "news.feed_cache": dict(label="Feed addresses", off=True),
    "tech.v_candidates": dict(label="New tech domains", description="Domains found from certificate logs and Hacker News.",
                              key=["row_id"], sort=("discovered_at", "desc"), hide=["row_id"]),
    "tech.v_qualifications": dict(label="Tech site checks", description="Latest check per domain: a real tech site or not.",
                                  key=["domain"], sort=("checked_at", "desc")),
    "tech.v_registrations": dict(label="Domain registrations", description="When each domain was registered, and where.",
                                 key=["domain"], sort=("registered_at", "desc")),
    "tech.v_profiles": dict(label="Tech site profiles", description="What each site is about, from reading it.",
                            key=["domain"], sort=("profiled_at", "desc")),
    "tech.v_changes": dict(label="Tech site changes", description="Changes noticed when re-checking sites.",
                           sort=("detected_at", "desc")),
    "tech.v_companies": dict(label="Tech companies", description="Company facts from each tech site's own pages.",
                             key=["domain"], sort=("checked_at", "desc")),
    **{f"tech.{t}": dict(off=True) for t in ("candidates", "qualifications", "registrations", "profiles", "changes",
                                             "companies", "runs")},
    "markets.v_latest": dict(label="Latest prices", description="The latest price of every instrument.",
                             key=["asset_class", "symbol"], sort=("asset_class", "asc")),
    "markets.quotes": dict(label="Price history", description="Every price from every run.", sort=("run_at", "desc"), hide=["extra"]),
    "markets.v_daily": dict(label="Daily prices", description="The last price of each day per instrument.",
                            key=["asset_class", "symbol", "day"], sort=("day", "desc")),
    "markets.stoch_rsi": dict(label="Stochastic RSI", description="Latest Stochastic RSI per CSE share and coin, 0-100: RSI 14, Stochastic 14, %K 3, %D 3, on daily closes. "
                              "%K above 80 is overbought, below 20 oversold. Sources: cse.lk; powered by CoinPaprika. Numbers only, not advice.",
                              sort=("pct_k", "desc"), hide=["closes_needed", "status", "computed_at"]),
    "markets.daily_closes": dict(label="Daily closes", description="One close per day per CSE share and coin, from cse.lk and CoinPaprika (the price at 00:00 UTC).",
                                 sort=("day", "desc"), hide=["fetched_at"]),
    "markets.history_sync": dict(label="Daily closes progress", off=True),
    "markets.history_refusals": dict(label="Daily closes refusals", off=True),
    "markets.runs": dict(label="Market runs", off=True),
    "markets.source_runs": dict(label="Market source health", off=True),
    "foresight.v_forecasts": dict(label="Forecasts", description="Scenarios with odds, saved before the outcome and scored afterwards. Not promises, and not advice.",
                                  key=["id"], sort=("as_of", "desc"), hide=["method", "method_version", "reasons", "made_at", "normal_score"], private=True),
    "foresight.v_track_record": dict(label="Forecast track record", description="Live forecasts only: how they scored against the simple guess. Skill above 0 beats it.",
                                     key=["section", "grp", "question", "method", "method_version"], private=True),
    "foresight.backtests": dict(label="Forecast tests on the past", description="How each method would have done on past data. Kept apart from the live record, because a test can flatter.",
                                sort=("run_at", "desc"), hide=["details", "id"], private=True),
    **{f"foresight.{t}": dict(off=True) for t in ("forecasts", "outcomes", "signals", "runs")},
    "vehicles.v_vehicle_ads_latest": dict(label="Vehicle ads", description="One row per ad: latest details, first seen, times seen.",
                                          key=["url"], sort=("first_seen", "desc")),
    "vehicles.v_vehicle_price_changes": dict(label="Vehicle price changes", description="Ads whose price changed between runs.",
                                             sort=("saved_at", "desc")),
    "vehicles.ads": dict(label="Vehicle ad history", description="Every ad seen in every run.", sort=("saved_at", "desc")),
    "vehicles.v_vehicle_ads": dict(off=True),
    "vehicles.runs": dict(label="Vehicle runs", off=True),
    "vehicles.meta": dict(off=True),
    "companies.v_companies": dict(label="Companies", description="Companies from every source, with their website check.",
                                  key=["id"], sort=("name", "asc"), hide=["id", "source_id", "last_seen"]),
    "companies.companies": dict(label="Company records", description="One row per company per source, with the source's own fields.",
                                sort=("name", "asc"), hide=["id", "source_id", "last_seen"]),
    "companies.sites": dict(label="Company websites", description="What each company's own website says.",
                            sort=("checked_at", "desc")),
    "companies.refusals": dict(label="Websites that refused us", off=True),
    "companies.cursors": dict(off=True),
    "companies.runs": dict(label="Company runs", off=True),
    "scrapers.scraper_rows": dict(label="Scraper results", description="Every record the older scrapers saved.",
                                  sort=("saved_at", "desc")),
    "scrapers.v_news_headlines": dict(label="Sri Lankan headlines", description="Headlines from the Sri Lankan news scraper.",
                                      sort=("saved_at", "desc")),
    "scrapers.v_products": dict(label="Products", description="Product listings per run.", sort=("saved_at", "desc")),
}
