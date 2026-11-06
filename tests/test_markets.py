"""markets: parsers on real/realistic responses, LKR metal prices, end-to-end against fake APIs."""

import asyncio
import json
from pathlib import Path

import pytest

from markets import collect
from markets.collect import lkr_metal_prices, run_collection
from markets.providers import crypto, cse, stooq, yahoo
from test_pipeline_e2e import Site

FIX = Path(__file__).parent / "fixtures"


MARKET_TIME = 1762113600                      # 2025-11-02 20:00 UTC
DAY = 86400


def chart(symbol, closes, price, currency="USD", name=None, today_included=True):
    """Yahoo's answer: one close per day, the last one on the latest price's day (unless it isn't in yet)."""
    last_day = MARKET_TIME - MARKET_TIME % DAY - (0 if today_included else DAY)
    stamps = [last_day - (len(closes) - 1 - i) * DAY + 14 * 3600 for i in range(len(closes))]
    return {"chart": {"result": [{"meta": {"symbol": symbol, "currency": currency, "regularMarketPrice": price,
                                           "regularMarketTime": MARKET_TIME, "longName": name or symbol,
                                           "regularMarketVolume": 1000, "exchangeName": "X", "gmtoffset": 0},
                                  "timestamp": stamps,
                                  "indicators": {"quote": [{"close": closes}]}}], "error": None}}


COINGECKO = [{"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "current_price": 101234.5, "market_cap": 2.0e12,
              "market_cap_rank": 1, "total_volume": 4.1e10, "high_24h": 102000, "low_24h": 99000,
              "price_change_24h": 1234.5, "price_change_percentage_24h": 1.23, "last_updated": "2026-11-02T10:00:00.000Z",
              "price_change_percentage_1h_in_currency": 0.1, "price_change_percentage_24h_in_currency": 1.23,
              "price_change_percentage_7d_in_currency": -2.5, "image": "https://x/btc.png"},
             {"id": "ethereum", "symbol": "eth", "name": "Ethereum", "current_price": 3900.1, "market_cap": 4.7e11,
              "market_cap_rank": 2, "total_volume": 2.0e10, "price_change_percentage_24h": -0.5,
              "price_change_percentage_24h_in_currency": -0.5, "last_updated": "2026-11-02T10:00:00.000Z"}]
PAPRIKA = [{"id": "eth-ethereum", "name": "Ethereum", "symbol": "ETH", "rank": 2, "last_updated": "2026-11-02T10:00:00Z",
            "quotes": {"USD": {"price": 3900.1, "volume_24h": 2.0e10, "market_cap": 4.7e11, "percent_change_1h": 0.1,
                               "percent_change_24h": -0.5, "percent_change_7d": 3.0}}},
           {"id": "btc-bitcoin", "name": "Bitcoin", "symbol": "BTC", "rank": 1, "last_updated": "2026-11-02T10:00:00Z",
            "quotes": {"USD": {"price": 101234.5, "volume_24h": 4.1e10, "market_cap": 2.0e12, "percent_change_24h": 1.23}}},
           {"id": "x-nr", "name": "Unranked", "symbol": "NR", "rank": 0, "quotes": {"USD": {"price": 1}}}]


def test_yahoo_parse_uses_previous_close_and_sparkline():
    q = yahoo.parse_chart(chart("^GSPC", [5600.0, None, 5650.0, 5700.0, 5728.8], 5728.8, name="S&P 500"), "index")
    assert (q["symbol"], q["name"], q["price"], q["prev_close"]) == ("^GSPC", "S&P 500", 5728.8, 5700.0)
    assert q["change"] == pytest.approx(28.8) and q["change_pct"] == pytest.approx(0.505263, rel=1e-4)
    assert q["extra"]["spark"] == [5600.0, 5650.0, 5700.0, 5728.8] and q["as_of"] == "2025-11-02T20:00:00Z"
    with pytest.raises(ValueError):
        yahoo.parse_chart({"chart": {"result": None, "error": {"description": "No data found"}}}, "index")


def test_yahoo_previous_close_when_today_isnt_in_the_closes_yet():
    """USD/LKR once compared today with the day before yesterday: today's close wasn't in Yahoo's list."""
    q = yahoo.parse_chart(chart("LKR=X", [322.51, 322.45, 322.48, 322.62], 332.58, "LKR", today_included=False), "fx")
    assert q["prev_close"] == 322.62 and q["change_pct"] == pytest.approx(3.087, rel=1e-3)
    assert q["extra"]["spark"][-1] == 332.58                       # today's price is shown at the end


def test_cse_live_index_replaces_yesterdays_summary():
    """During trading the daily summary still shows yesterday: aspiData / snpData have today's value."""
    aspi = cse.parse_market_summary(json.loads((FIX / "cse_daily_market_summary.json").read_text()))[0]
    later = 1774204200000 + 86400000 + 4 * 3600000                                   # the next day, mid-session
    live = {"value": 20956.9, "change": 591.93, "percentage": 2.906, "highValue": 20960.0, "lowValue": 20364.97,
            "timestamp": later}
    now = cse.apply_live(aspi, live, "Regular Trading")
    assert now["price"] == 20956.9 and now["prev_close"] == pytest.approx(20364.97) and now["change_pct"] == 2.906
    assert now["as_of"] > aspi["as_of"] and now["extra"]["market_status"] == "Regular Trading"
    assert now["extra"]["per"] == 10.3 and now["extra"]["summary_date"] == aspi["as_of"]
    assert cse.apply_live(aspi, None) is aspi                                         # live failed: keep the summary
    assert cse.apply_live(aspi, {**live, "timestamp": 1774204200000 - 86400000}) is aspi   # older than the summary


