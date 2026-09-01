from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Iterable

from aihot.models import Event, NormalizedItem
from aihot.normalize import normalize_title, normalize_url


_ASCII_TOKEN = re.compile(r"[a-z0-9]+")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_PRIMARY_CLASSES = ("primary_official", "open_source", "research")
_CLASS_PRIORITY = {name: index for index, name in enumerate(_PRIMARY_CLASSES)}


def _title_features(title: str) -> set[str]:
    normalized = normalize_title(title)
    features = {f"ascii:{token}" for token in _ASCII_TOKEN.findall(normalized)}
    for run in _CJK_RUN.findall(normalized):
        if len(run) == 1:
            features.add(f"cjk:{run}")
        else:
            features.update(f"cjk:{run[index:index + 2]}" for index in range(len(run) - 1))
    return features


def title_similarity(left: str, right: str) -> float:
    left_features = _title_features(left)
    right_features = _title_features(right)
    if not left_features or not right_features:
        return 0.0
    return len(left_features & right_features) / len(left_features | right_features)


def _published_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _within_window(left: NormalizedItem, right: NormalizedItem, hours_window: float) -> bool:
    left_time = _published_at(left.published_at)
    right_time = _published_at(right.published_at)
    if left_time is None or right_time is None:
        return False
    return abs((left_time - right_time).total_seconds()) <= hours_window * 3600


def _same_canonical_url(left: NormalizedItem, right: NormalizedItem) -> bool:
    return bool(left.url and right.url and normalize_url(left.url) == normalize_url(right.url))


def _matches_cluster(
    candidate: NormalizedItem,
    cluster: Iterable[NormalizedItem],
    title_threshold: float,
    hours_window: float,
) -> bool:
    for existing in cluster:
        if _same_canonical_url(candidate, existing):
            return True
        if _within_window(candidate, existing, hours_window) and title_similarity(candidate.title, existing.title) >= title_threshold:
            return True
    return False


def _representative(items: list[NormalizedItem]) -> NormalizedItem:
    return min(
        items,
        key=lambda item: (
            _CLASS_PRIORITY.get(item.source_class, len(_PRIMARY_CLASSES)),
            normalize_title(item.title),
            normalize_url(item.url),
            item.item_id,
        ),
    )


def _evidence_status(items: list[NormalizedItem], primary_url: str | None) -> str:
    if primary_url:
        return "primary"
    if all(item.source_class == "aggregator" for item in items):
        return "discovery_only"
    eligible_non_aggregator_sources = {
        item.source_id for item in items if item.evidence_allowed and item.source_class != "aggregator"
    }
    if len(eligible_non_aggregator_sources) >= 2:
        return "multi_source"
    return "insufficient"


def _event_from_items(items: list[NormalizedItem]) -> Event:
    ordered_items = sorted(items, key=lambda item: item.item_id)
    representative = _representative(ordered_items)
    primary_candidates = [
        item for item in ordered_items if item.source_class in _PRIMARY_CLASSES and item.evidence_allowed and item.url
    ]
    primary_url = _representative(primary_candidates).url if primary_candidates else None
    identity = normalize_url(primary_url or representative.url) or normalize_title(representative.title)
    event_id = hashlib.sha256(f"event:{identity}".encode("utf-8")).hexdigest()
    return Event(
        event_id=event_id,
        canonical_title=representative.title,
        items=ordered_items,
        source_ids=sorted({item.source_id for item in ordered_items}),
        source_classes=sorted({item.source_class for item in ordered_items}),
        primary_source_url=primary_url,
        evidence_status=_evidence_status(ordered_items, primary_url),
    )


def cluster_items(
    items: Iterable[NormalizedItem],
    *,
    title_threshold: float,
    hours_window: float,
) -> list[Event]:
    """Greedily build deterministic clusters from URLs, titles, and time windows."""
    clusters: list[list[NormalizedItem]] = []
    for candidate in sorted(items, key=lambda item: item.item_id):
        for cluster in clusters:
            if _matches_cluster(candidate, cluster, title_threshold, hours_window):
                cluster.append(candidate)
                break
        else:
            clusters.append([candidate])
    return sorted((_event_from_items(cluster) for cluster in clusters), key=lambda event: event.event_id)
