from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_notes.__main__ import main
from ai_notes.adr import AdrValidationError, inventory_adrs


def _write_adr(root: Path, filename: str, status: str, title: str = "A decision") -> None:
    adr_dir = root / "docs" / "adr"
    adr_dir.mkdir(parents=True, exist_ok=True)
    (adr_dir / filename).write_text(
        f"# {title}\n\nStatus: {status}\n\n## Decision\n\nKeep it small.\n",
        encoding="utf-8",
    )


class AdrInventoryTests(unittest.TestCase):
    def test_current_adrs_have_deterministic_status_inventory(self) -> None:
        records = inventory_adrs(ROOT)

        self.assertEqual([f"{number:04d}" for number in range(1, 10)], [record.number for record in records])
        first = records[0]
        self.assertEqual("partially_superseded", first.status)
        self.assertEqual("0007", first.superseded_by)
        self.assertEqual("docs/adr/0001-release-atom-staged-review-and-source-governance.md", first.path)
        self.assertIsNone(records[-1].superseded_by)

    def test_missing_replacement_target_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_adr(
                root,
                "0001-old.md",
                "Superseded by [`ADR 0002`](0002-missing.md)",
            )

            with self.assertRaisesRegex(AdrValidationError, "replacement ADR 0002 does not exist"):
                inventory_adrs(root)

    def test_unrecognized_status_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_adr(root, "0001-unclear.md", "Mostly done")

            with self.assertRaisesRegex(AdrValidationError, "unrecognized status"):
                inventory_adrs(root)

    def test_duplicate_number_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_adr(root, "0001-first.md", "Accepted")
            _write_adr(root, "0001-second.md", "Rejected")

            with self.assertRaisesRegex(AdrValidationError, "duplicate ADR number 0001"):
                inventory_adrs(root)

    def test_replacement_link_must_match_numbered_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_adr(root, "0001-old.md", "Superseded by [`ADR 0002`](0003-other.md)")
            _write_adr(root, "0002-new.md", "Accepted")
            _write_adr(root, "0003-other.md", "Accepted")

            with self.assertRaisesRegex(AdrValidationError, "replacement link for ADR 0002"):
                inventory_adrs(root)

    def test_cli_prints_json_and_returns_nonzero_for_invalid_inventory(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["adr-status", "--root", str(ROOT)])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(0, code)
        self.assertEqual("0001", payload["adrs"][0]["number"])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_adr(root, "0001-unclear.md", "Unknown")
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = main(["adr-status", "--root", str(root)])

        self.assertEqual(1, code)
        self.assertIn("ADR validation failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
