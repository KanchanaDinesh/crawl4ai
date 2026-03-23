# uv run news_scraper.py
# ----------------------------------------
import asyncio
import json
import os
from dataclasses import dataclass
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler, CacheMode
from datetime import datetime

@dataclass
class SiteConfig:
    name: str
    url: str
    domain: str
    base_selector: str
    title_selector: str
    url_selector: str
    output_filename: str

async def scrape_news_site(crawler: AsyncWebCrawler, config: SiteConfig):
    print(f"Scraping {config.name}...")

    result = await crawler.arun(
        url=config.url,
        bypass_cache=True,
        cache_mode=CacheMode.BYPASS
    )

    if not result.success:
        print(f"Failed to crawl {config.name}. Error: {result.error_message}")
        return

    soup = BeautifulSoup(result.html, 'html.parser')
    items = soup.select(config.base_selector)
    
    news_items = []
    for item in items:
        title_el = item.select_one(config.title_selector)
        url_el = item.select_one(config.url_selector)
        
        if title_el and url_el:
            news_items.append({
                "title": title_el.get_text(strip=True),
                "url": url_el.get('href')
            })

    print(f"Successfully extracted {len(news_items)} items from {config.name}")
    return news_items

async def main():
    sites = [
        SiteConfig(
            name="Daily Mirror",
            url="https://www.dailymirror.lk/",
            domain="dailymirror.lk",
            base_selector="div.news_block", 
            title_selector="p > a",
            url_selector="p > a",
            output_filename="dailymirror_news.json"
        ),
        SiteConfig(
            name="News.lk",
            url="https://www.news.lk/",
            domain="news.lk",
            base_selector="div.sppb-addon-article",
            title_selector="h3 > a",
            url_selector="h3 > a",
            output_filename="news_lk_news.json"
        ),
        SiteConfig(
            name="The Island",
            url="https://island.lk/",
            domain="island.lk",
            base_selector="li.mvp-blog-story-wrap",
            title_selector="h2",
            url_selector="a",
            output_filename="island_news.json"
        ),
        SiteConfig(
            name="LankaENews",
            url="https://www.lankaenews.com/",
            domain="lankaenews.com",
            base_selector="div.newes, div.newes_left", 
            title_selector="h4 > a",
            url_selector="h4 > a",
            output_filename="lankaenews_news.json"
        ),
        SiteConfig(
            name="CBSL",
            url="https://www.cbsl.gov.lk/",
            domain="cbsl.gov.lk",
            base_selector="li.views-row",
            title_selector="div.views-field-field-file-title a",
            url_selector="div.views-field-field-file-title a",
            output_filename="cbsl_news.json"
        ),
        SiteConfig(
            name="Newswire",
            url="https://www.newswire.lk/",
            domain="newswire.lk",
            base_selector="div.posts-listunit, div.content-block",
            title_selector="h4 > a",
            url_selector="h4 > a",
            output_filename="newswire_news.json"
        ),
    ]

    all_news_items = []
    async with AsyncWebCrawler(verbose=True) as crawler:
        count = 1
        for site in sites:
            items = await scrape_news_site(crawler, site)
            if items:
                for item in items:
                    item['name'] = site.name
                    item['url'] = site.url
                    item['domain'] = site.domain
                    item['fetch_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    item['score'] = 0
                    item['item_number'] = count
                    item['country'] = "LK"
                    item['category'] = "tech"
                    count += 1
                all_news_items.extend(items)

    if all_news_items:
        with open('news.json', 'w', encoding='utf-8') as f:
            json.dump(all_news_items, f, indent=2, ensure_ascii=False)
        print(f"Successfully saved {len(all_news_items)} items to news.json")
    else:
        print("No news items extracted.")

if __name__ == "__main__":
    asyncio.run(main())
