from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from test_impact_experiment import ROOT, baseline_payload, suite_payload
from ai_notes.contracts import validate_contract
from ai_notes.impact_experiment import build_blind_input, blind_input_sha256, score_impact_baseline
from ai_notes.storage import write_json_atomic


class ImpactV2Tests(unittest.TestCase):
    def fixtures(self):
        suite = suite_payload()
        suite["schema_version"] = "impact-suite.v2"
        # Existing dependency, but the final change is compatible.
        suite["cases"][-1]["relationship"] = "direct_dependency"
        baseline = baseline_payload(suite)
        baseline["schema_version"] = "impact-baseline.v2"
        for gold, case in zip(suite["cases"], baseline["cases"]):
            assessment = case["project_assessments"][0]
            assessment["relationship"] = "direct_dependency"
            assessment["requires_change"] = bool(gold["affected_projects"])
        return suite, baseline

    def score(self, suite, baseline):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json_atomic(root / "suite.json", suite)
            write_json_atomic(root / "baseline.json", baseline)
            return score_impact_baseline(suite_path=root / "suite.json", baseline_path=root / "baseline.json")

    def test_compatible_direct_dependency_is_not_an_affected_project(self):
        suite, baseline = self.fixtures()
        score = self.score(suite, baseline)
        self.assertEqual(score.false_positive_projects, 0)
        self.assertEqual(score.true_positive_projects, 4)
        self.assertEqual(score.precision, 1)

    def test_requires_change_alone_drives_scoring_not_prose(self):
        suite, baseline = self.fixtures()
        baseline["cases"][-1]["project_assessments"][0].update(requires_change=True, reason="No change needed")
        baseline["cases"][0]["project_assessments"][0].update(requires_change=False, reason="Must change")
        score = self.score(suite, baseline)
        self.assertEqual((score.false_positive_projects, score.false_negative_projects), (1, 1))
        self.assertEqual(len(score.critical_misses), 1)

    def test_required_boolean_is_strict_no_missing_null_string_or_integer(self):
        suite, baseline = self.fixtures()
        for value in (None, "false", 0, 1, "missing"):
            payload = copy.deepcopy(baseline)
            assessment = payload["cases"][0]["project_assessments"][0]
            if value == "missing":
                del assessment["requires_change"]
            else:
                assessment["requires_change"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.score(suite, payload)

    def test_change_requires_real_dependency(self):
        suite, baseline = self.fixtures()
        for relationship in ("unrelated", "semantic_similarity"):
            baseline["cases"][0]["project_assessments"][0]["relationship"] = relationship
            with self.subTest(relationship=relationship), self.assertRaises(ValueError):
                self.score(suite, baseline)

    def test_non_dependencies_can_report_no_change(self):
        suite, baseline = self.fixtures()
        for relationship in ("unrelated", "semantic_similarity"):
            baseline["cases"][-1]["project_assessments"][0]["relationship"] = relationship
            self.assertEqual(self.score(suite, baseline).false_positive_projects, 0)

    def test_v2_blind_contract_explains_fields_without_revealing_answers(self):
        suite, _ = self.fixtures()
        blind = build_blind_input(suite)
        self.assertEqual(blind["schema_version"], "impact-blind-input.v2")
        self.assertEqual(blind["response_contract"]["schema_version"], "impact-baseline.v2")
        self.assertIn("requires_change", blind["response_contract"]["project_assessment_required"])
        self.assertIn("git cat-file", blind["repository_read_rule"])
        self.assertNotIn("rationale", json.dumps(blind))
        self.assertNotIn("affected_projects", json.dumps(blind))
        self.assertNotIn("requires_change", json.dumps(blind["cases"]))

    def test_versions_cannot_be_mixed_or_silently_upgraded(self):
        suite, baseline = self.fixtures()
        baseline["schema_version"] = "impact-baseline.v1"
        with self.assertRaises(ValueError): self.score(suite, baseline)
        suite, baseline = self.fixtures()
        suite["schema_version"] = "impact-suite.v1"
        suite["cases"][-1]["relationship"] = "semantic_similarity"
        with self.assertRaises(ValueError): self.score(suite, baseline)

    def test_old_v1_blind_hash_and_legacy_scoring_are_unchanged(self):
        suite = json.loads((ROOT / "experiments/cross-project-impact-v2/suite.json").read_text(encoding="utf-8"))
        self.assertEqual(blind_input_sha256(suite), "2f53909af71ec486d8e83274d15f9e4cb5a327d5441a97d1a9451e2294b4a5d5")
        suite = suite_payload()
        baseline = baseline_payload(suite, false_positive=True)
        self.assertEqual(self.score(suite, baseline).false_positive_projects, 1)
        baseline["cases"][0]["project_assessments"][0]["requires_change"] = False
        with self.assertRaises(ValueError): validate_contract("impact-baseline.v1", baseline)


if __name__ == "__main__":
    unittest.main()
