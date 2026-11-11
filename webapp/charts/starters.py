"""Ready-made dashboards, one per section, so the Charts menu starts with something useful (Kanchana, 2026-11-10).

They're ordinary charts and dashboards, kept as what was chosen (catalog field paths), like any made by hand: anyone may
change or delete them afterwards. `python -m webapp starter_charts` makes them. Each chart is worked out first, with its
owner's rights, and one that can't be drawn (a dataset that isn't there, a field renamed) is left out and named, so a
new server with only some tools gets the dashboards it can show. A dashboard that's already there is left as it is, so
running it again never doubles anything and never undoes someone's changes.
"""

from dataclasses import dataclass, field

from django.db import transaction

from webapp.audit.log import record
from webapp.catalog.models import Dataset
from webapp.explore.query import EMPTY

from .data import ChartProblem, chart_data
from .models import Chart, Dashboard, DashboardItem


@dataclass(frozen=True)
class Spec:
    name: str
    dataset: str                     # schema.name
    kind: str = Chart.COLUMN
    group: str = ""                  # catalog field paths
    unit: str = ""
    split: str = ""
    measure: str = Chart.COUNT
    of: str = ""
    filters: tuple = ()              # ((path, value), ...): a list is "one of these", text as the grid's filters
    limit: int = 12
    wide: bool = False
    lowest: bool = False             # smallest first


@dataclass(frozen=True)
class Board:
    name: str
    description: str
    charts: tuple


N, PIE, BAR, COL, LINE, AREA = Chart.NUMBER, Chart.PIE, Chart.BAR, Chart.COLUMN, Chart.LINE, Chart.AREA
RSI, CLOSES, LATEST = "markets.stoch_rsi", "markets.daily_closes", "markets.v_latest"
CSE, COINS, OK = ("asset_class", ["stock_lk"]), ("asset_class", ["crypto"]), ("status", ["ok"])
NEWS, FEEDS = "news.news_items", "news.news_source_runs"
DOMAINS, CHECKS, REGISTERED = "tech.v_candidates", "tech.v_qualifications", "tech.v_registrations"
COMPANIES, ADS, ARTICLES = "companies.v_companies", "vehicles.v_vehicle_ads_latest", "articles.v_articles"
WEEK = ("first_seen_at", "7d")
IKMAN = ("site", ["ikman"])          # riyasewana gives no location, and ikman no year of make

