from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aihot.cluster import cluster_items
from aihot.models import NormalizedItem
from aihot.normalize import normalize_title, normalize_url


def item(
    item_id: str,
    title: str,
    url: str,
    published_at: str = "2026-08-27T10:00:00Z",
) -> NormalizedItem:
    return NormalizedItem(
        item_id=item_id,
        title=title,
        summary="A useful workflow update.",
        url=url,
        source_id="fixture-source",
        source_class="primary_official",
        owner="Fixture",
        published_at=published_at,
        discovered_at="2026-08-27T00:00:00Z",
        categories=["product"],
        entities=[],
        evidence_allowed=True,
        raw_ref="data/raw/2026-08-27/fixture.json",
    )


class NormalizeAndClusterTests(unittest.TestCase):
    def test_normalizes_url_and_title_deterministically(self) -> None:
        self.assertEqual(
            "https://example.com/Product?q=one",
            normalize_url("HTTPS://EXAMPLE.COM/Product/?utm_source=newsletter&gclid=abc&q=one#details"),
        )
        self.assertEqual("openai releases responses api update", normalize_title("OpenAI — Releases: Responses API Update!"))

    def test_clusters_similar_chinese_titles_within_time_window(self) -> None:
        events = cluster_items(
            [
                item("a", "OpenAI 发布 Responses API 更新", "https://example.test/a"),
                item("b", "OpenAI 发布 Responses API 新功能更新", "https://example.test/b"),
            ],
            title_threshold=0.45,
            hours_window=48,
        )

        self.assertEqual(1, len(events))
        self.assertEqual({"a", "b"}, {record.item_id for record in events[0].items})

    def test_clusters_similar_english_titles_and_identical_canonical_urls(self) -> None:
        events = cluster_items(
            [
                item("a", "OpenAI releases Responses API update", "https://example.test/update?utm_source=x"),
                item("b", "OpenAI releases an update to Responses API", "https://other.test/report"),
                item("c", "Completely unrelated title", "https://example.test/update#source"),
            ],
            title_threshold=0.50,
            hours_window=48,
        )

        self.assertEqual(1, len(events))
        self.assertEqual({"a", "b", "c"}, {record.item_id for record in events[0].items})

    def test_does_not_merge_unrelated_titles(self) -> None:
        events = cluster_items(
            [
                item("a", "OpenAI API pricing changes", "https://example.test/pricing"),
                item("b", "New agent benchmark released", "https://example.test/benchmark"),
            ],
            title_threshold=0.45,
            hours_window=48,
        )

        self.assertEqual(2, len(events))


if __name__ == "__main__":
    unittest.main()
