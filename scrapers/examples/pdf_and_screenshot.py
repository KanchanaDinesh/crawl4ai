"""Save a page as both a PDF and a full-page screenshot.

    python -m scrapers.examples.pdf_and_screenshot [URL]
"""

import asyncio
import sys
from base64 import b64decode

from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig

from ..common import output_path

DEFAULT_URL = "https://strandsagents.com/latest/"


async def main(url: str) -> None:
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(
            url=url,
            config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS, pdf=True, screenshot=True),
        )
    if not result.success:
        print("Failed:", result.error_message)
        return
    if result.screenshot:
        output_path("screenshot.png").write_bytes(b64decode(result.screenshot))
        print("Saved output/screenshot.png")
    if result.pdf:
        output_path("page.pdf").write_bytes(result.pdf)
        print("Saved output/page.pdf")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL))
