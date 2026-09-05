from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ai_notes.contracts import (
    IMPACT_BASELINE_SCHEMA, IMPACT_SUITE_SCHEMA, IMPACT_BASELINE_V2_SCHEMA,
    IMPACT_SUITE_V2_SCHEMA, validate_contract,
)
from ai_notes.storage import canonical_json_bytes, sha256_bytes, write_bytes_atomic


@dataclass(frozen=True, slots=True)
class ImpactScore:
    suite_id: str
    blind_input_sha256: str
    true_positive_projects: int
    false_negative_projects: int
    false_positive_projects: int
    project_recall: float
    precision: float
    critical_misses: tuple[dict[str, str], ...]
    repeated_critical_misses: bool
    evidence_completeness: float
    required_test_completeness: float
    evidence_levels: tuple[dict[str, str], ...]
    dependency_graph_decision: str

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["critical_misses"] = list(self.critical_misses)
        payload["evidence_levels"] = list(self.evidence_levels)
        return payload


def _load(path: Path, schema_name: str) -> dict[str, Any]:
    return validate_contract(schema_name, json.loads(path.read_text(encoding="utf-8")))


def _validate_suite(suite: dict[str, Any]) -> None:
    project_ids = [item["project_id"] for item in suite["repositories"]]
    if len(project_ids) != len(set(project_ids)):
        raise ValueError("Impact suite repository project_id values must be unique")
    case_ids = [item["case_id"] for item in suite["cases"]]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Impact suite case_id values must be unique")
    known = set(project_ids)
    for case in suite["cases"]:
        if case["change_project"] not in known:
            raise ValueError(f"Unknown change project in {case['case_id']}")
        affected = set(case["affected_projects"])
        if not affected <= known or case["change_project"] in affected:
            raise ValueError(f"Invalid affected projects in {case['case_id']}")
        if suite["schema_version"] == IMPACT_SUITE_SCHEMA and case["relationship"] == "direct_dependency" and not affected:
            raise ValueError(f"Direct dependency case {case['case_id']} requires an affected project")
        if case["relationship"] == "semantic_similarity" and affected:
            raise ValueError(f"Semantic-only case {case['case_id']} cannot declare an affected project")
        evidence_projects = {item["project_id"] for item in case["evidence"]}
        if not evidence_projects <= known:
            raise ValueError(f"Unknown evidence project in {case['case_id']}")
        if any(item["line_start"] > item["line_end"] for item in case["evidence"]):
            raise ValueError(f"Invalid evidence line range in {case['case_id']}")
        if case["relationship"] == "direct_dependency" and not ({case["change_project"]} | affected) <= evidence_projects:
            raise ValueError(f"Direct dependency case {case['case_id']} requires two-sided evidence")


def load_impact_suite(path: Path) -> dict[str, Any]:
    suite = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(suite, dict) or suite.get("schema_version") not in (IMPACT_SUITE_SCHEMA, IMPACT_SUITE_V2_SCHEMA):
        raise ValueError("Unsupported impact suite version")
    validate_contract(suite["schema_version"], suite)
    _validate_suite(suite)
    return suite


