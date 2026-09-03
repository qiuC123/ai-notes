from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_notes.impact_experiment import (
    blind_input_sha256,
    build_blind_input,
    load_impact_suite,
    score_impact_baseline,
)
from ai_notes.storage import write_json_atomic


def suite_payload() -> dict:
    evidence = lambda project, path, line, symbol: {
        "project_id": project,
        "path": path,
        "line_start": line,
        "line_end": line + 3,
        "symbol": symbol,
        "claim": "frozen evidence",
    }
    test = lambda project, selector: {
        "project_id": project,
        "path": "tests/test_contract.py",
        "selector": selector,
    }
    cases = []
    for index in range(1, 5):
        cases.append({
            "case_id": f"impact-{index:02d}",
            "title": f"Direct case {index}",
            "change_project": "producer",
            "change_description": "Change one public contract.",
            "relationship": "direct_dependency",
            "affected_projects": ["consumer"],
            "contract": "Public JSON contract.",
            "criticality": "critical" if index < 3 else "important",
            "required_tests": [test("producer", f"producer-{index}"), test("consumer", f"consumer-{index}")],
            "evidence": [
                evidence("producer", "src/contract.py", index * 10, "public_contract"),
                evidence("consumer", "src/client.py", index * 10, "consume_contract"),
            ],
            "rationale": "The consumer parses the producer output.",
        })
    cases.append({
        "case_id": "impact-05",
        "title": "Semantic control",
        "change_project": "consumer",
        "change_description": "Change consumer-only business labels.",
        "relationship": "semantic_similarity",
        "affected_projects": [],
        "contract": "Consumer domain policy only.",
        "criticality": "routine",
        "required_tests": [test("consumer", "consumer-only")],
        "evidence": [evidence("consumer", "src/domain.py", 5, "classify")],
        "rationale": "No producer interface changes.",
    })
    return {
        "schema_version": "impact-suite.v1",
        "suite_id": "test-impact-suite",
        "frozen_at": "2026-09-03T00:00:00Z",
        "source_learning": {
            "run_id": "20260903T055625Z-2556d12b",
            "relation_id": "rel-8d3b4f2a7c91",
            "external_repository": "example/method",
            "external_commit_sha": "a" * 40,
        },
        "repositories": [
            {"project_id": "producer", "repository": "example/producer", "root": "C:/producer", "git_head": "b" * 40, "read_only": True},
            {"project_id": "consumer", "repository": "example/consumer", "root": "C:/consumer", "git_head": "c" * 40, "read_only": True},
        ],
        "cases": cases,
    }


def baseline_payload(suite: dict, *, missed: set[str] | None = None, false_positive: bool = False) -> dict:
    missed = missed or set()
    cases = []
    for case in suite["cases"]:
        other = "consumer" if case["change_project"] == "producer" else "producer"
        direct = case["relationship"] == "direct_dependency" and case["case_id"] not in missed
        if case["case_id"] == "impact-05":
            relationship = "direct_dependency" if false_positive else "semantic_similarity"
        else:
            relationship = "direct_dependency" if direct else "unrelated"
        cases.append({
            "case_id": case["case_id"],
            "project_assessments": [{
                "project_id": other,
                "relationship": relationship,
                "evidence": [
                    {key: item[key] for key in ("project_id", "path", "line_start", "line_end", "symbol")}
                    for item in case["evidence"]
                ] if direct else [],
                "required_tests": list(case["required_tests"]),
                "reason": "Independent repository search result.",
            }],
        })
    return {
        "schema_version": "impact-baseline.v1",
        "suite_id": suite["suite_id"],
        "completed_at": "2026-09-03T01:00:00Z",
        "blind_protocol": {
            "independent_task_id": "independent-test-task",
            "blind_input_sha256": blind_input_sha256(suite),
            "gold_answer_accessed_before_completion": False,
            "ai_notes_repository_inspected": False,
        },
        "observed_repositories": [
            {"project_id": item["project_id"], "git_head": item["git_head"]}
            for item in suite["repositories"]
        ],
        "cases": cases,
    }


class ImpactExperimentTests(unittest.TestCase):
    def test_blind_input_contains_prompts_but_no_gold_answers(self) -> None:
        suite = suite_payload()
        blind = build_blind_input(suite)
        rendered = json.dumps(blind)

        self.assertEqual(5, len(blind["cases"]))
        self.assertNotIn("affected_projects", rendered)
        self.assertNotIn("rationale", rendered)
        self.assertNotIn("frozen evidence", rendered)
        self.assertNotIn("producer-1", rendered)
        self.assertNotIn("tests/test_contract.py", rendered)
        self.assertEqual("impact-baseline.v1", blind["response_contract"]["schema_version"])
        self.assertIn("pinned git_head", blind["repository_read_rule"])

    def test_perfect_baseline_records_two_sided_evidence_and_rejects_graph(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite = suite_payload()
            suite_path = root / "suite.json"
            baseline_path = root / "baseline.json"
            write_json_atomic(suite_path, suite)
            write_json_atomic(baseline_path, baseline_payload(suite))

            result = score_impact_baseline(suite_path=suite_path, baseline_path=baseline_path)

        self.assertEqual(1.0, result.project_recall)
        self.assertEqual(1.0, result.precision)
        self.assertEqual(1.0, result.evidence_completeness)
        self.assertEqual(1.0, result.required_test_completeness)
        self.assertTrue(all(item["level"] == "two_sided" for item in result.evidence_levels))
        self.assertFalse(result.repeated_critical_misses)
        self.assertEqual("do_not_add_dependency_graph_to_mvp", result.dependency_graph_decision)

    def test_two_critical_misses_allow_only_graph_root_cause_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite = suite_payload()
            suite_path = root / "suite.json"
            baseline_path = root / "baseline.json"
            write_json_atomic(suite_path, suite)
            write_json_atomic(
                baseline_path,
                baseline_payload(suite, missed={"impact-01", "impact-02"}, false_positive=True),
            )

            result = score_impact_baseline(suite_path=suite_path, baseline_path=baseline_path)

        self.assertEqual(0.5, result.project_recall)
        self.assertEqual(0.6667, result.precision)
        self.assertEqual(2, len(result.critical_misses))
        self.assertTrue(result.repeated_critical_misses)
        self.assertEqual(
            "dependency_graph_candidate_requires_root_cause_review",
            result.dependency_graph_decision,
        )

    def test_revision_or_blind_hash_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite = suite_payload()
            suite_path = root / "suite.json"
            baseline_path = root / "baseline.json"
            write_json_atomic(suite_path, suite)
            baseline = baseline_payload(suite)
            baseline["blind_protocol"]["blind_input_sha256"] = "d" * 64
            write_json_atomic(baseline_path, baseline)

            with self.assertRaisesRegex(ValueError, "frozen blind input"):
                score_impact_baseline(suite_path=suite_path, baseline_path=baseline_path)

    def test_frozen_suite_contract_is_valid(self) -> None:
        suite = load_impact_suite(ROOT / "experiments" / "cross-project-impact-v1" / "suite.json")
        self.assertEqual(5, len(suite["cases"]))


if __name__ == "__main__":
    unittest.main()
