import asyncio
import json
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from crawl4ai.extraction_strategy import JsonCssExtractionStrategy

async def main():
    schema = {
        "name": "Sunday Times News",
        "baseSelector": "h2.posttitle",
        "fields": [
            {
                "name": "title",
                "selector": "a",
                "type": "text",
            },
            {
                "name": "url",
                "selector": "a",
                "type": "attribute",
                "attribute": "href",
            },
        ],
    }

    async with AsyncWebCrawler() as crawler:
        print("Crawling https://www.sundaytimes.lk/ ...")
        result = await crawler.arun(
            url="https://www.sundaytimes.lk/",
            config=CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                extraction_strategy=JsonCssExtractionStrategy(schema),
            ),
        )

        if result.success:
            print("Successfully extracted data:")
            news_items = json.loads(result.extracted_content)
            
            # Print first few items
            print(f"Found {len(news_items)} items. Sample:")
            print(json.dumps(news_items[:3], indent=2))
            
            # Save to file
            output_file = "news.json"
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(news_items, f, indent=2)
            print(f"\nSaved all items to {output_file}")
        else:
            print("Failed to crawl.")
            print(f"Error: {result.error_message}")

if __name__ == "__main__":
    asyncio.run(main())