BOARDS = (
    Board("Markets", "Stochastic RSI for the CSE and the crypto top 100, prices and today's moves. Numbers, not advice.", (
        Spec("CSE shares overbought", RSI, N, filters=(CSE, ("zone", ["overbought"]))),
        Spec("CSE shares oversold", RSI, N, filters=(CSE, ("zone", ["oversold"]))),
        Spec("Coins overbought", RSI, N, filters=(COINS, ("zone", ["overbought"]))),
        Spec("Coins oversold", RSI, N, filters=(COINS, ("zone", ["oversold"]))),
        Spec("CSE: Stochastic RSI zones", RSI, PIE, group="zone", filters=(CSE, OK)),
        Spec("Crypto: Stochastic RSI zones", RSI, PIE, group="zone", filters=(COINS, OK)),
        Spec("CSE: oversold shares (%K)", RSI, Chart.TABLE, group="symbol", measure=Chart.MAX, of="pct_k", filters=(CSE, OK, ("zone", ["oversold"])), limit=15),
        Spec("Crypto: highest %K", RSI, BAR, group="symbol", measure=Chart.MAX, of="pct_k", filters=(COINS, OK), limit=15),
        Spec("Crypto: oversold coins (%K)", RSI, BAR, group="symbol", measure=Chart.MAX, of="pct_k", filters=(COINS, OK, ("zone", ["oversold"])), limit=15),
        Spec("CSE: lowest %K", RSI, BAR, group="symbol", measure=Chart.MAX, of="pct_k", filters=(CSE, OK), limit=15, lowest=True),
        Spec("Bitcoin: daily close (USD)", CLOSES, LINE, group="day", unit="day", measure=Chart.MAX, of="close", filters=(COINS, ("symbol", ["BTC"])), wide=True),
        Spec("John Keells (JKH): daily close (LKR)", CLOSES, LINE, group="day", unit="day", measure=Chart.MAX, of="close", filters=(CSE, ("symbol", ["JKH.N0000"])), wide=True),
        Spec("Crypto: share of market cap", LATEST, PIE, group="symbol", measure=Chart.SUM, of="market_cap", filters=(COINS,)),
        Spec("Crypto: biggest rises today (%)", LATEST, BAR, group="symbol", measure=Chart.MAX, of="change_pct", filters=(COINS,)),
        Spec("CSE: biggest rises today (%)", LATEST, BAR, group="symbol", measure=Chart.MAX, of="change_pct", filters=(CSE,)),
        Spec("Crypto: biggest falls today (%)", LATEST, BAR, group="symbol", measure=Chart.MIN, of="change_pct", filters=(COINS,), lowest=True),
        Spec("CSE: biggest falls today (%)", LATEST, BAR, group="symbol", measure=Chart.MIN, of="change_pct", filters=(CSE,), lowest=True),
    )),
    Board("World news", "What the news sites wrote: how much, from where and about what.", (
        Spec("Headlines in the last 7 days", NEWS, N, filters=(WEEK,)),
        Spec("Feeds that failed today", FEEDS, N, filters=(("status", ["failed"]), ("run_at", "1d"))),
        Spec("Headlines per day by country group", NEWS, AREA, group="first_seen_at", unit="day", split="group_code", filters=(("first_seen_at", "30d"),), wide=True),
        Spec("Headlines by country group (7 days)", NEWS, COL, group="group_code", filters=(WEEK,)),
        Spec("Topics (7 days)", NEWS, PIE, group="topics", filters=(WEEK,)),
        Spec("Busiest sources (7 days)", NEWS, BAR, group="source", filters=(WEEK,), limit=15),
        Spec("Most mentioned tech terms (7 days)", NEWS, BAR, group="tech_terms", filters=(WEEK,), limit=15),
        Spec("Failed feeds by source (7 days)", FEEDS, BAR, group="source", filters=(("status", ["failed"]), ("run_at", "7d"))),
    )),
    Board("Tech intel", "New tech websites: where they were found, what they are and when they were registered.", (
        Spec("New domains in the last 7 days", DOMAINS, N, filters=(("discovered_at", "7d"),)),
        Spec("Qualified sites", CHECKS, N, filters=(("status", ["qualified"]),)),
        Spec("New domains per day by source", DOMAINS, AREA, group="discovered_at", unit="day", split="source", filters=(("discovered_at", "30d"),), wide=True),
        Spec("Check results", CHECKS, PIE, group="status"),
        Spec("Qualified sites by category", CHECKS, BAR, group="category", filters=(("status", ["qualified"]),)),
        Spec("Average score by category", CHECKS, BAR, group="category", measure=Chart.AVG, of="score", filters=(("status", ["qualified"]),)),
        Spec("Domains registered per month (2 years)", REGISTERED, COL, group="registered_at", unit="month", filters=(("registered_at", "730d"),), wide=True),
        Spec("Top registrars", REGISTERED, BAR, group="registrar", filters=(("error", [EMPTY]),)),
    )),
    Board("Companies", "The company directory: where companies are, where they came from, and what their websites gave.", (
        Spec("Company records", COMPANIES, N),
        Spec("Websites read", COMPANIES, N, filters=(("site_status", ["ok"]),)),
        Spec("Companies by country (top 15)", COMPANIES, BAR, group="country", limit=15),
        Spec("Websites read by country (top 15)", COMPANIES, BAR, group="country", filters=(("site_status", ["ok"]),), limit=15),
        Spec("Records by source", COMPANIES, PIE, group="source"),
        Spec("Website status", COMPANIES, PIE, group="site_status"),
        Spec("New records per week", COMPANIES, COL, group="first_seen", unit="week", wide=True),
    )),
    Board("Vehicles", "The vehicle ads on ikman.lk and riyasewana.com today.", (
        Spec("Ads now", ADS, N),
        Spec("Average asking price (LKR)", ADS, N, measure=Chart.AVG, of="price_lkr"),
        Spec("Ads by site", ADS, PIE, group="site"),
        Spec("Top locations (ikman)", ADS, BAR, group="location", filters=(IKMAN,), limit=15),
        Spec("Average price by location (ikman, LKR)", ADS, BAR, group="location", measure=Chart.AVG, of="price_lkr", filters=(IKMAN,), limit=15),
        Spec("Ads by year of make (riyasewana)", ADS, COL, group="year", filters=(("site", ["riyasewana"]),), limit=20),
        Spec("New ads per day", ADS, COL, group="first_seen", unit="day", filters=(("first_seen", "30d"),), wide=True),
    )),
    Board("Articles", "Articles and blogs from the most valuable companies, by service, country and company.", (
        Spec("Articles in the last 30 days", ARTICLES, N, filters=(("published_at", "30d"),)),
        Spec("Articles per week by country (6 months)", ARTICLES, COL, group="published_at", unit="week", split="countries", filters=(("published_at", "180d"),), wide=True),
        Spec("Articles by service", ARTICLES, BAR, group="tags"),
        Spec("Most active companies", ARTICLES, BAR, group="company", limit=15),
        Spec("Articles by sector", ARTICLES, PIE, group="sector"),
    )),
)