@pytest.fixture
def pg(pg_env):
    """Markets are saved in the test PostgreSQL (a database of this test's own)."""
    from warehouse import postgres
    postgres.setup()
    return pg_env


def test_old_sl20_rows_are_relabelled_as_total_return(tmp_path, pg):
    from markets.store import MarketStore
    store = MarketStore(tmp_path)
    store.con.execute("""insert into markets.quotes (run_at, asset_class, symbol, name, price, source, extra) values
        ('2026-11-04 10:00:00+00', 'index', 'S&P SL20', 'S&P Sri Lanka 20', 12394.73, 'cse', '{"market": "LK"}'),
        ('2026-11-05 10:00:00+00', 'index', 'S&P SL20', 'S&P Sri Lanka 20', 6033.19, 'cse', '{"field": "spp"}')""")
    store.close()
    store = MarketStore(tmp_path)
    rows = store.con.execute("select symbol, price from markets.quotes order by run_at").fetchall()
    assert rows == [("S&P SL20 TRI", 12394.73), ("S&P SL20", 6033.19)]
    store.close()


def test_cse_parsers_on_real_responses():
    idx = cse.parse_market_summary(json.loads((FIX / "cse_daily_market_summary.json").read_text()))
    aspi, sl20, tri = idx
    assert aspi["symbol"] == "ASPI" and aspi["price"] == 20364.97 and aspi["prev_close"] is not None
    assert aspi["change_pct"] == pytest.approx((20364.97 - aspi["prev_close"]) / aspi["prev_close"] * 100)
    # "spp" is the S&P SL20 price index; "spt" (about twice as big) is its total return index
    assert sl20["symbol"] == "S&P SL20" and sl20["price"] == 5686.82 and sl20["prev_close"] == 5752.19
    assert tri["symbol"] == "S&P SL20 TRI" and tri["price"] == 11681.77 and aspi["extra"]["per"] == 10.3
    stocks = cse.parse_trade_summary(json.loads((FIX / "cse_trade_summary.json").read_text()))
    first = stocks[0]
    assert (first["symbol"], first["name"], first["price"], first["prev_close"]) == \
        ("ABAN.N0000", "Abans Electricals PLC", 1020.5, 1061.5)
    assert first["change_pct"] == pytest.approx(-3.8624587847385774) and first["volume"] == 937
    assert len(stocks) == 25 and all(s["currency"] == "LKR" for s in stocks)


def test_crypto_parsers():
    cg = crypto.parse_coingecko(COINGECKO)
    assert [(c["rank"], c["symbol"], c["price"]) for c in cg] == [(1, "BTC", 101234.5), (2, "ETH", 3900.1)]
    assert cg[0]["extra"]["pct_7d"] == -2.5 and cg[0]["change_pct"] == 1.23
    cp = crypto.parse_coinpaprika(PAPRIKA, top_n=2)
    assert [(c["rank"], c["symbol"]) for c in cp] == [(1, "BTC"), (2, "ETH")] and cp[1]["extra"]["pct_7d"] == 3.0


def test_lkr_metal_prices():
    gold = yahoo.parse_chart(chart("GC=F", [2400.0, 2500.0], 2500.0), "commodity", "Gold", {"metal": "gold"})
    fx = yahoo.parse_chart(chart("LKR=X", [300.0, 300.0], 300.0, currency="LKR"), "fx", "USD/LKR", {"lkr_rate": True})
    out = {q["symbol"]: q for q in lkr_metal_prices([gold, fx])}
    per_g = 2500 / 31.1034768 * 300
    assert out["GOLD_LKR_G"]["price"] == pytest.approx(per_g)
    assert out["GOLD24K_LKR_PAWN"]["price"] == pytest.approx(per_g * 8)
    assert out["GOLD22K_LKR_PAWN"]["price"] == pytest.approx(per_g * 8 * 22 / 24)
    assert out["GOLD24K_LKR_PAWN"]["change_pct"] == pytest.approx(100 / 24, rel=1e-6)   # 2400 -> 2500
    assert lkr_metal_prices([gold]) == []                                                 # no FX rate, no LKR prices


