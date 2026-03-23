import asyncio
import json
import os
from dataclasses import dataclass
from typing import List, Dict, Any
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from crawl4ai.extraction_strategy import JsonCssExtractionStrategy

@dataclass
class SiteConfig:
    name: str
    url: str
    schema: Dict[str, Any]
    output_file: str

async def crawl_site(crawler: AsyncWebCrawler, config: SiteConfig):
    print(f"\n--- Crawling {config.name} ---")
    
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        extraction_strategy=JsonCssExtractionStrategy(config.schema)
    )

    result = await crawler.arun(
        url=config.url,
        config=run_config
    )

    if result.success:
        print(f"Successfully extracted data from {config.name}")
        data = json.loads(result.extracted_content)
        
        # Basic validation (print first item)
        if data:
            print(f"First item sample: {json.dumps(data[0], indent=2)}")
        
        with open(config.output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"Saved {len(data)} items to {config.output_file}")
    else:
        print(f"Failed to crawl {config.name}")
        print(f"Error: {result.error_message}")

async def main():
    # Configuration for Damro
    damro_config = SiteConfig(
        name="Damro",
        url="https://www.damro.lk/products/office-furniture/tables-cupboards-racks/executive-tables",
        output_file="damro_products.json",
        schema={
            "name": "Damro Products",
            "baseSelector": "div.product-grid-item",
            "fields": [
                {"name": "title", "selector": "div.product-title > a", "type": "text"},
                {"name": "url", "selector": "div.product-title > a", "type": "attribute", "attribute": "href"},
                {"name": "price", "selector": "div.product-price", "type": "text"},
                {"name": "image", "selector": "div.product-element-top.product_img img", "type": "attribute", "attribute": "src"},
            ],
        }
    )

    # Configuration for WebScraper.io Test Site
    webscraper_config = SiteConfig(
        name="WebScraper Test Site",
        url="https://webscraper.io/test-sites/e-commerce/allinone",
        output_file="webscraper_test_site.json",
        schema={
            "name": "Test Site Items",
            "baseSelector": "div.thumbnail",
            "fields": [
                {"name": "title", "selector": "a.title", "type": "text"},
                {"name": "url", "selector": "a.title", "type": "attribute", "attribute": "href"},
                {"name": "price", "selector": "h4.price", "type": "text"},
                {"name": "description", "selector": "p.description", "type": "text"},
                {"name": "image", "selector": "img.image", "type": "attribute", "attribute": "src"},
            ],
        }
    )

    sites = [damro_config, webscraper_config]

    async with AsyncWebCrawler() as crawler:
        for site in sites:
            await crawl_site(crawler, site)

if __name__ == "__main__":
    asyncio.run(main())