@dataclass
class Outcome:
    made: dict = field(default_factory=dict)        # dashboard name -> number of charts
    kept: list = field(default_factory=list)        # dashboards already there, left alone
    left_out: list = field(default_factory=list)    # (dashboard, chart, why)
    empty: list = field(default_factory=list)       # (dashboard, chart): drawn, but no rows yet


def _dataset(ref: str) -> Dataset | None:
    schema, _, name = ref.partition(".")
    return Dataset.objects.filter(schema=schema, name=name, missing=False).first()


def _chart(spec: Spec, owner, shared: bool) -> Chart:
    ds = _dataset(spec.dataset)
    if ds is None:
        raise ChartProblem(f"the dataset {spec.dataset} isn't in the catalog (run catalog_scan, or that tool hasn't run yet)")
    return Chart(owner=owner, name=spec.name, dataset=ds, kind=spec.kind, group_field=spec.group, time_unit=spec.unit, split_field=spec.split,
                 measure=spec.measure, measure_field=spec.of, filters=[{"field": p, "value": v} for p, v in spec.filters], limit=spec.limit,
                 lowest_first=spec.lowest, shared=shared)


def _works(chart: Chart, owner) -> bool:
    """Raises ChartProblem if it can't be drawn; says whether it has anything to draw today."""
    data = chart_data(chart, owner)
    return data.number is not None if chart.kind == Chart.NUMBER else bool(data.categories)


def build(owner, *, shared: bool = True, save: bool = True, boards=BOARDS) -> Outcome:
    out = Outcome()
    for board in boards:
        if Dashboard.objects.filter(owner=owner, name=board.name).exists():
            out.kept.append(board.name)
            continue
        good = []
        for spec in board.charts:
            try:
                chart = _chart(spec, owner, shared)
                if not _works(chart, owner):
                    out.empty.append((board.name, spec.name))
            except ChartProblem as e:
                out.left_out.append((board.name, spec.name, str(e)))
                continue
            good.append((chart, spec.wide))
        if not good:
            continue
        out.made[board.name] = len(good)
        if save:
            _save(board, good, owner, shared)
    return out


@transaction.atomic
def _save(board: Board, charts: list, owner, shared: bool) -> None:
    dash = Dashboard.objects.create(owner=owner, name=board.name, description=board.description, shared=shared)
    for i, (chart, wide) in enumerate(charts):
        chart.save()
        DashboardItem.objects.create(dashboard=dash, chart=chart, order=(i + 1) * 10, wide=wide)
    record(None, "dashboard_saved", f"Ready-made dashboard “{dash.name}” made with {len(charts)} charts", target_type="dashboard", target_id=dash.pk,
           detail={"starter": True, "charts": len(charts)}, user=owner)