def test_end_to_end_with_fake_apis(tmp_path, monkeypatch, pg):
    enc = lambda s: s.replace("^", "%5E")  # noqa: E731  (aiohttp sends "=" as is)
    yahoo_site = Site({f"/v8/finance/chart/{enc(sym)}": json.dumps(chart(sym, closes, closes[-1], cur, name))
                       for sym, closes, cur, name in [("^GSPC", [5700.0, 5728.8], "USD", "S&P 500"),
                                                      ("AAPL", [230.0, 225.0], "USD", "Apple Inc."),
                                                      ("GC=F", [2400.0, 2500.0], "USD", "Gold"),
                                                      ("LKR=X", [300.0, 301.0], "LKR", "USD/LKR")]})
    cse_site = Site({"/api/dailyMarketSummery": (FIX / "cse_daily_market_summary.json").read_text(),
                     "/api/tradeSummary": (FIX / "cse_trade_summary.json").read_text()})
    paprika_site = Site({"/v1/tickers": json.dumps(PAPRIKA)})        # CoinGecko "fails", backup used
    monkeypatch.setattr(yahoo, "HOSTS", (f"http://{yahoo_site.domain}",))
    monkeypatch.setattr(cse, "API", f"http://{cse_site.domain}/api")
    monkeypatch.setattr(crypto, "COINGECKO", f"http://{cse_site.domain}/nope")
    monkeypatch.setattr(crypto, "COINPAPRIKA", f"http://{paprika_site.domain}/v1/tickers")
    settings = {"us_indices": [{"symbol": "^GSPC", "name": "S&P 500"}], "us_stocks": ["AAPL", "MISSING"],
                "commodities": [{"symbol": "GC=F", "name": "Gold", "metal": "gold"}],
                "fx": [{"symbol": "LKR=X", "name": "USD/LKR", "lkr_rate": True}],
                "crypto": {"top_n": 2}, "fetch": {"timeout_seconds": 5}}
    snap = asyncio.run(run_collection(settings))
    by = {(q["asset_class"], q["symbol"]): q for q in snap["quotes"]}
    assert by[("index", "^GSPC")]["change_pct"] == pytest.approx(0.505263, rel=1e-4)
    assert by[("stock_us", "AAPL")]["change"] == pytest.approx(-5.0)
    assert by[("index", "ASPI")]["price"] == 20364.97 and len([k for k in by if k[0] == "stock_lk"]) == 25
    assert by[("crypto", "BTC")]["source"] == "coinpaprika" and by[("crypto", "BTC")]["rank"] == 1
    assert ("commodity_lkr", "GOLD24K_LKR_PAWN") in by
    failed = {s["source"]: s["error"] for s in snap["sources"] if not s["ok"]}
    assert list(failed) == ["MISSING"]                                                # one bad symbol, rest fine

    monkeypatch.setenv("PROJECT_DATA_DIR", str(tmp_path / "data"))
    from markets.store import MarketStore
    store = MarketStore(tmp_path / "data" / "markets")
    store.save_run(snap["run_at"], snap["finished_at"], snap["quotes"], snap["sources"])
    store.save_run(snap["run_at"].replace("Z", "1Z"), snap["finished_at"], snap["quotes"], snap["sources"])
    con = store.con
    assert con.execute("select count(*) from markets.v_latest").fetchone()[0] == len(snap["quotes"])
    assert con.execute("select count(*) from markets.quotes where symbol = 'BTC'").fetchone()[0] == 2  # history kept
    assert con.execute("select count(*) from markets.source_runs where not ok").fetchone()[0] == 2
    store.close()
    for s in (yahoo_site, cse_site, paprika_site):
        s.server.shutdown()


def stooq_csv(closes):
    rows = "\n".join(f"2026-10-{27 + i},{c},{c},{c},{c},1000" for i, c in enumerate(closes))
    return f"Date,Open,High,Low,Close,Volume\n{rows}\n"


def test_stooq_and_fx_api_parsers():
    from markets.providers import fxapi, stooq
    q = stooq.parse_history_csv(stooq_csv([5700.0, 5728.8]), "index", "^GSPC", "S&P 500", "USD")
    assert (q["price"], q["prev_close"], q["source"], q["as_of"]) == (5728.8, 5700.0, "stooq", "2026-10-28T00:00:00Z")
    with pytest.raises(ValueError):
        stooq.parse_history_csv("No data", "index", "^GSPC", "S&P 500")
    assert stooq.default_symbol("BRK-B") == "brk-b.us"
    data = {"result": "success", "time_last_update_unix": 1762041600, "rates": {"LKR": 300.0, "EUR": 0.8, "INR": 88.0}}
    got = {q["symbol"]: q for q in fxapi.parse_rates(data, [{"symbol": "LKR=X", "lkr_rate": True}, {"symbol": "EURUSD=X"},
                                                            {"symbol": "DX-Y.NYB"}])}
    assert got["LKR=X"]["price"] == 300.0 and got["EURUSD=X"]["price"] == pytest.approx(1.25)
    assert "DX-Y.NYB" not in got and got["LKR=X"]["change_pct"] is None
    with pytest.raises(ValueError):
        fxapi.parse_rates({"result": "error", "error-type": "unsupported-code"}, [])


def test_fill_change_from_previous_day_only():
    from markets.collect import fill_change_from_previous
    from markets.providers import quote
    today = quote("fx", "LKR=X", "USD/LKR", 303.0, as_of="2026-11-02T00:00:00Z")
    same_day = quote("fx", "INR=X", "USD/INR", 88.0, as_of="2026-11-02T00:00:00Z")
    fill_change_from_previous([today, same_day], {("fx", "LKR=X"): {"price": 300.0, "as_of": "2026-11-01T00:00:00Z"},
                                                  ("fx", "INR=X"): {"price": 87.0, "as_of": "2026-11-02T00:00:00Z"}})
    assert today["change_pct"] == pytest.approx(1.0) and today["extra"]["change_vs"] == "previous saved rate"
    assert same_day["change_pct"] is None                       # same day: no fake "change"


