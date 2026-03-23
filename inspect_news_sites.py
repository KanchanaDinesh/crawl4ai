import asyncio
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode

async def main():
    sites = [
        {"name": "dailymirror", "url": "https://www.dailymirror.lk/"},
        {"name": "news_lk", "url": "https://www.news.lk/"},
        {"name": "island", "url": "https://island.lk/"},
        {"name": "lankaenews", "url": "https://www.lankaenews.com/"},
        {"name": "cbsl", "url": "https://www.cbsl.gov.lk/en/press/press-releases"},
        {"name": "newswire", "url": "https://www.newswire.lk/"}
    ]

    async with AsyncWebCrawler() as crawler:
        for site in sites:
            print(f"Fetching {site['name']}...")
            result = await crawler.arun(
                url=site['url'],
                config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS)
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
