from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RawRecord:
    """A source-specific record before it is normalized into a public event."""

    title: str
    summary: str = ""
    url: str = ""
    published_at: str | None = None
    categories: list[str] = field(default_factory=list)
    owner: str = ""
    extra_urls: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    link_relation: str = "direct_source"


@dataclass(slots=True)
class NormalizedItem:
    """The uniform item contract written into daily event artifacts."""

    item_id: str
    title: str
    summary: str
    url: str
    source_id: str
    source_class: str
    owner: str
    published_at: str | None
    discovered_at: str
    categories: list[str]
    entities: list[str]
    evidence_allowed: bool
    raw_ref: str
    source_links: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class Event:
    """A deterministic cluster of normalized source items."""

    event_id: str
    canonical_title: str
    items: list[NormalizedItem]
    source_ids: list[str]
    source_classes: list[str]
    primary_source_url: str | None
    evidence_status: str