def test_yahoo_blocked_falls_back_to_stooq_and_fx_api(monkeypatch):
    from markets.providers import fxapi, stooq
    hits = {"yahoo": 0}

    yahoo_site = Site({})
    # the fake Yahoo answers 429 to everything, like the real one does when it rate-limits
    import test_pipeline_e2e as t
    orig = t.Handler.do_GET

    def do_get(self):
        if self.site is yahoo_site:
            hits["yahoo"] += 1
            self.send_response(429); self.end_headers(); return
        return orig(self)
    monkeypatch.setattr(t.Handler, "do_GET", do_get)

    prices = {"^spx": [5700.0, 5728.8], "aapl.us": [230.0, 225.0], "msft.us": [400.0, 410.0],
              "xauusd": [2400.0, 2500.0], "xagusd": [30.0, 31.0]}       # no usdlkr / eurusd on this fake Stooq
    stooq_site = Site({"/q/d/l/": lambda full: next((stooq_csv(v) for k, v in prices.items() if f"s={k.replace('^', '%5E')}&" in full
                                                     or f"s={k}&" in full), "No data")})
    er_site = Site({"/v6/latest/USD": json.dumps({"result": "success", "time_last_update_unix": 1762041600,
                                                  "rates": {"LKR": 300.0, "EUR": 0.8}})})
    monkeypatch.setattr(yahoo, "HOSTS", (f"http://{yahoo_site.domain}",))
    monkeypatch.setattr(stooq, "API", f"http://{stooq_site.domain}/q/d/l/")
    monkeypatch.setattr(fxapi, "API", f"http://{er_site.domain}/v6/latest/USD")
    settings = {"us_indices": [{"symbol": "^GSPC", "name": "S&P 500", "stooq": "^spx"}], "us_stocks": ["AAPL", "MSFT"],
                "commodities": [{"symbol": "GC=F", "name": "Gold", "metal": "gold", "stooq": "xauusd"},
                                {"symbol": "SI=F", "name": "Silver", "metal": "silver", "stooq": "xagusd"}],
                "fx": [{"symbol": "LKR=X", "name": "USD/LKR", "lkr_rate": True, "stooq": "usdlkr"},
                       {"symbol": "EURUSD=X", "name": "EUR/USD", "stooq": "eurusd"}],
                "cse": {"enabled": False}, "fetch": {"timeout_seconds": 5, "concurrency": 1}}
    snap = asyncio.run(run_collection(settings, only=["us", "metals", "fx"]))
    by = {q["symbol"]: q for q in snap["quotes"]}
    assert hits["yahoo"] == 1                                    # asked once, then left alone for the run
    assert "429" in snap["yahoo_backoff"]
    assert {by[s]["source"] for s in ("^GSPC", "AAPL", "MSFT", "GC=F", "SI=F")} == {"stooq"}
    assert by["AAPL"]["change"] == pytest.approx(-5.0) and by["GC=F"]["price"] == 2500.0
    assert by["LKR=X"]["source"] == "open.er-api.com" and by["EURUSD=X"]["price"] == pytest.approx(1.25)
    assert "GOLD24K_LKR_PAWN" in by                              # LKR gold works with the fallback FX rate
    assert [s for s in snap["sources"] if not s["ok"]] == []     # everything covered by some source
    for s in (yahoo_site, stooq_site, er_site):
        s.server.shutdown()


def test_saving_a_run_with_no_prices(tmp_path, pg):
    """No internet: nothing collected, but the run and its failed sources are still recorded."""
    from markets.store import MarketStore
    store = MarketStore(tmp_path)
    store.save_run("2026-11-02T10:00:00.000001Z", "2026-11-02T10:00:05Z", [],
                   [{"source": "cse indices", "group": "lk", "ok": False, "count": 0, "error": "offline", "seconds": 1.0}])
    store.save_run("2026-11-02T11:00:00.000001Z", "2026-11-02T11:00:05Z", [], [])
    assert store.con.execute("select count(*) from markets.runs").fetchone()[0] == 2
    assert store.con.execute("select error from markets.source_runs").fetchone()[0] == "offline"
    store.close()


def test_stooqs_browser_check_is_recognised_not_worked_around():
    page = ("<!DOCTYPE html><html><body><noscript>This site requires JavaScript to verify your browser.</noscript>"
            "<script>fetch('/__verify',{method:'POST'})</script></body></html>")
    with pytest.raises(stooq.StooqBlocked):
        stooq.parse_history_csv(page, "stock_us", "AAPL", "Apple")


# ── Stochastic RSI ───────────────────────────────────────────────────────────────────────────────────────────────
# StockCharts' published RSI example (school.stockcharts.com, "Relative Strength Index"), 14 periods
STOCKCHARTS_CLOSES = [44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245, 45.8433, 46.0826, 45.8931, 46.0328, 45.6140, 46.2820, 46.2820, 46.0028, 46.0328,
                      46.4116, 46.2222, 45.6439, 46.2122, 46.2521, 45.7137, 46.4515, 45.7835, 45.3548, 44.0288, 44.1783, 44.2181, 44.5672, 43.4205, 42.6628, 43.1314]
STOCKCHARTS_RSI = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42, 39.99, 41.46, 41.87, 45.46, 37.30, 33.08, 37.77]


def test_rsi_matches_the_published_example():
    from markets.indicators import wilder_rsi
    got = wilder_rsi(STOCKCHARTS_CLOSES)
    assert got[:14] == [None] * 14
    assert [round(v, 2) for v in got[14:]] == pytest.approx(STOCKCHARTS_RSI, abs=0.02)


def test_stoch_rsi_by_hand_from_the_published_rsi():
    from markets.indicators import stoch_rsi_series
    s = stoch_rsi_series(STOCKCHARTS_CLOSES)
    # last 14 RSI values: highest 63.26, lowest 33.08, latest 37.77 -> (37.77 - 33.08) / (63.26 - 33.08) = 15.54
    assert s["stoch_rsi"][-1] == pytest.approx(15.54, abs=0.1)
    # the day before: latest 33.08 is the lowest of its window -> exactly 0
    assert s["stoch_rsi"][-2] == pytest.approx(0, abs=1e-6)
    # two days before: window highest 63.26, lowest 37.30, latest 37.30 -> 0, so %K of the last three is 15.54 / 3
    assert s["stoch_rsi"][-3] == pytest.approx(0, abs=1e-6)
    assert s["pct_k"][-1] == pytest.approx(15.54 / 3, abs=0.05)
    assert s["pct_d"][-1] == pytest.approx(sum(s["pct_k"][-3:]) / 3)
    # the first values appear exactly when the arithmetic says they can: 14 + 14 + 3 + 3 less the three shared days = 32 closes
    assert stoch_rsi_series(STOCKCHARTS_CLOSES[:27])["stoch_rsi"][-1] is None
    assert stoch_rsi_series(STOCKCHARTS_CLOSES[:28])["stoch_rsi"][-1] is not None
    assert stoch_rsi_series(STOCKCHARTS_CLOSES[:29])["pct_k"][-1] is None
    assert stoch_rsi_series(STOCKCHARTS_CLOSES[:30])["pct_k"][-1] is not None
    assert stoch_rsi_series(STOCKCHARTS_CLOSES[:31])["pct_d"][-1] is None
    assert stoch_rsi_series(STOCKCHARTS_CLOSES[:32])["pct_d"][-1] is not None