def build_blind_input(suite: dict[str, Any]) -> dict[str, Any]:
    _validate_suite(suite)
    payload = {
        "schema_version": "impact-blind-input.v1",
        "suite_id": suite["suite_id"],
        "source_learning": {
            "run_id": suite["source_learning"]["run_id"],
            "relation_id": suite["source_learning"]["relation_id"],
        },
        "repositories": [
            {
                "project_id": item["project_id"],
                "root": item["root"],
                "git_head": item["git_head"],
                "read_only": item["read_only"],
            }
            for item in suite["repositories"]
        ],
        "repository_read_rule": (
            "Analyze the pinned git_head with read-only git show commands; ignore later working-tree changes, "
            "do not switch checkouts, and do not modify either sample repository."
        ),
        "cases": [
            {
                "case_id": case["case_id"],
                "title": case["title"],
                "change_project": case["change_project"],
                "change_description": case["change_description"],
            }
            for case in suite["cases"]
        ],
        "response_contract": {
            "schema_version": "impact-baseline.v1",
            "required_top_level": [
                "schema_version",
                "suite_id",
                "completed_at",
                "blind_protocol",
                "observed_repositories",
                "cases",
            ],
            "blind_protocol": {
                "required": [
                    "independent_task_id",
                    "blind_input_sha256",
                    "gold_answer_accessed_before_completion",
                    "ai_notes_repository_inspected",
                ],
                "fixed_values": {
                    "gold_answer_accessed_before_completion": False,
                    "ai_notes_repository_inspected": False,
                },
            },
            "case_rule": "Assess every repository except change_project exactly once.",
            "project_assessment_required": [
                "project_id",
                "relationship",
                "evidence",
                "required_tests",
                "reason",
            ],
            "relationship_values": [
                "direct_dependency",
                "semantic_similarity",
                "unrelated",
            ],
            "evidence_fields": "project_id, path, and either line_start plus line_end or symbol",
            "test_fields": "project_id, path, selector",
        },
    }
    if suite["schema_version"] == IMPACT_SUITE_V2_SCHEMA:
        payload["schema_version"] = "impact-blind-input.v2"
        payload["repository_read_rule"] = (
            "Analyze pinned git_head blobs with the authorized read-only tools; ignore working-tree changes, "
            "do not switch checkouts or modify sample repositories. The Chemist runtime uses git cat-file."
        )
        contract = payload["response_contract"]
        contract["schema_version"] = IMPACT_BASELINE_V2_SCHEMA
        contract["project_assessment_required"].insert(2, "requires_change")
        contract["relationship_definition"] = "Existing project relationship, independent of this change's compatibility."
        contract["requires_change_definition"] = (
            "Required boolean: true only if the other project must change to preserve correct behavior for this case. "
            "A direct_dependency may have requires_change=false. Optional regression tests do not imply true. "
            "If uncertain, report incomplete rather than defaulting to false. True requires direct_dependency. "
            "Impact scoring uses requires_change only, never relationship or free-text reason."
        )
        contract["test_fields"] = "project_id, path, exact selector (ClassName.test_method or top-level test_function)"
    return payload


def blind_input_sha256(suite: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(build_blind_input(suite)))


def write_blind_input(*, suite_path: Path, output_path: Path) -> str:
    suite = load_impact_suite(suite_path)
    payload = build_blind_input(suite)
    write_bytes_atomic(output_path, canonical_json_bytes(payload))
    return blind_input_sha256(suite)


def _normalized_path(value: str) -> str:
    return value.replace("\\", "/").strip("/").casefold()


def _evidence_matches(observed: dict[str, Any], required: dict[str, Any]) -> bool:
    if observed["project_id"] != required["project_id"]:
        return False
    observed_path = _normalized_path(observed["path"])
    required_path = _normalized_path(required["path"])
    if observed_path != required_path and not observed_path.endswith(f"/{required_path}"):
        return False
    if observed.get("symbol") and required.get("symbol"):
        if observed["symbol"] == required["symbol"]:
            return True
    if "line_start" not in observed or "line_end" not in observed:
        return False
    return observed["line_start"] <= required["line_end"] and observed["line_end"] >= required["line_start"]


def _test_matches(observed: dict[str, Any], required: dict[str, Any]) -> bool:
    observed_path = _normalized_path(observed["path"])
    required_path = _normalized_path(required["path"])
    return (
        observed["project_id"] == required["project_id"]
        and (observed_path == required_path or observed_path.endswith(f"/{required_path}"))
        and observed["selector"] == required["selector"]
    )


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


