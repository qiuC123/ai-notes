import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_learning_review import ROOT, EVIDENCE_ID, decisions, write_prepared_run

spec = importlib.util.spec_from_file_location("chemist_learning", ROOT / "experiments/project-chemist/learning_worker.py")
learning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learning)


class ChemistLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project, self.queue = write_prepared_run(self.root)
        scoped = patch.object(learning, "OWNED_PATHS", ("README.md",))
        scoped.start()
        self.addCleanup(scoped.stop)
        self.reader = learning.LearningReader(self.queue, self.root)

    def analysis(self):
        result = decisions(self.queue, self.project)
        result["connections"][0]["owned_project_problem"]["status"] = "inferred"
        return {k: v for k, v in result.items() if k not in {"schema_version", "run_id", "queue_sha256"}}

    def test_reads_only_scoped_sources_with_hashes_and_limits(self):
        self.assertEqual(len(self.reader.context()["owned_sources"]), 1)
        self.assertIn("1: Codex", self.reader.read(EVIDENCE_ID)["text"])
        self.assertIn("3: GitHub", self.reader.read("README.md", 3, 1)["text"])
        for source, count in (("../README.md", 1), (".env", 1), ("README.md", 201), ("README.md", True)):
            with self.assertRaises(ValueError): self.reader.read(source, 1, count)

    def test_submission_uses_existing_cross_checks_without_writing_ledger(self):
        result = self.reader.submit(self.analysis())
        self.assertTrue(result["valid"])
        self.assertFalse(result["ledger_written"])
        self.assertFalse((self.root / "data/learning/ledger.jsonl").exists())
        self.assertEqual(result["decisions"]["queue_sha256"], self.reader.queue_hash)
        bad = self.analysis()
        bad["connections"][0]["external_capability"]["evidence"][0]["quote"] = "invented quote"
        with self.assertRaises(ValueError): self.reader.submit(bad)

    def test_rejects_metadata_override_confirmation_and_project_drift(self):
        with self.assertRaises(ValueError): self.reader.submit({**self.analysis(), "run_id": "fake"})
        bad = self.analysis()
        bad["connections"][0]["owned_project_problem"]["status"] = "confirmed"
        with self.assertRaises(ValueError): self.reader.submit(bad)
        (self.project / "README.md").write_text("changed", encoding="utf-8")
        with self.assertRaises(ValueError): learning.LearningReader(self.queue, self.root)

    def test_allows_honest_no_connection(self):
        result = self.analysis()
        result.update(connections=[], no_connection_reason="No verified benefit", next_actions=["continue"])
        self.assertTrue(self.reader.submit(result)["valid"])
