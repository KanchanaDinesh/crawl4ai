import asyncio
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode

async def main():
    sites = [
        {"name": "imdb_calendar", "url": "https://www.imdb.com/calendar/?ref_=nwc_nv_menu"},
        {"name": "imdb_top_250", "url": "https://www.imdb.com/chart/top/?ref_=chtmvm_nv_menu"}
    ]

    async with AsyncWebCrawler(verbose=True) as crawler:
        for site in sites:
            print(f"Fetching {site['name']}...")
            # IMDb often requires headers or specific browser emulation to avoid 403/404 on bots
            # crawl4ai usually handles this well, but we'll monitor the output.
            result = await crawler.arun(
                url=site['url'],
                bypass_cache=True,
                cache_mode=CacheMode.BYPASS
            )
            
            if result.success:
                filename = f"{site['name']}_dump.html"
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(result.html)
                print(f"Saved to {filename}")
            else:
                print(f"Failed to fetch {site['name']}: {result.error_message}")

if __name__ == "__main__":
    asyncio.run(main())
