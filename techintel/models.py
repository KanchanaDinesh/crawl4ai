"""Record shapes written by each stage (stored as JSON objects, one per line)."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Candidate:
    """A domain found by a discovery source."""
    domain: str
    url: str
    source: str                     # seeds | hackernews | crtsh | certstream | outlinks
    discovered_at: str = field(default_factory=utc_now)
    signal_at: str | None = None    # when the source saw it (cert issued, HN post time, ...)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Qualification:
    """Result of the cheap homepage check."""
    domain: str
    url: str
    status: str                     # qualified | watch | rejected
    reason: str                     # why it got that status
    checked_at: str = field(default_factory=utc_now)
    final_url: str | None = None
    http_status: int | None = None
    fetch_mode: str | None = None
    title: str | None = None
    description: str | None = None
    word_count: int = 0
    category: str | None = None
    score: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)
    matched: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Change:
    """A difference between two profile snapshots of the same domain."""
    domain: str
    change_type: str                # e.g. tech_added, page_added, description_changed, site_down
    field: str
    before: Any = None
    after: Any = None
    detected_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return asdict(self)
