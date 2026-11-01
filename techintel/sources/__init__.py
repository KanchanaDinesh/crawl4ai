"""Discovery sources. Each module exposes:

    async def collect(ctx: SourceContext) -> list[Candidate]

and keeps its parsing in a separate pure function so it can be tested offline.
To add a source: create a module here and register it in SOURCES.
"""

from dataclasses import dataclass, field

from ..domains import DomainFilter
from ..settings import Settings
from ..store import FileStore


@dataclass
class SourceContext:
    settings: Settings
    store: FileStore
    domain_filter: DomainFilter
    options: dict = field(default_factory=dict)   # CLI options, e.g. {"minutes": 5}


def get_sources() -> dict:
    from . import certstream, crtsh, hackernews, outlinks, seeds
    return {
        "seeds": seeds.collect,
        "hackernews": hackernews.collect,
        "crtsh": crtsh.collect,
        "certstream": certstream.collect,
        "outlinks": outlinks.collect,
    }
