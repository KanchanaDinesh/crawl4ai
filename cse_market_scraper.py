import asyncio
import json
import logging
import aiohttp
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

async def fetch_cse_market_summary():
    """
    Fetches the daily market summary directly from the CSE API.
    """
    url = "https://www.cse.lk/api/dailyMarketSummery"
    
    # Setting up standard headers to mimic a browser request
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://www.cse.lk",
        "Referer": "https://www.cse.lk/equity/daily-market-summary",
    }
    
    logger.info(f"Fetching data from {url}")
    
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # The CSE API uses POST for this endpoint
            async with session.post(url, timeout=30) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info("Successfully fetched market summary data.")
                    return data
                else:
                    logger.error(f"Failed to fetch data. Status code: {response.status}")
                    return None
    except Exception as e:
        logger.error(f"Error while fetching data: {str(e)}")
        return None

async def main():
    data = await fetch_cse_market_summary()
    
    if data:
        output_file = "cse_market_summary.json"
        
        # The API typically returns a list of lists: [[current_day_data], [previous_day_data]]
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            logger.info(f"Successfully saved market summary data to {output_file}")
            
            # Print a quick summary of the current day to the console
            if len(data) > 0 and len(data[0]) > 0:
                current_day = data[0][0]
                trade_date_ms = current_day.get("tradeDate", 0)
                
                if trade_date_ms:
                    trade_date = datetime.fromtimestamp(trade_date_ms / 1000.0).strftime('%Y-%m-%d')
                else:
                    trade_date = "N/A"
                    
                asi = current_day.get("asi", "N/A")
                spt = current_day.get("spt", "N/A")
                turnover = current_day.get("marketTurnover", 0.0)
                
                print(f"\n--- CSE Market Summary ({trade_date}) ---")
                print(f"All Share Price Index (ASPI): {asi}")
                print(f"S&P SL20 Index:             {spt}")
                if isinstance(turnover, (int, float)):
                    print(f"Market Turnover:             LKR {turnover:,.2f}")
                else:
                    print(f"Market Turnover:             {turnover}")
                print("-" * 35)
                
        except Exception as e:
            logger.error(f"Failed to save data to {output_file}: {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())
