import asyncio
import json
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from crawl4ai.extraction_strategy import JsonCssExtractionStrategy

async def main():
    schema = {
        "name": "Damro Products",
        "baseSelector": "div.product-grid-item",
        "fields": [
            {
                "name": "title",
                "selector": "div.product-title > a",
                "type": "text",
            },
            {
                "name": "url",
                "selector": "div.product-title > a",
                "type": "attribute",
                "attribute": "href",
            },
            {
                "name": "price",
                "selector": "div.product-price",
                "type": "text",
            },
            {
                "name": "image",
                "selector": "div.product-element-top.product_img img",
                "type": "attribute",
                "attribute": "src",
            },
        ],
    }

    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(
            url="https://www.damro.lk/products/office-furniture/tables-cupboards-racks/executive-tables",
            config=CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                extraction_strategy=JsonCssExtractionStrategy(schema),
            ),
        )

        if result.success:
            print("Successfully extracted data:")
            products = json.loads(result.extracted_content)
            print(json.dumps(products, indent=2))
            
            # Save to file for inspection
            with open("products.json", "w", encoding="utf-8") as f:
                json.dump(products, f, indent=2)
            print(f"\nSaved {len(products)} products to products.json")
        else:
            print("Failed to crawl.")

if __name__ == "__main__":
    asyncio.run(main())
