from __future__ import annotations

import sys
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aihot.release_review import (
    DecisionFileReviewer,
    PendingDecisionReviewer,
    ReviewExecutionError,
    ReviewValidationError,
    finalize_decisions,
    review_in_batches,
)
from aihot.release_sources import ReleaseRecord
from aihot.ai_notes import build_default_ai_notes_pipeline


def release() -> ReleaseRecord:
    return ReleaseRecord(
        release_key="openai/codex@rust-v0.151.0",
        source_id="openai_codex",
        repository="openai/codex",
        release_tag="rust-v0.151.0",
        title="0.151.0",
        release_notes_html=(
            "<h2>New Features</h2><p>Extensions can now inspect&nbsp; or replace MCP tool results "
            "before they reach the model.</p>"
        ),
        release_notes_text="New Features Extensions can now inspect or replace MCP tool results before they reach the model.",
        url="https://github.com/openai/codex/releases/tag/rust-v0.151.0",
        published_at="2026-08-29T09:57:02Z",
        discovered_at="2026-09-01T00:00:00Z",
        raw_ref="data/raw/2026-09-01/openai_codex.xml",
    )


class ReleaseReviewTests(unittest.TestCase):
    def test_accepts_decision_only_when_each_evidence_quote_matches_normalized_release_text(self) -> None:
        payload = {
            "decisions": [
                {
                    "release_key": release().release_key,
                    "decision": "accept",
                    "change_types": ["feature"],
                    "substantive_changes": [
                        {
                            "summary_zh": "扩展可以在结果进入模型前检查或替换 MCP 工具结果。",
                            "evidence": "Extensions can now inspect or replace MCP tool results before they reach the model.",
                        }
                    ],
                    "decision_reason": "改变了扩展处理 MCP 工具结果的能力边界。",
                    "pending_verification": [],
                }
            ]
        }

        decisions = finalize_decisions([release()], payload)

        self.assertEqual("accept", decisions[0]["decision"])
        self.assertEqual(release().release_key, decisions[0]["release_key"])

    def test_pending_reviewer_stops_at_artifact_boundary_without_calling_a_model(self) -> None:
        self.assertEqual({"decisions": []}, PendingDecisionReviewer().review([]))
        with self.assertRaises(ReviewExecutionError):
            PendingDecisionReviewer().review([release()])

    def test_decision_file_reviewer_selects_exact_bounded_batch(self) -> None:
        payload = {
            "decisions": [
                {
                    "release_key": release().release_key,
                    "decision": "reject",
                    "change_types": [],
                    "substantive_changes": [],
                    "decision_reason": "Maintenance only.",
                    "pending_verification": [],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = DecisionFileReviewer(path).review([release()])

        self.assertEqual(payload, result)

    def test_default_release_pipeline_stops_for_codex_decisions_without_hermes_runtime(self) -> None:
        pipeline = build_default_ai_notes_pipeline(ROOT)

        self.assertIsInstance(pipeline.reviewer, PendingDecisionReviewer)

    def test_review_queue_is_split_into_bounded_batches(self) -> None:
        records = [
            replace(release(), release_key=f"openai/codex@rust-v0.{index}.0", release_tag=f"rust-v0.{index}.0")
            for index in range(7)
        ]

        class Reviewer:
            def __init__(self) -> None:
                self.batch_sizes: list[int] = []

            def review(self, batch: list[ReleaseRecord]) -> dict[str, object]:
                self.batch_sizes.append(len(batch))
                return {"decisions": [
                    {
                        "release_key": item.release_key,
                        "decision": "reject",
                        "change_types": [],
                        "substantive_changes": [],
                        "decision_reason": "Maintenance only.",
                        "pending_verification": [],
                    }
                    for item in batch
                ]}

        reviewer = Reviewer()
        payload = review_in_batches(reviewer, records, batch_size=5)

        self.assertEqual([5, 2], reviewer.batch_sizes)
        self.assertEqual(7, len(payload["decisions"]))

    def test_finalize_rejects_non_strict_decision_shapes(self) -> None:
        base = {
            "release_key": release().release_key,
            "decision": "reject",
            "change_types": ["feature"],
            "substantive_changes": [
                {
                    "summary_zh": "不应出现在拒绝项。",
                    "evidence": "Extensions can now inspect or replace MCP tool results before they reach the model.",
                }
            ],
            "decision_reason": "Rejected.",
            "pending_verification": [True],
        }

        with self.assertRaises(ReviewValidationError):
            finalize_decisions([release()], {"decisions": [base]})


if __name__ == "__main__":
    unittest.main()
