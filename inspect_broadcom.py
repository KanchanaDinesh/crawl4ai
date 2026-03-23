import asyncio
from crawl4ai import AsyncWebCrawler, CacheMode

async def main():
    url = "https://news.broadcom.com/apj"
    async with AsyncWebCrawler(verbose=True) as crawler:
        result = await crawler.arun(url=url, bypass_cache=True, cache_mode=CacheMode.BYPASS)
        if result.success:
            with open("broadcom_dump.html", "w", encoding="utf-8") as f:
                f.write(result.html)
            print("Saved broadcom_dump.html")
        else:
            print(f"Failed: {result.error_message}")

if __name__ == "__main__":
    asyncio.run(main())