def score_impact_baseline(*, suite_path: Path, baseline_path: Path) -> ImpactScore:
    suite = load_impact_suite(suite_path)
    is_v2 = suite["schema_version"] == IMPACT_SUITE_V2_SCHEMA
    baseline = _load(baseline_path, IMPACT_BASELINE_V2_SCHEMA if is_v2 else IMPACT_BASELINE_SCHEMA)
    if baseline["suite_id"] != suite["suite_id"]:
        raise ValueError("Baseline suite_id does not match the frozen suite")
    expected_blind_hash = blind_input_sha256(suite)
    if baseline["blind_protocol"]["blind_input_sha256"] != expected_blind_hash:
        raise ValueError("Baseline was not produced from the current frozen blind input")

    expected_heads = {item["project_id"]: item["git_head"] for item in suite["repositories"]}
    observed_heads = {item["project_id"]: item["git_head"] for item in baseline["observed_repositories"]}
    if len(observed_heads) != len(baseline["observed_repositories"]) or observed_heads != expected_heads:
        raise ValueError("Baseline repository revisions do not match the frozen suite")

    gold_cases = {item["case_id"]: item for item in suite["cases"]}
    baseline_cases = {item["case_id"]: item for item in baseline["cases"]}
    if len(baseline_cases) != len(baseline["cases"]) or set(baseline_cases) != set(gold_cases):
        raise ValueError("Baseline must assess every frozen case exactly once")

    project_ids = set(expected_heads)
    tp = fn = fp = 0
    critical_misses: list[dict[str, str]] = []
    matched_evidence = total_evidence = 0
    matched_tests = total_tests = 0
    evidence_levels: list[dict[str, str]] = []

    for case_id, gold in gold_cases.items():
        assessments = baseline_cases[case_id]["project_assessments"]
        if any(
            item.get("line_start", 1) > item.get("line_end", item.get("line_start", 1))
            for assessment in assessments
            for item in assessment["evidence"]
        ):
            raise ValueError(f"Baseline case {case_id} has an invalid evidence line range")
        by_project = {item["project_id"]: item for item in assessments}
        expected_assessed = project_ids - {gold["change_project"]}
        if len(by_project) != len(assessments) or set(by_project) != expected_assessed:
            raise ValueError(f"Baseline case {case_id} must assess every other project exactly once")
        predicted = {
            project_id for project_id, item in by_project.items()
            if (item["requires_change"] if is_v2 else item["relationship"] == "direct_dependency")
        }
        expected = set(gold["affected_projects"])
        tp += len(predicted & expected)
        fn += len(expected - predicted)
        fp += len(predicted - expected)
        if gold["criticality"] == "critical":
            critical_misses.extend(
                {"case_id": case_id, "project_id": project_id}
                for project_id in sorted(expected - predicted)
            )

        observed_tests = [
            item
            for assessment in assessments
            for item in assessment["required_tests"]
        ]
        total_tests += len(gold["required_tests"])
        matched_tests += sum(
            any(_test_matches(item, requirement) for item in observed_tests)
            for requirement in gold["required_tests"]
        )

        if not expected:
            continue
        observed_evidence = [
            item
            for project_id in expected & predicted
            for item in by_project[project_id]["evidence"]
        ]
        required_evidence = gold["evidence"]
        matches = [
            requirement for requirement in required_evidence
            if any(_evidence_matches(item, requirement) for item in observed_evidence)
        ]
        matched_evidence += len(matches)
        total_evidence += len(required_evidence)
        matched_projects = {item["project_id"] for item in matches}
        expected_sides = {gold["change_project"]} | expected
        if not matched_projects:
            level = "none"
        elif expected_sides <= matched_projects:
            level = "two_sided"
        else:
            level = "one_sided"
        for project_id in sorted(expected):
            evidence_levels.append({"case_id": case_id, "project_id": project_id, "level": level})

    repeated = len(critical_misses) >= 2
    decision = (
        "dependency_graph_candidate_requires_root_cause_review"
        if repeated
        else "do_not_add_dependency_graph_to_mvp"
    )
    return ImpactScore(
        suite_id=suite["suite_id"],
        blind_input_sha256=expected_blind_hash,
        true_positive_projects=tp,
        false_negative_projects=fn,
        false_positive_projects=fp,
        project_recall=_ratio(tp, tp + fn),
        precision=_ratio(tp, tp + fp),
        critical_misses=tuple(critical_misses),
        repeated_critical_misses=repeated,
        evidence_completeness=_ratio(matched_evidence, total_evidence),
        required_test_completeness=_ratio(matched_tests, total_tests),
        evidence_levels=tuple(evidence_levels),
        dependency_graph_decision=decision,
    )
