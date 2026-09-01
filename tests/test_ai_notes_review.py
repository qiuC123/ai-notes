from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aihot.release_review import HermesCliReviewer, ReviewValidationError, finalize_decisions, review_in_batches
from aihot.release_sources import ReleaseRecord


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

    def test_hermes_reviewer_invokes_verified_zero_tool_oneshot_contract(self) -> None:
        captured: dict[str, object] = {}

        def runner(command: list[str], **kwargs: object) -> object:
            captured["call_count"] = int(captured.get("call_count", 0)) + 1
            captured["command"] = command
            prompt_path = Path(command[command.index("--query-file") + 1])
            prompt = prompt_path.read_text(encoding="utf-8")
            captured["prompt"] = prompt
            output = (
                '{"available_tool_names":[]}\n'
                if "available_tool_names" in prompt
                else '{"decisions": []}\n'
            )
            return SimpleNamespace(
                returncode=0,
                stdout="Warning: Unknown toolsets: __no_tools__\nsession_id: test\n" + output,
                stderr="",
            )

        reviewer = HermesCliReviewer(runner=runner)
        payload = reviewer.review([])

        command = captured["command"]
        self.assertIn("__no_tools__", command)
        self.assertIn("--safe-mode", command)
        self.assertIn("--max-turns", command)
        self.assertEqual(2, captured["call_count"])
        self.assertEqual({"decisions": []}, payload)
        self.assertIn("untrusted", str(captured["prompt"]).lower())
        self.assertIn('"substantive_changes"', str(captured["prompt"]))
        self.assertIn("pending_verification must be a JSON array", str(captured["prompt"]))

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