def test_stoch_rsi_stays_on_the_0_to_100_scale_and_marks_zones():
    import random
    from markets.indicators import latest_stoch_rsi, stoch_rsi_series, zone
    rng = random.Random(7)
    closes = [100.0]
    for _ in range(300):
        closes.append(closes[-1] * (1 + rng.uniform(-0.05, 0.05)))
    s = stoch_rsi_series(closes)
    for key in ("rsi", "stoch_rsi", "pct_k", "pct_d"):
        values = [v for v in s[key] if v is not None]
        assert values and min(values) >= 0 and max(values) <= 100
    assert latest_stoch_rsi(closes)["status"] == "ok"
    assert (zone(80), zone(80.1), zone(20), zone(19.9), zone(None)) == ("middle", "overbought", "middle", "oversold", None)


def test_flat_prices_give_no_number_and_dont_divide_by_zero():
    from markets.indicators import latest_stoch_rsi, wilder_rsi
    flat = [50.0] * 60
    assert wilder_rsi(flat)[-1] is None
    v = latest_stoch_rsi(flat)
    assert (v["status"], v["stoch_rsi"], v["pct_k"], v["pct_d"], v["zone"]) == ("flat", None, None, None, None)
    assert "no value" in v["message"]


def test_a_price_that_rises_then_stops_gives_no_number():
    """Every change after the rise is zero: both averages shrink together, so RSI only moves by rounding noise. Dividing
    by that range would give a confident, meaningless number."""
    from markets.indicators import latest_stoch_rsi
    closes = [40.0 + i * (1 if i % 3 else -1) for i in range(20)] + [55.0] * 40
    assert latest_stoch_rsi(closes)["status"] == "flat"
    rising = [float(i + 1) for i in range(60)]             # always up: RSI is 100 every day, so no range either
    v = latest_stoch_rsi(rising)
    assert (v["status"], v["pct_k"]) == ("flat", None)


def test_too_little_history_says_so_with_the_count():
    from markets.indicators import MIN_CLOSES, latest_stoch_rsi
    assert MIN_CLOSES == 34
    for n in (0, 5, 33):
        v = latest_stoch_rsi([100.0 + i % 3 for i in range(n)])
        assert (v["status"], v["stoch_rsi"], v["closes_used"]) == ("short", None, n)
        assert v["message"] == f"not enough history yet ({n} of 34 days)"
    assert latest_stoch_rsi([100.0 + (i * 7) % 11 for i in range(34)])["status"] == "ok"


# ── daily closes: parsers and the plan ───────────────────────────────────────────────────────────────────────────
def lk_ms(y, m, d, hour=0, minute=0):
    from markets.closes import LK
    from datetime import datetime
    return int(datetime(y, m, d, hour, minute, tzinfo=LK).timestamp() * 1000)


def lk_now(y, m, d, hour, minute=0):
    from markets.closes import LK
    from datetime import datetime
    return datetime(y, m, d, hour, minute, tzinfo=LK)


def test_cse_chart_keeps_only_finished_days():
    from datetime import date
    from markets.closes import parse_cse_chart
    data = {"chartData": [{"t": lk_ms(2026, 11, 5), "p": 18.8}, {"t": lk_ms(2026, 11, 6), "p": 18.7}, {"t": lk_ms(2026, 11, 4), "p": None},
                          {"t": lk_ms(2026, 11, 3), "p": 0}], "id": 297}
    assert parse_cse_chart(data, lk_now(2026, 11, 6, 11)) == [(date(2026, 11, 5), 18.8)]
    assert parse_cse_chart(data, lk_now(2026, 11, 6, 15, 30)) == [(date(2026, 11, 5), 18.8), (date(2026, 11, 6), 18.7)]
    with pytest.raises(ValueError):
        parse_cse_chart({"oops": 1}, lk_now(2026, 11, 6, 11))


def test_trade_summary_only_gives_closes_of_the_latest_session():
    from datetime import date
    from markets.closes import parse_trade_summary
    ids, closes, session = parse_trade_summary({"reqTradeSummery": [
        {"id": 1, "symbol": "A.N0000", "price": 10.0, "sharevolume": 5, "lastTradedTime": lk_ms(2026, 11, 6, 14, 20)},
        {"id": 2, "symbol": "B.N0000", "price": 20.0, "sharevolume": 5, "lastTradedTime": lk_ms(2026, 11, 5, 14, 20)},
        {"id": 3, "symbol": "C.N0000", "price": 30.0, "sharevolume": 0, "lastTradedTime": lk_ms(2026, 11, 6, 10, 0)},
        {"id": 4, "symbol": "D.N0000", "price": 40.0, "sharevolume": 5, "lastTradedTime": None}]})
    assert ids == {"A.N0000": "1", "B.N0000": "2", "C.N0000": "3", "D.N0000": "4"}
    assert closes == {"A.N0000": 10.0} and session == date(2026, 11, 6)


def test_plan_after_the_close_one_call_is_enough_unless_a_day_was_missed():
    from datetime import date
    from markets.closes import plan_cse
    fri, thu, wed = date(2026, 11, 6), date(2026, 11, 5), date(2026, 11, 4)
    plan = plan_cse({"ok": thu, "done": fri, "new": None, "behind": wed}, fri, [thu, wed], lk_now(2026, 11, 6, 16))
    assert (plan["target"], plan["append"]) == (fri, True)
    assert plan["chart"] == ["behind", "new"]


