from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_notes.storage import append_jsonl_atomic
from ai_notes.trial import evaluate_trial


class LearningTrialTests(unittest.TestCase):
    def test_trial_requires_exact_manual_validation_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / "data" / "learning" / "ledger.jsonl"
            for index in range(10):
                run_id = f"run-{index}"
                append_jsonl_atomic(
                    ledger,
                    {
                        "event": "learning_finalized",
                        "run_id": run_id,
                        "status": "success",
                        "entry_mode": "discovered" if index < 3 else "nominated",
                        "discovery_metrics": (
                            {
                                "search_query_count": 4,
                                "screened_candidate_count": 20,
                                "deep_read_count": 5,
                                "direct_candidate_count": 16,
                                "adjacent_candidate_count": 4,
                                "selected_repository": f"example/project-{index}",
                            }
                            if index < 3
                            else None
                        ),
                    },
                )
                if index < 6:
                    append_jsonl_atomic(
                        ledger,
                        {"event": "feedback_recorded", "run_id": run_id, "feedback": "continue"},
                    )
            append_jsonl_atomic(
                ledger,
                {
                    "event": "learning_review_failed",
                    "run_id": "run-0",
                    "attempt_sha256": "a" * 64,
                    "error_class": "dual_evidence",
                },
            )

            status = evaluate_trial(root)

        self.assertTrue(status.eligible_for_automation)
        self.assertEqual(10, status.completed_runs)
        self.assertEqual(3, status.discovered_runs)
        self.assertEqual(7, status.nominated_runs)
        self.assertEqual(6, status.positive_feedback_runs)
        self.assertEqual(1, status.dual_evidence_errors)

    def test_partial_runs_silence_and_duplicate_failures_do_not_fake_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / "data" / "learning" / "ledger.jsonl"
            append_jsonl_atomic(
                ledger,
                {"event": "learning_finalized", "run_id": "partial", "status": "partial", "entry_mode": "discovered"},
            )
            append_jsonl_atomic(
                ledger,
                {"event": "learning_finalized", "run_id": "bare-label", "status": "success", "entry_mode": "discovered"},
            )
            failure = {
                "event": "learning_review_failed",
                "run_id": "bad",
                "attempt_sha256": "b" * 64,
                "error_class": "dual_evidence",
            }
            append_jsonl_atomic(ledger, failure)
            append_jsonl_atomic(ledger, failure)

            status = evaluate_trial(root)

        self.assertFalse(status.eligible_for_automation)
        self.assertEqual(0, status.completed_runs)
        self.assertEqual(0, status.positive_feedback_runs)
        self.assertEqual(1, status.dual_evidence_errors)
        self.assertIn("completed_runs<10", status.unmet_requirements)


if __name__ == "__main__":
    unittest.main()
