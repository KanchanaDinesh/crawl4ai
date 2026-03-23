import asyncio
import csv
import logging
import aiohttp

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

async def fetch_trade_summary():
    """
    Fetches the trade summary directly from the CSE API.
    """
    url = "https://www.cse.lk/api/tradeSummary"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://www.cse.lk",
        "Referer": "https://www.cse.lk/equity/trade-summary",
    }
    
    logger.info(f"Fetching data from {url}")
    
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(url, timeout=30) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"Successfully fetched {len(data)} records.")
                    return data
                else:
                    logger.error(f"Failed to fetch data. Status code: {response.status}")
                    return None
    except Exception as e:
        logger.error(f"Error while fetching data: {str(e)}")
        return None

def save_to_csv(data, filename="trade_summary.csv"):
    if not data:
        return
        
    try:
        with open(filename, mode='w', newline='', encoding='utf-8') as f:
            if isinstance(data, list) and len(data) > 0:
                # Use the keys from the first dictionary as CSV headers
                headers = list(data[0].keys())
                writer = csv.DictWriter(f, fieldnames=headers)
                
                writer.writeheader()
                for row in data:
                    writer.writerow(row)
                    
            logger.info(f"Successfully saved data to {filename}")
    except Exception as e:
        logger.error(f"Failed to save data to {filename}: {str(e)}")

async def main():
    data = await fetch_trade_summary()
    if data and "reqTradeSummery" in data:
        save_to_csv(data["reqTradeSummery"], "trade_summary.csv")
    elif data:
        save_to_csv(data, "trade_summary.csv")

if __name__ == "__main__":
    asyncio.run(main())
