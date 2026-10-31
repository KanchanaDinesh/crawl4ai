"""Try a JsonCssExtractionStrategy schema on inline HTML, without hitting a website.

Handy for testing a schema before pointing it at a real site.
    python -m scrapers.examples.raw_html_extraction
"""

import asyncio
import json

from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig, JsonCssExtractionStrategy

SCHEMA = {
    "name": "Example Items",
    "baseSelector": "div.item",
    "fields": [
        {"name": "title", "selector": "h2", "type": "text"},
        {"name": "link", "selector": "a", "type": "attribute", "attribute": "href"},
    ],
}

RAW_HTML = "<div class='item'><h2>Item 1</h2><a href='https://example.com/item1'>Link 1</a></div>"


async def main() -> None:
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(
            url="raw://" + RAW_HTML,
            config=CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                extraction_strategy=JsonCssExtractionStrategy(SCHEMA),
            ),
        )
    print(json.loads(result.extracted_content))


if __name__ == "__main__":
    asyncio.run(main())
