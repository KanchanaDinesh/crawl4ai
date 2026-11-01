"""Manually listed sites (config/seeds.txt): one domain or URL per line."""

from ..domains import root_domain
from ..models import Candidate


def parse_seeds(text: str) -> list[Candidate]:
    candidates = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        domain = root_domain(line)
        if domain:
            url = line if "://" in line else f"https://{line}"
            candidates.append(Candidate(domain=domain, url=url, source="seeds"))
    return candidates


async def collect(ctx) -> list[Candidate]:
    path = ctx.settings.config_file(ctx.options.get("seeds_file") or ctx.settings.source("seeds").get("file", "seeds.txt"))
    if not path.exists():
        return []
    return parse_seeds(path.read_text(encoding="utf-8"))
