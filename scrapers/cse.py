"""Colombo Stock Exchange (CSE) data, fetched straight from the cse.lk JSON API.

No browser is needed here: the site's own API answers plain POST requests,
which is faster and more reliable than rendering the page.

Usage:
    python -m scrapers.cse summary     # daily market summary -> output/cse_market_summary.json
    python -m scrapers.cse trades      # per-company trade summary -> output/cse_trade_summary.csv
    python -m scrapers.cse all
"""

import argparse
import asyncio
import logging
from datetime import datetime
from typing import Any

import aiohttp

from .common import BROWSER_USER_AGENT, save_csv, save_json, setup_logging

logger = logging.getLogger(__name__)

API_BASE = "https://www.cse.lk/api"


async def cse_post(session: aiohttp.ClientSession, endpoint: str, referer_path: str) -> Any:
    """POST to a CSE API endpoint and return the decoded JSON, or None on failure."""
    url = f"{API_BASE}/{endpoint}"
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://www.cse.lk",
        "Referer": f"https://www.cse.lk/{referer_path}",
    }
    logger.info("Fetching %s", url)
    try:
        async with session.post(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                logger.error("%s returned HTTP %s", url, resp.status)
                return None
            return await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.error("Request to %s failed: %s", url, e)
        return None


def print_market_summary(data: Any) -> None:
    """Print today's headline numbers. The API returns [[today], [previous day]]."""
    try:
        today = data[0][0]
    except (IndexError, KeyError, TypeError):
        return
    trade_date_ms = today.get("tradeDate")
    trade_date = (datetime.fromtimestamp(trade_date_ms / 1000).strftime("%Y-%m-%d")
                  if trade_date_ms else "N/A")
    turnover = today.get("marketTurnover")
    turnover_str = f"LKR {turnover:,.2f}" if isinstance(turnover, (int, float)) else str(turnover)

    print(f"\n--- CSE Market Summary ({trade_date}) ---")
    print(f"All Share Price Index (ASPI): {today.get('asi', 'N/A')}")
    print(f"S&P SL20 Index:               {today.get('spt', 'N/A')}")
    print(f"Market Turnover:              {turnover_str}")
    print("-" * 40)


async def market_summary(session: aiohttp.ClientSession) -> None:
    # "Summery" is the API's own spelling.
    data = await cse_post(session, "dailyMarketSummery", "equity/daily-market-summary")
    if data:
        path = save_json(data, "cse_market_summary.json")
        logger.info("Saved market summary to %s", path)
        print_market_summary(data)


async def trade_summary(session: aiohttp.ClientSession) -> None:
    data = await cse_post(session, "tradeSummary", "equity/trade-summary")
    rows = data.get("reqTradeSummery", []) if isinstance(data, dict) else data
    if rows:
        path = save_csv(rows, "cse_trade_summary.csv")
        logger.info("Saved %d trade rows to %s", len(rows), path)
    else:
        logger.warning("No trade summary rows returned.")


async def run(command: str) -> None:
    async with aiohttp.ClientSession() as session:
        if command in ("summary", "all"):
            await market_summary(session)
        if command in ("trades", "all"):
            await trade_summary(session)


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Fetch Colombo Stock Exchange data.")
    parser.add_argument("command", choices=["summary", "trades", "all"], nargs="?", default="all")
    asyncio.run(run(parser.parse_args().command))


if __name__ == "__main__":
    main()
