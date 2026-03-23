import asyncio
import json
import sys
import os
from crawl4ai import AsyncWebCrawler, CacheMode
from bs4 import BeautifulSoup
from urllib.parse import urljoin

async def fetch_html(url):
    print(f"Fetching {url}...")
    async with AsyncWebCrawler(verbose=True) as crawler:
        result = await crawler.arun(url=url, bypass_cache=True, cache_mode=CacheMode.BYPASS)
        if result.success:
            return result.html
        else:
            print(f"Error fetching {url}: {result.error_message}")
            return None

def analyze_html(html, config):
    soup = BeautifulSoup(html, 'html.parser')
    
    print(f"\n--- Analysis for {config['name']} ---")
    print(f"Total HTML length: {len(html)} chars")
    
    base_selector = config.get('base_selector')
    if base_selector:
        items = soup.select(base_selector)
        print(f"Base Selector '{base_selector}' found {len(items)} items.")
        
        if items:
            print("\nPreview of first 3 items:")
            for i, item in enumerate(items[:3]):
                print(f"\n[Item {i+1}]")
                # Try title selector
                title_sel = config.get('title_selector')
                if title_sel:
                    title_el = item.select_one(title_sel)
                    if title_el:
                        print(f"  Title: {title_el.get_text(strip=True)}")
                    else:
                        print(f"  Title: (Not found with '{title_sel}')")
                
                # Try URL selector
                url_sel = config.get('url_selector')
                if url_sel:
                    url_el = item.select_one(url_sel)
                    if url_el:
                        href = url_el.get('href')
                        full_url = urljoin(config['url'], href)
                        print(f"  URL: {full_url}")
                    else:
                        print(f"  URL: (Not found with '{url_sel}')")
                        
                # General link dump if specific selectors fail or just for info
                links = item.find_all('a')
                print(f"  (debug) Found {len(links)} links in this item:")
                for l in links:
                    print(f"    - txt: '{l.get_text(strip=True)[:30]}...' -> href: {l.get('href')}")
    else:
        print("No base_selector defined. Listing all links (first 20)...")
        links = soup.find_all('a')
        for l in links[:20]:
             print(f"Link: {l.get_text(strip=True)[:50]} -> {l.get('href')}")

async def main():
    if not os.path.exists('websites.json'):
        print("websites.json not found.")
        return

    with open('websites.json', 'r') as f:
        sites = json.load(f)

    if len(sys.argv) < 2:
        print("Usage: python site_inspector.py <site_name_or_all>")
        print("Available sites:")
        for s in sites:
            print(f" - {s['name']}")
        return

    target = sys.argv[1]
    
    selected_sites = []
    if target == 'all':
        selected_sites = sites
    else:
        for s in sites:
            if s['name'] == target:
                selected_sites = [s]
                break
    
    if not selected_sites:
        print(f"Site '{target}' not found.")
        return

    for site in selected_sites:
        html = await fetch_html(site['url'])
        if html:
            # Save dump
            dump_file = f"dump_{site['name']}.html"
            with open(dump_file, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"Saved HTML to {dump_file}")
            
            analyze_html(html, site)

if __name__ == "__main__":
    asyncio.run(main())
