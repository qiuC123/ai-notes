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
from ai_notes.review import ExperimentConfirmation, ExperimentResult, FeedbackResult, FinalizeResult, ValidationResult


RUN_ID = "20260902T083000Z-0123abcd"


class LearningCliTests(unittest.TestCase):
    def test_validate_learning_reports_hashes_without_finalizing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.json"
            input_path.write_text("{}\n", encoding="utf-8")
            stdout = io.StringIO()
            with patch(
                "ai_notes.__main__.validate_learning_decisions",
                return_value=ValidationResult(RUN_ID, "a" * 64, "b" * 64),
            ) as validate, redirect_stdout(stdout):
                code = main(
                    [
                        "validate-learning",
                        RUN_ID,
                        "--root",
                        directory,
                        "--decisions",
                        str(input_path),
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(0, code)
        self.assertEqual("valid", payload["status"])
        self.assertEqual("a" * 64, payload["queue_sha256"])
        self.assertEqual("b" * 64, payload["decisions_sha256"])
        validate.assert_called_once()

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

    def test_prepare_maps_failed_manifest_to_exit_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "learning-run-manifest.json"
            stdout = io.StringIO()
            with patch(
                "ai_notes.__main__.prepare_learning",
                return_value=PrepareResult(RUN_ID, None, manifest, "failed", ()),
            ), redirect_stdout(stdout):
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

        self.assertEqual(1, code)
        self.assertEqual("failed", json.loads(stdout.getvalue())["status"])

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

    def test_watch_status_lists_only_explicit_persistent_watches(self) -> None:
        stdout = io.StringIO()
        with patch(
            "ai_notes.__main__.list_watched_projects",
            return_value=[{"repository": "openai/codex", "enabled": True}],
        ), redirect_stdout(stdout):
            code = main(["watch-status", "--root", "."])

        self.assertEqual(0, code)
        self.assertEqual("openai/codex", json.loads(stdout.getvalue())["projects"][0]["repository"])

    def test_feedback_status_lists_runs_awaiting_explicit_feedback(self) -> None:
        stdout = io.StringIO()
        pending = [{"run_id": RUN_ID, "repository": "openai/codex"}]
        with patch("ai_notes.__main__.list_pending_feedback", return_value=pending), redirect_stdout(stdout):
            code = main(["feedback-status", "--root", "."])

        self.assertEqual(0, code)
        self.assertEqual(RUN_ID, json.loads(stdout.getvalue())["runs"][0]["run_id"])

    def test_record_experiment_result_reports_append_and_confirmation_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "result.json"
            input_path.write_text("{}\n", encoding="utf-8")
            stdout = io.StringIO()
            with patch(
                "ai_notes.__main__.record_experiment_result",
                return_value=ExperimentResult(RUN_ID, "rel-0123456789ab", True, "c" * 64),
            ) as record, redirect_stdout(stdout):
                code = main(
                    [
                        "record-experiment-result",
                        RUN_ID,
                        "--root",
                        directory,
                        "--result",
                        str(input_path),
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(0, code)
        self.assertTrue(payload["recorded"])
        self.assertEqual("awaiting_user_confirmation", payload["downstream_status"])
        record.assert_called_once()

    def test_confirm_experiment_result_reports_explicit_confirmation(self) -> None:
        stdout = io.StringIO()
        with patch(
            "ai_notes.__main__.confirm_experiment_result",
            return_value=ExperimentConfirmation(RUN_ID, "rel-0123456789ab", True, "c" * 64),
        ) as confirm, redirect_stdout(stdout):
            code = main(["confirm-experiment-result", RUN_ID, "--root", "."])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(0, code)
        self.assertTrue(payload["recorded"])
        self.assertEqual("confirmed", payload["downstream_status"])
        confirm.assert_called_once()

    def test_runtime_error_is_reported_without_traceback(self) -> None:
        stderr = io.StringIO()
        with patch("ai_notes.__main__.prepare_learning", side_effect=ValueError("bad URL")), redirect_stderr(stderr):
            code = main(["prepare-learning", "https://example.com/repo", "--project", "."])

        self.assertEqual(1, code)
        self.assertIn("ValueError: bad URL", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
