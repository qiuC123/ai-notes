from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aihot.models import Event, NormalizedItem
from aihot.score import load_scoring_config, score_event


def event(
    *,
    title: str = "Agent workflow API release",
    source_class: str = "primary_official",
    evidence_status: str = "primary",
) -> Event:
    record = NormalizedItem(
        item_id="item-1",
        title=title,
        summary="A practical API update for creator workflows.",
        url="https://example.test/release",
        source_id="source-1",
        source_class=source_class,
        owner="Example",
        published_at="2026-08-27T08:00:00Z",
        discovered_at="2026-08-27T00:00:00Z",
        categories=["agent", "api"],
        entities=["Example"],
        evidence_allowed=True,
        raw_ref="data/raw/2026-08-27/source.json",
    )
    return Event(
        event_id="event-1",
        canonical_title=title,
        items=[record],
        source_ids=["source-1"],
        source_classes=[source_class],
        primary_source_url=record.url if evidence_status == "primary" else None,
        evidence_status=evidence_status,
    )


class ScoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_scoring_config(ROOT / "config" / "scoring.yaml")

    def test_discovery_only_has_explained_penalty_and_clamped_total(self) -> None:
        result = score_event(event(source_class="aggregator", evidence_status="discovery_only"), self.config, as_of="2026-08-27")

        self.assertEqual(-20, result.penalties["discovery_only"])
        self.assertGreaterEqual(result.total, 0)
        self.assertLessEqual(result.total, 100)
        self.assertTrue(result.recommendation.startswith("仅作为发现信号"))

    def test_primary_evidence_scores_above_equivalent_aggregator_only_event(self) -> None:
        primary = score_event(event(), self.config, as_of="2026-08-27")
        aggregator_only = score_event(
            event(source_class="aggregator", evidence_status="discovery_only"), self.config, as_of="2026-08-27"
        )

        self.assertGreater(primary.total, aggregator_only.total)
        self.assertEqual(20, primary.components["evidence"])
        self.assertEqual(0, aggregator_only.components["evidence"])

    def test_all_other_hard_penalties_have_reasons(self) -> None:
        rumor = score_event(event(title="据称 Agent workflow API release"), self.config, as_of="2026-08-27")
        repeated = score_event(
            event(), self.config, as_of="2026-08-27", recent_decision_titles=["Agent workflow API release"]
        )
        irrelevant = score_event(
            event(title="Celebrity stock price funding discussion"), self.config, as_of="2026-08-27"
        )

        self.assertEqual(-15, rumor.penalties["weak_evidence_language"])
        self.assertEqual(-20, repeated.penalties["recent_decision_similarity"])
        self.assertEqual(-15, irrelevant.penalties["non_workflow_noise"])

    def test_result_contains_every_component_for_audit(self) -> None:
        result = score_event(event(), self.config, as_of="2026-08-27")

        self.assertEqual(
            {
                "audience_value",
                "evidence",
                "novelty",
                "timeliness",
                "amesi_fit",
                "explainability",
                "production_cost_fit",
            },
            set(result.components),
        )


if __name__ == "__main__":
    unittest.main()
