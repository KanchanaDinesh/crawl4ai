"""markets: parsers on real/realistic responses, LKR metal prices, end-to-end against fake APIs."""

import asyncio
import json
from pathlib import Path

import pytest

from markets import collect
from markets.collect import lkr_metal_prices, run_collection
from markets.providers import crypto, cse, yahoo
from test_pipeline_e2e import Site

FIX = Path(__file__).parent / "fixtures"


def chart(symbol, closes, price, currency="USD", name=None):
    return {"chart": {"result": [{"meta": {"symbol": symbol, "currency": currency, "regularMarketPrice": price,
                                           "regularMarketTime": 1762113600, "longName": name or symbol,
                                           "regularMarketVolume": 1000, "exchangeName": "X"},
                                  "timestamp": list(range(len(closes))),
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


def test_cse_parsers_on_real_responses():
    idx = cse.parse_market_summary(json.loads((FIX / "cse_daily_market_summary.json").read_text()))
    aspi, sl20 = idx
    assert aspi["symbol"] == "ASPI" and aspi["price"] == 20364.97 and aspi["prev_close"] is not None
    assert aspi["change_pct"] == pytest.approx((20364.97 - aspi["prev_close"]) / aspi["prev_close"] * 100)
    assert sl20["symbol"] == "S&P SL20" and sl20["price"] == 11681.77 and aspi["extra"]["per"] == 10.3
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


def test_end_to_end_with_fake_apis(tmp_path, monkeypatch):
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
    assert con.execute("select count(*) from v_latest").fetchone()[0] == len(snap["quotes"])
    assert con.execute("select count(*) from quotes where symbol = 'BTC'").fetchone()[0] == 2     # history kept
    assert con.execute("select count(*) from source_runs where not ok").fetchone()[0] == 2
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


def test_saving_a_run_with_no_prices(tmp_path):
    """No internet: nothing collected, but the run and its failed sources are still recorded."""
    from markets.store import MarketStore
    store = MarketStore(tmp_path)
    store.save_run("2026-11-02T10:00:00.000001Z", "2026-11-02T10:00:05Z", [],
                   [{"source": "cse indices", "group": "lk", "ok": False, "count": 0, "error": "offline", "seconds": 1.0}])
    store.save_run("2026-11-02T11:00:00.000001Z", "2026-11-02T11:00:05Z", [], [])
    assert store.con.execute("select count(*) from runs").fetchone()[0] == 2
    assert store.con.execute("select error from source_runs").fetchone()[0] == "offline"
    store.close()