def test_plan_during_trading_never_uses_todays_prices():
    from datetime import date
    from markets.closes import plan_cse
    fri, thu, wed = date(2026, 11, 6), date(2026, 11, 5), date(2026, 11, 4)
    plan = plan_cse({"ok": thu, "behind": wed}, fri, [thu, wed], lk_now(2026, 11, 6, 11))
    assert (plan["target"], plan["append"]) == (thu, False)
    assert plan["chart"] == ["behind"]              # tradeSummary is showing today: only its own history has Thursday


def test_plan_on_a_weekend_uses_fridays_close():
    from datetime import date
    from markets.closes import plan_cse
    fri, thu = date(2026, 11, 6), date(2026, 11, 5)
    plan = plan_cse({"ok": thu, "done": fri}, fri, [fri, thu], lk_now(2026, 11, 7, 9))
    assert (plan["target"], plan["append"], plan["chart"]) == (fri, True, [])


def test_coin_matching_prefers_the_same_name_then_the_rank():
    from markets.closes import match_coins, price_is_plausible
    coins = [{"id": "uni-universe", "name": "Universe", "symbol": "UNI", "rank": 0, "is_active": True},
             {"id": "uni-uniswap", "name": "Uniswap", "symbol": "UNI", "rank": 25, "is_active": True},
             {"id": "uni-old", "name": "Uni Old", "symbol": "UNI", "rank": 3, "is_active": False},
             {"id": "wsteth-wrapped-liquid-staked-ether-20", "name": "Wrapped stETH", "symbol": "WSTETH", "rank": 40, "is_active": True}]
    assert match_coins(coins, "UNI", "Uniswap")[0] == "uni-uniswap"
    assert "uni-old" not in match_coins(coins, "UNI", "Uniswap", keep=5)
    assert match_coins(coins, "WSTETH", "Lido Staked Ether wrapped") == ["wsteth-wrapped-liquid-staked-ether-20"]
    assert match_coins(coins, "NOPE", "Nope") == []
    # the name misleads: our JUP at rank 82 is its "Jupiter Exchange Token" at 92, and its coin named Jupiter is at 2,302
    jup = [{"id": "jup-jupiter", "name": "Jupiter", "symbol": "JUP", "rank": 2302}, {"id": "jup-jupiter-exchange-token", "name": "Jupiter Exchange Token", "symbol": "JUP", "rank": 92}]
    assert match_coins(jup, "JUP", "Jupiter", "jupiter-exchange-solana", rank=82)[0] == "jup-jupiter-exchange-token"
    assert price_is_plausible(100, 150) and not price_is_plausible(100, 250) and not price_is_plausible(100, None)


# ── daily closes: end to end against a fake CSE and a fake CoinPaprika ───────────────────────────────────────────
def _closes_for(n, start=100.0):
    return [start + ((i * 7) % 11) for i in range(n)]


def _fake_sources(monkeypatch, routes, calls):
    """Answers by URL ending. A value may be a dict/list (JSON) or an int (that HTTP status)."""
    import markets.closes as closes

    async def fake_json(session, method, url, *, data=None, params=None, timeout=30):
        calls.append((url.rsplit("/", 1)[-1] if "tickers" not in url else url.split("/")[-2], data, params))
        for ending, answer in routes.items():
            if ending in url:
                got = answer(data, params) if callable(answer) else answer
                if isinstance(got, int):
                    if got in closes.REFUSED_STATUSES:
                        raise closes.Refused(f"HTTP {got}")
                    raise ValueError(f"HTTP {got}")
                return got
        raise AssertionError(f"unexpected call {url}")
    monkeypatch.setattr(closes, "_json", fake_json)


def _saved_store(tmp_path, quotes):
    from markets.store import MarketStore
    store = MarketStore(tmp_path)
    store.save_run("2026-11-06T10:00:00Z", "2026-11-06T10:00:05Z", quotes, [])
    return store


def _quote(cls, symbol, name, price, **extra):
    return {"asset_class": cls, "symbol": symbol, "name": name, "price": price, "extra": extra, "source": "test"}


def _days_back(n, last_ms):
    return [last_ms - (n - 1 - i) * 86400000 for i in range(n)]


