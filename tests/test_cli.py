from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aihot.__main__ import main
class FakePipeline:
    def __init__(self, status: str = "success") -> None:
        self.calls: list[str] = []
        self.status = status

    def run(self, run_date: str) -> object:
        self.calls.append(run_date)
        return SimpleNamespace(
            accepted_information_path=Path(tempfile.gettempdir()) / "ai-notes-tests" / "accepted-information.json",
            status=self.status,
        )


class CommandLineTests(unittest.TestCase):
    def test_daily_command_runs_ai_notes_without_network_in_test(self) -> None:
        pipeline = FakePipeline()

        with patch("aihot.__main__.build_default_ai_notes_pipeline", return_value=pipeline):
            exit_code = main(["daily", "--date", "2026-08-27", "--root", str(ROOT)])

        self.assertEqual(0, exit_code)
        self.assertEqual(["2026-08-27"], pipeline.calls)

    def test_daily_command_returns_nonzero_for_review_failure(self) -> None:
        pipeline = FakePipeline("review_failed")

        with patch("aihot.__main__.build_default_ai_notes_pipeline", return_value=pipeline):
            exit_code = main(["daily", "--date", "2026-08-27", "--root", str(ROOT)])

        self.assertEqual(1, exit_code)

    def test_daily_command_returns_nonzero_when_pipeline_setup_fails(self) -> None:
        with patch("aihot.__main__.build_default_ai_notes_pipeline", side_effect=ValueError("bad registry")):
            exit_code = main(["daily", "--date", "2026-08-27", "--root", str(ROOT)])

        self.assertEqual(1, exit_code)


if __name__ == "__main__":
    unittest.main()
