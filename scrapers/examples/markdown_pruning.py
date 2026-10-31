"""Crawl a page and turn it into filtered Markdown (PruningContentFilter).

    python -m scrapers.examples.markdown_pruning [URL]
Docs: https://docs.crawl4ai.com/core/simple-crawling/
"""

import asyncio
import sys

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, DefaultMarkdownGenerator, PruningContentFilter

from ..common import output_path

DEFAULT_URL = "https://www.damro.lk/products/office-furniture/tables-cupboards-racks/executive-tables"


async def main(url: str) -> None:
    config = CrawlerRunConfig(
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.6),
            options={"ignore_links": True},
        )
    )
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=url, config=config)

    print("success:", result.success, "status:", result.status_code)
    if result.success:
        output_path("example_page.html").write_text(result.html, encoding="utf-8")
        output_path("example_page.md").write_text(result.markdown.fit_markdown or "", encoding="utf-8")
        print("Saved output/example_page.html and output/example_page.md")
        # Also available: result.cleaned_html, result.markdown.raw_markdown, result.media, result.links


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL))
