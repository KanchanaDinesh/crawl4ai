import asyncio
import json
import logging
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def scrape_imdb_top_250(crawler):
    """
    Scrapes the IMDb Top 250 movies list using structured JSON-LD data.
    """
    url = "https://www.imdb.com/chart/top/?ref_=chtmvm_nv_menu"
    logger.info(f"Scraping IMDb Top 250: {url}")
    
    result = await crawler.arun(url=url)
    
    if not result.success:
        logger.error(f"Failed to fetch IMDb Top 250: {result.error_message}")
        return []

    soup = BeautifulSoup(result.html, 'html.parser')
    json_ld_script = soup.find('script', type='application/ld+json')
    
    items = []
    
    if json_ld_script:
        try:
            data = json.loads(json_ld_script.string)
            # The structured data usually contains the list under 'itemListElement'
            movie_list = data.get('itemListElement', [])
            
            for entry in movie_list:
                item = entry.get('item', {})
                movie_data = {
                    'title': item.get('name'),
                    'url': item.get('url'),
                    'image': item.get('image'),
                    'rating': item.get('aggregateRating', {}).get('ratingValue'),
                    'genre': item.get('genre'),
                    'type': item.get('@type'), # Should be 'Movie'
                    'rank': entry.get('position'),
                    'website': 'IMDb Top 250'
                }
                items.append(movie_data)
                
            logger.info(f"Extracted {len(items)} movies from Top 250.")
            
        except json.JSONDecodeError:
            logger.error("Failed to parse JSON-LD for Top 250.")
    else:
        logger.warning("Could not find JSON-LD script for Top 250.")
        
    return items

async def scrape_imdb_calendar(crawler):
    """
    Scrapes the IMDb Calendar for upcoming releases using generic __NEXT_DATA__ extraction.
    """
    url = "https://www.imdb.com/calendar/?ref_=nwc_nv_menu"
    logger.info(f"Scraping IMDb Calendar: {url}")
    
    result = await crawler.arun(url=url)
    
    if not result.success:
        logger.error(f"Failed to fetch IMDb Calendar: {result.error_message}")
        return []

    soup = BeautifulSoup(result.html, 'html.parser')
    next_data_script = soup.find('script', id='__NEXT_DATA__')
    
    items = []
    
    if next_data_script:
        try:
            data = json.loads(next_data_script.string)
            # Navigate to props -> pageProps -> groups
            groups = data.get('props', {}).get('pageProps', {}).get('groups', [])
            
            for group in groups:
                 release_date_group = group.get('group') # e.g., "Jan 14, 2026"
                 entries = group.get('entries', [])
                 
                 for entry in entries:
                     title_text = entry.get('titleText')
                     # 'titleType' is a dict, e.g., {'id': 'movie', 'text': 'Movie'}
                     title_type = entry.get('titleType', {}).get('text') 
                     
                     release_data = {
                         'title': title_text,
                         'release_date': entry.get('releaseDate') or release_date_group,
                         'type': title_type,
                         'genres': entry.get('genres', []),
                         'year': entry.get('releaseYear', {}).get('year'),
                         'credits': [c.get('text') for c in entry.get('principalCredits', [])],
                         'url': f"https://www.imdb.com/title/{entry.get('id')}/" if entry.get('id') else None,
                         'image': entry.get('imageModel', {}).get('url'),
                         'website': 'IMDb Calendar'
                     }
                     items.append(release_data)

            logger.info(f"Extracted {len(items)} upcoming releases from Calendar.")

        except json.JSONDecodeError:
            logger.error("Failed to parse __NEXT_DATA__ for Calendar.")
    else:
        logger.warning("Could not find __NEXT_DATA__ script for Calendar.")

    return items

async def main():
    output_file = 'imdb.json'
    existing_items = {}
    
    # Load existing data to support incremental crawling (Upsert strategy)
    try:
        with open(output_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Create a dictionary keyed by URL for fast lookups.
            # If URL is missing (rare), fallback to title+date or just append.
            for item in data:
                key = item.get('url') or f"{item.get('title')}_{item.get('release_date')}"
                existing_items[key] = item
        logger.info(f"Loaded {len(existing_items)} existing items for incremental update.")
    except (FileNotFoundError, json.JSONDecodeError):
        logger.info("No existing data found. Starting fresh.")

    async with AsyncWebCrawler(verbose=True) as crawler:
        # Run scrapers
        top_250_items = await scrape_imdb_top_250(crawler)
        calendar_items = await scrape_imdb_calendar(crawler)
        
        new_items_list = top_250_items + calendar_items
        
        # Upsert Logic: Update existing or Insert new
        added_count = 0
        updated_count = 0
        
        for item in new_items_list:
            key = item.get('url') or f"{item.get('title')}_{item.get('release_date')}"
            
            if key in existing_items:
                # Update existing item (in case rank, rating, or other details changed)
                # We overwrite with the latest data
                existing_items[key] = item
                updated_count += 1
            else:
                # Insert new item
                existing_items[key] = item
                added_count += 1
        
        # Convert back to list
        final_items = list(existing_items.values())
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(final_items, f, indent=4, ensure_ascii=False)
            
        logger.info(f"Incremental Crawl Complete. Added {added_count} new entries, Updated {updated_count} entries.")
        logger.info(f"Total items in {output_file}: {len(final_items)}")

if __name__ == "__main__":
    asyncio.run(main())
