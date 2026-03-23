# tested by 2026 Jan 12 
# seems working fine 
# https://docs.crawl4ai.com/core/simple-crawling/
# https://docs.crawl4ai.com/core/examples/

import asyncio
from crawl4ai import *

async def main():
    async with AsyncWebCrawler() as crawler:
        config = CrawlerRunConfig(
            markdown_generator=DefaultMarkdownGenerator(
                content_filter=PruningContentFilter(threshold=0.6),
                options={"ignore_links": True}
            )
        )

        result = await crawler.arun(
            #url="https://www.dailymirror.lk/",
            #url="https://www.hitad.lk/search-sl?keyword=vehicle&district=",
            #url="https://ikman.lk/en/ads/sri-lanka/vans",
            url="https://www.damro.lk/products/office-furniture/tables-cupboards-racks/executive-tables",
            config=config
        )
        # Save output to HTML file
        with open("output.html", "w", encoding="utf-8") as f:
            f.write(result.html)
        print("Successfully saved crawled content to output.html")
        # Different content formats
        #print(result.html)         # Raw HTML
        #print(result.cleaned_html) # Cleaned HTML
        #print(result.markdown.raw_markdown) # Raw markdown from cleaned html
        #print(result.markdown.fit_markdown) # Most relevant content in markdown

        # Check success status
        print(result.success)      # True if crawl succeeded
        #print(result.status_code)  # HTTP status code (e.g., 200, 404)

        # Access extracted media and links
        #print(result.media)        # Dictionary of found media (images, videos, audio)
        #print(result.links)        # Dictionary of internal and external links



if __name__ == "__main__":
    asyncio.run(main())