def test_cse_backfill_then_one_call_update_and_saved_values(tmp_path, monkeypatch, pg):
    from markets import closes as C
    store = _saved_store(tmp_path, [_quote("stock_lk", "AAA.N0000", "Aaa PLC", 110.0), _quote("stock_lk", "BBB.N0000", "Bbb PLC", 50.0),
                                    _quote("stock_lk", "OLD.N0000", "Old PLC", 9.0)])
    calls = []
    trade = {"reqTradeSummery": [{"id": 11, "symbol": "AAA.N0000", "price": 110.0, "sharevolume": 5, "lastTradedTime": lk_ms(2026, 11, 6, 14, 20)},
                                 {"id": 12, "symbol": "BBB.N0000", "price": 50.0, "sharevolume": 5, "lastTradedTime": lk_ms(2026, 11, 6, 14, 20)}]}
    summary = [[{"tradeDate": lk_ms(2026, 11, 6)}], [{"tradeDate": lk_ms(2026, 11, 5)}]]

    def chart(data, params):
        if data["stockId"] == "11":
            return {"chartData": [{"t": t, "p": p} for t, p in zip(_days_back(40, lk_ms(2026, 11, 6)), _closes_for(40))]}
        if data["stockId"] == "12":
            return {"chartData": [{"t": t, "p": p} for t, p in zip(_days_back(5, lk_ms(2026, 11, 6)), _closes_for(5, 50.0))]}
        return {"chartData": []}
    _fake_sources(monkeypatch, {"tradeSummary": trade, "dailyMarketSummery": summary, "companyChartDataByStock": chart,
                                "companyInfoSummery": {"reqSymbolInfo": {"id": 99}}}, calls)
    result = asyncio.run(C.sync_cse(None, store, lk_now(2026, 11, 6, 16), float("inf"), pause=0))
    assert (result["fetched"], result["errors"], result["left"]) == (3, 0, 0)
    assert [c[0] for c in calls].count("companyChartDataByStock") == 3
    store.update_stoch_rsi()
    rows = {r[0]: r for r in store.con.execute("select symbol, status, closes_used, stoch_rsi, pct_k, pct_d, last_day::text, message from markets.stoch_rsi").fetchall()}
    assert rows["AAA.N0000"][1:3] == ("ok", 40) and rows["AAA.N0000"][6] == "2026-11-06" and rows["AAA.N0000"][7] is None
    assert all(0 <= v <= 100 for v in rows["AAA.N0000"][3:6])
    assert rows["BBB.N0000"][1:6] == ("short", 5, None, None, None)
    assert rows["BBB.N0000"][7] == "not enough history yet (5 of 34 days)"
    assert rows["OLD.N0000"][7].startswith("not enough history yet (0 of 34 days): cse.lk has no daily prices")

    # next trading day: every share is up to date, so one tradeSummary call adds the closes
    calls.clear()
    trade2 = {"reqTradeSummery": [{"id": 11, "symbol": "AAA.N0000", "price": 111.5, "sharevolume": 5, "lastTradedTime": lk_ms(2026, 11, 9, 14, 20)}]}
    summary2 = [[{"tradeDate": lk_ms(2026, 11, 9)}], [{"tradeDate": lk_ms(2026, 11, 6)}]]
    _fake_sources(monkeypatch, {"tradeSummary": trade2, "dailyMarketSummery": summary2}, calls)
    result = asyncio.run(C.sync_cse(None, store, lk_now(2026, 11, 9, 16), float("inf"), pause=0))
    assert (result["fetched"], result["appended"]) == (0, 1)
    assert sorted(c[0] for c in calls) == ["dailyMarketSummery", "tradeSummary"]
    assert store.con.execute("select close from markets.daily_closes where symbol = 'AAA.N0000' and day = '2026-11-09'").fetchone()[0] == 111.5
    # a share with no trade that day has no close that day, but it's up to date
    assert store.con.execute("select count(*) from markets.daily_closes where symbol = 'BBB.N0000' and day = '2026-11-09'").fetchone()[0] == 0
    assert store.sync_state("stock_lk")["BBB.N0000"]["synced_day"].isoformat() == "2026-11-09"
    store.close()


def test_cse_stops_on_refusal_and_the_next_run_continues(tmp_path, monkeypatch, pg):
    from markets import closes as C
    store = _saved_store(tmp_path, [_quote("stock_lk", s, s, 10.0) for s in ("A", "B", "C")])
    trade = {"reqTradeSummery": [{"id": i + 1, "symbol": s, "price": 10.0, "sharevolume": 5, "lastTradedTime": lk_ms(2026, 11, 6, 14)} for i, s in enumerate("ABC")]}
    summary = [[{"tradeDate": lk_ms(2026, 11, 6)}], [{"tradeDate": lk_ms(2026, 11, 5)}]]
    calls, asked = [], []

    def chart(data, params):
        asked.append(data["stockId"])
        return 429 if data["stockId"] == "2" else {"chartData": [{"t": lk_ms(2026, 11, 6), "p": 10.0}]}
    _fake_sources(monkeypatch, {"tradeSummary": trade, "dailyMarketSummery": summary, "companyChartDataByStock": chart}, calls)
    result = asyncio.run(C.sync_cse(None, store, lk_now(2026, 11, 6, 16), float("inf"), pause=0))
    assert result["refused"] == "HTTP 429" and result["fetched"] == 1 and result["left"] == 2
    assert asked == ["1", "2"]                       # stops at once, no retry, nothing more asked
    assert store.refused_recently("cse", 60)["reason"] == "HTTP 429"
    assert store.refused_recently("coinpaprika", 60) is None

    # a run within the hour doesn't ask at all
    asked.clear(); calls.clear()
    agent = {"fetch": {"user_agent": "honest-test/1.0"}}
    results = asyncio.run(C.sync_all(store, agent, ["lk"], 1, now=lk_now(2026, 11, 6, 16, 5)))
    assert results[0]["refused"] == "HTTP 429" and calls == []

    # once the wait is over it continues where it stopped: A is not asked again
    store.con.execute("update markets.history_refusals set refused_at = now() - interval '2 hours'")
    _fake_sources(monkeypatch, {"tradeSummary": trade, "dailyMarketSummery": summary,
                                "companyChartDataByStock": lambda d, p: asked.append(d["stockId"]) or {"chartData": [{"t": lk_ms(2026, 11, 6), "p": 10.0}]}}, calls)
    result = asyncio.run(C.sync_cse(None, store, lk_now(2026, 11, 6, 16, 10), float("inf"), pause=0))
    assert asked == ["2", "3"] and result["left"] == 0
    store.close()


