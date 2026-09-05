from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("chemist_worker", ROOT / "experiments/project-chemist/worker.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class ChemistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        cls.root = cls.base / "monorepo"
        cls.root.mkdir()

        def git(*args):
            return subprocess.check_output(["git", "-C", str(cls.root), *args], stderr=subprocess.DEVNULL).decode().strip()

        git("init", "-q")
        for project in ("producer", "consumer"):
            folder = cls.root / project
            (folder / "tests").mkdir(parents=True)
            (folder / "contract.py").write_text("partial = False\n\ndef contract():\n    return partial\n", encoding="utf-8")
            (folder / "tests/test_contract.py").write_text(
                "# Parsing must not execute this file\nraise RuntimeError('do not run')\n"
                "class ContractTests:\n    def test_partial(self):\n        pass\n\ndef test_top():\n    pass\n", encoding="utf-8")
            (folder / ".env").write_text("DO_NOT_READ", encoding="utf-8")
            (folder / "auth.json").write_text('{"secret":"DO_NOT_READ"}', encoding="utf-8")
            (folder / "binary.txt").write_bytes(b"a\x00b")
            (folder / "large.txt").write_bytes(b"a" * (worker.MAX_FILE + 1))
        (cls.root / "outside.md").write_text("Not authorized", encoding="utf-8")
        git("add", ".")
        # A committed symbolic link must never resolve to a host file.
        oid = subprocess.check_output(["git", "-C", str(cls.root), "hash-object", "-w", "--stdin"], input=b"../../outside.md").decode().strip()
        git("update-index", "--add", "--cacheinfo", f"120000,{oid},producer/link.md")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture")
        cls.head = git("rev-parse", "HEAD")
        cls.manifest = {
            "schema_version": "impact-blind-input.v1", "suite_id": "chemist-test",
            "source_learning": {}, "repository_read_rule": "pinned", "response_contract": {},
            "repositories": [{"project_id": p, "root": str(cls.root / p), "git_head": cls.head, "read_only": True} for p in ("producer", "consumer")],
            "cases": [{"case_id": f"impact-{i:02d}", "title": "test", "change_project": "producer", "change_description": "Rename partial"} for i in range(1, 6)],
        }
        cls.input = cls.base / "blind-input.json"
        cls.input.write_text(json.dumps(cls.manifest), encoding="utf-8")
        (cls.root / "producer/contract.py").write_text("DRIFTED WORKTREE", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        # Git objects are read-only on Windows; let TemporaryDirectory handle them.
        cls.temp.cleanup()

    def setUp(self):
        self.agent = worker.Chemist(self.input)

    def result(self):
        evidence = [{"project_id": p, "path": "contract.py", "line_start": 1, "line_end": 4, "symbol": "partial"} for p in ("producer", "consumer")]
        tests = [{"project_id": p, "path": "tests/test_contract.py", "selector": "ContractTests.test_partial"} for p in ("producer", "consumer")]
        return {
            "schema_version": "impact-baseline.v1", "suite_id": "chemist-test", "completed_at": "2026-09-05T00:00:00Z",
            "blind_protocol": {"independent_task_id": "fixture", "blind_input_sha256": self.agent.input_hash,
                               "gold_answer_accessed_before_completion": False, "ai_notes_repository_inspected": False},
            "observed_repositories": [{"project_id": p, "git_head": self.head} for p in ("producer", "consumer")],
            "cases": [{"case_id": f"impact-{i:02d}", "project_assessments": [{"project_id": "consumer", "relationship": "direct_dependency", "evidence": copy.deepcopy(evidence), "required_tests": copy.deepcopy(tests), "reason": "Fixture only"}]} for i in range(1, 6)],
        }

    def test_pinned_read_ignores_worktree_and_confines_monorepo(self):
        self.assertIn("1: partial = False", self.agent.read("producer", "contract.py")["text"])
        paths = self.agent.inventory("producer")["paths"]
        self.assertNotIn("outside.md", paths)
        self.assertNotIn("consumer/contract.py", paths)
        self.assertNotIn("link.md", paths)
        self.assertNotIn(".env", paths)
        self.assertNotIn("auth.json", paths)
        self.assertNotIn("large.txt", paths)

    def test_rejects_unlisted_paths_and_projects(self):
        for path in ("../outside.md", "../../outside.md", "E:/secret.txt", "contract.py:HEAD", "link.md", ".env", "auth.json", "large.txt"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.agent.read("producer", path)
        with self.assertRaises(ValueError):
            self.agent.inventory("unknown")

    def test_binary_and_limits(self):
        with self.assertRaises(ValueError):
            self.agent.read("producer", "binary.txt")
        for kwargs in ({"count": 201}, {"start": 0}, {"start": True}, {"start": 9999}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.agent.read("producer", "contract.py", **kwargs)
        with self.assertRaises(ValueError):
            self.agent.search("producer", "", "contract.py")

    def test_literal_search_and_test_ast(self):
        self.assertEqual(self.agent.search("producer", ".*", "contract.py")["total"], 0)
        self.assertEqual(self.agent.search("producer", "PARTIAL", "contract.py")["total"], 2)
        parsed = self.agent.test_selectors("producer", "tests/test_contract.py")
        self.assertEqual([s["selector"] for s in parsed["selectors"]], ["ContractTests.test_partial", "test_top"])
        self.assertFalse(parsed["tests_executed"])

    def test_valid_result_does_not_claim_semantic_correctness(self):
        result = self.agent.validate_result(self.result())
        self.assertTrue(result["valid"])
        self.assertFalse(result["semantic_correctness_verified"])
        self.assertFalse(result["tests_executed"])

    def test_rejects_weak_or_invented_evidence(self):
        for failure in ("one_side", "line", "symbol", "test", "missing_test_side", "symbol_only", "unknown_project"):
            payload = self.result()
            a = payload["cases"][0]["project_assessments"][0]
            if failure == "one_side": a["evidence"].pop()
            if failure == "line": a["evidence"][0]["line_end"] = 999
            if failure == "symbol": a["evidence"][0]["symbol"] = "invented"
            if failure == "test": a["required_tests"][0]["selector"] = "ContractTests.test_invented"
            if failure == "missing_test_side": a["required_tests"].pop()
            if failure == "symbol_only":
                del a["evidence"][0]["line_start"]
                del a["evidence"][0]["line_end"]
            if failure == "unknown_project": a["evidence"][0]["project_id"] = "outside"
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                self.agent.validate_result(payload)

    def test_rejects_revision_hash_case_and_blind_protocol_mismatch(self):
        for failure in ("head", "hash", "case", "duplicate", "gold"):
            payload = self.result()
            if failure == "head": payload["observed_repositories"][0]["git_head"] = "f" * 40
            if failure == "hash": payload["blind_protocol"]["blind_input_sha256"] = "f" * 64
            if failure == "case": payload["cases"].pop()
            if failure == "duplicate": payload["cases"][0] = payload["cases"][1]
            if failure == "gold": payload["blind_protocol"]["gold_answer_accessed_before_completion"] = True
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                self.agent.validate_result(payload)

    def test_semantic_similarity_does_not_require_invented_dependency(self):
        payload = self.result()
        a = payload["cases"][0]["project_assessments"][0]
        a.update(relationship="semantic_similarity", evidence=[], required_tests=[])
        self.assertTrue(self.agent.validate_result(payload)["valid"])

    def test_rejects_gold_input_and_missing_commit(self):
        bad = copy.deepcopy(self.manifest)
        bad["cases"][0]["evidence"] = []
        path = self.base / "bad.json"
        path.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaises(ValueError): worker.Chemist(path)
        bad = copy.deepcopy(self.manifest)
        bad["repositories"][0]["git_head"] = "f" * 40
        path.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaises(ValueError): worker.Chemist(path).context()

    def test_cli_envelope_and_exit_code(self):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"}
        for action, expected in (("context", 0), ("shell", 1)):
            result = subprocess.run([sys.executable, str(ROOT / "experiments/project-chemist/worker.py"), "--input", str(self.input)],
                input=json.dumps({"action": action, "params": {}}), capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, expected)
            self.assertEqual(json.loads(result.stdout)["ok"], expected == 0)


if __name__ == "__main__":
    unittest.main()
