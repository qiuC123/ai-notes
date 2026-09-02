from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_notes.__main__ import main
from ai_notes.learning import PrepareResult
from ai_notes.review import FeedbackResult, FinalizeResult


RUN_ID = "20260902T083000Z-0123abcd"


class LearningCliTests(unittest.TestCase):
    def test_prepare_prints_machine_readable_handoff_and_partial_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = Path(directory) / "learning-queue.json"
            queue.write_text("{}\n", encoding="utf-8")
            stdout = io.StringIO()
            with patch(
                "ai_notes.__main__.prepare_learning",
                return_value=PrepareResult(RUN_ID, queue, None, "partial", ("readme: rate limit",)),
            ) as prepare, redirect_stdout(stdout):
                code = main(
                    [
                        "prepare-learning",
                        "https://github.com/openai/codex",
                        "--project",
                        ".",
                        "--root",
                        directory,
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(2, code)
        self.assertEqual(RUN_ID, payload["run_id"])
        self.assertEqual(64, len(payload["queue_sha256"]))
        prepare.assert_called_once()

    def test_finalize_decisions_maps_review_failure_to_exit_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            decisions = Path(directory) / "input.json"
            decisions.write_text("{}", encoding="utf-8")
            stdout = io.StringIO()
            with patch(
                "ai_notes.__main__.finalize_learning",
                return_value=FinalizeResult(RUN_ID, manifest, None, "review_failed", False, ("bad evidence",)),
            ), redirect_stdout(stdout):
                code = main(
                    [
                        "finalize-learning",
                        RUN_ID,
                        "--root",
                        directory,
                        "--decisions",
                        str(decisions),
                    ]
                )

        self.assertEqual(1, code)
        self.assertEqual("review_failed", json.loads(stdout.getvalue())["status"])

    def test_finalize_feedback_records_only_explicit_enum(self) -> None:
        stdout = io.StringIO()
        with patch(
            "ai_notes.__main__.record_feedback",
            return_value=FeedbackResult(RUN_ID, "watch", True, None),
        ) as feedback, redirect_stdout(stdout):
            code = main(["finalize-learning", RUN_ID, "--feedback", "watch"])

        self.assertEqual(0, code)
        self.assertTrue(json.loads(stdout.getvalue())["recorded"])
        feedback.assert_called_once()

    def test_runtime_error_is_reported_without_traceback(self) -> None:
        stderr = io.StringIO()
        with patch("ai_notes.__main__.prepare_learning", side_effect=ValueError("bad URL")), redirect_stderr(stderr):
            code = main(["prepare-learning", "https://example.com/repo", "--project", "."])

        self.assertEqual(1, code)
        self.assertIn("ValueError: bad URL", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