def test_crypto_match_is_checked_against_our_price_and_only_runs_once_a_day(tmp_path, monkeypatch, pg):
    from markets import closes as C
    store = _saved_store(tmp_path, [_quote("crypto", "UNI", "Uniswap", 8.0, id="uniswap"), _quote("crypto", "ABC", "Abc", 100.0, id="abc"),
                                    _quote("crypto", "ZZZ", "Zzz", 1.0, id="zzz")])
    catalogue = [{"id": "uni-uniswap", "name": "Uniswap", "symbol": "UNI", "rank": 25, "is_active": True},
                 {"id": "abc-other", "name": "Other", "symbol": "ABC", "rank": 900, "is_active": True}]
    calls = []

    def history(price, n=40):
        return lambda d, p: [{"timestamp": f"2026-{m:02d}-{day:02d}T00:00:00Z", "price": price + (i * 7) % 11 / 10}
                             for i, (m, day) in enumerate(_month_days(n))]
    _fake_sources(monkeypatch, {"/coins": catalogue, "uni-uniswap": history(8.0), "abc-other": history(0.001)}, calls)
    result = asyncio.run(C.sync_crypto(None, store, lk_now(2026, 11, 6, 16), float("inf"), pause=0))
    assert (result["fetched"], result["errors"]) == (1, 0)
    assert [c[0] for c in calls].count("coins") == 1
    state = store.sync_state("crypto")
    assert state["UNI"]["source_id"] == "uni-uniswap" and state["UNI"]["synced_day"].isoformat() == "2026-11-06"
    assert state["ABC"]["synced_day"] is None                # same ticker, a thousandth of the price: not this coin
    store.update_stoch_rsi()
    got = dict(store.con.execute("select symbol, message from markets.stoch_rsi").fetchall())
    assert got["UNI"] is None
    assert got["ABC"] == "not enough history yet (0 of 34 days): CoinPaprika has no coin that matches this one"
    assert got["ZZZ"].startswith("not enough history yet (0 of 34 days)")
    # the same day again: nothing to ask
    calls.clear()
    result = asyncio.run(C.sync_crypto(None, store, lk_now(2026, 11, 6, 17), float("inf"), pause=0))
    assert calls == [] and result["fetched"] == 0
    # next day: the known coin asks only for the last few days
    calls.clear()
    asyncio.run(C.sync_crypto(None, store, lk_now(2026, 11, 7, 9), float("inf"), pause=0))
    asks = [c for c in calls if c[0] == "uni-uniswap"]
    assert len(asks) == 1 and asks[0][2]["start"] == "2026-11-03" and asks[0][2]["interval"] == "1d"
    store.close()


def test_crypto_run_stays_inside_the_hourly_limit_and_takes_the_stalest_first(tmp_path, monkeypatch, pg):
    from datetime import date
    from markets import closes as C
    monkeypatch.setattr(C, "PAPRIKA_CALLS_PER_HOUR", 3)
    symbols = ["C1", "C2", "C3", "C4", "C5"]
    store = _saved_store(tmp_path, [{**_quote("crypto", s, s, 10.0, id=s.lower()), "rank": i + 1} for i, s in enumerate(symbols)])
    calls = []
    catalogue = [{"id": f"{s.lower()}-x", "name": s, "symbol": s, "rank": i + 1} for i, s in enumerate(symbols)]
    answer = lambda d, p: [{"timestamp": "2026-11-06T00:00:00Z", "price": 10.0}]
    _fake_sources(monkeypatch, {"/coins": catalogue, "-x": answer}, calls)
    result = asyncio.run(C.sync_crypto(None, store, lk_now(2026, 11, 6, 16), float("inf"), pause=0))
    assert (result["fetched"], result["left"]) == (3, 2)
    assert [c[0] for c in calls if c[0].endswith("-x")] == ["c1-x", "c2-x", "c3-x"]
    # within the hour: nothing more, and no call at all
    calls.clear()
    result = asyncio.run(C.sync_crypto(None, store, lk_now(2026, 11, 6, 17), float("inf"), pause=0))
    assert calls == [] and (result["fetched"], result["left"]) == (0, 2)
    # an hour on: the ones never fetched go before the ones already fetched
    store.con.execute("update markets.history_sync set fetched_at = now() - interval '2 hours'")
    calls.clear()
    result = asyncio.run(C.sync_crypto(None, store, lk_now(2026, 11, 7, 9), float("inf"), pause=0))
    assert [c[0] for c in calls if c[0].endswith("-x")] == ["c4-x", "c5-x", "c1-x"]
    assert store.sync_state("crypto")["C4"]["synced_day"] == date(2026, 11, 7) and result["left"] == 2
    store.close()


def test_a_coin_paprika_doesnt_have_is_looked_for_weekly_a_failed_known_coin_daily():
    from datetime import date
    from markets.closes import _waiting_after_failure as waiting
    today = date(2026, 11, 20)
    assert waiting({"source_id": None, "failed_on": date(2026, 11, 14)}, today)
    assert not waiting({"source_id": None, "failed_on": date(2026, 11, 13)}, today)
    assert waiting({"source_id": "x", "failed_on": today}, today)
    assert not waiting({"source_id": "x", "failed_on": date(2026, 11, 19)}, today)
    assert not waiting({"source_id": None, "failed_on": None}, today)


def _month_days(n):
    from datetime import date, timedelta
    return [((d := date(2026, 11, 6) - timedelta(days=n - 1 - i)).month, d.day) for i in range(n)]


def test_real_http_status_becomes_refused_with_an_honest_user_agent():
    """Through a real local HTTP server, not the stand-in: the status codes, and the headers we send."""
    from aiohttp import web
    from markets import closes as C
    seen = []

    async def scenario():
        async def handler(request):
            seen.append(dict(request.headers))
            return web.json_response({"a": 1}, status=int(request.query.get("status", 200)))
        app = web.Application()
        app.router.add_get("/x", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp_session("honest-test/1.0") as session:
                url = f"http://127.0.0.1:{port}/x"
                assert await C._json(session, "GET", url) == {"a": 1}
                for status in (401, 402, 403, 429):
                    with pytest.raises(C.Refused):
                        await C._json(session, "GET", url, params={"status": str(status)})
                with pytest.raises(ValueError):
                    await C._json(session, "GET", url, params={"status": "500"})
        finally:
            await runner.cleanup()
    asyncio.run(scenario())
    assert seen[0]["User-Agent"] == "honest-test/1.0"
    assert not {"Origin", "Referer"} & set(seen[0])


def aiohttp_session(agent):
    import aiohttp
    return aiohttp.ClientSession(headers={"User-Agent": agent})
