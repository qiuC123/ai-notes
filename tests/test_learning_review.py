from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_notes.storage import project_fingerprint, sha256_bytes, sha256_file, write_json_atomic
from ai_notes.review import finalize_learning, record_feedback


RUN_ID = "20260902T083000Z-0123abcd"
SHA = "1" * 40
EVIDENCE_ID = "ext-0123456789ab"
RELATION_ID = "rel-0123456789ab"
NOW = datetime(2026, 9, 2, 8, 31, tzinfo=UTC)


def write_policy(root: Path) -> None:
    config = root / "config"
    config.mkdir(parents=True)
    (config / "ai_notes_learning.yaml").write_text(
        'policy_version: "1"\nretention_days: 30\n', encoding="utf-8"
    )


def write_prepared_run(root: Path, *, learning_only: bool = False) -> tuple[Path, Path]:
    project = root / "owned"
    project.mkdir()
    owned_file = project / "README.md"
    owned_file.write_text("# Ai Notes\n\nGitHub co-learning is not implemented yet.\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "initial",
        ],
        check=True,
    )
    fingerprint = project_fingerprint(project)
    evidence_text = "Codex is a coding agent that runs locally and can inspect a repository."
    queue = {
        "schema_version": "learning-queue.v1",
        "run_id": RUN_ID,
        "created_at": "2026-09-02T08:30:00Z",
        "policy_version": "1",
        "input": {
            "url": "https://github.com/openai/codex",
            "entry_mode": "nominated",
            "kind": "repository",
            "canonical_repository": "openai/codex",
            "canonical_url": "https://github.com/openai/codex",
        },
        "verified_target": {
            "commit_sha": SHA,
            "ref": "main",
            "object_url": f"https://github.com/openai/codex/tree/{SHA}",
            "verified_at": "2026-09-02T08:30:00Z",
        },
        "source_risk": {
            "fork": False,
            "upstream_repository": None,
            "archived": learning_only,
            "license_spdx": None if learning_only else "Apache-2.0",
            "learning_only": learning_only,
            "reasons": ["archived_repository"] if learning_only else [],
        },
        "external_evidence": [
            {
                "evidence_id": EVIDENCE_ID,
                "kind": "readme",
                "official_url": f"https://github.com/openai/codex/blob/{SHA}/README.md",
                "commit_sha": SHA,
                "title": "README.md",
                "text": evidence_text,
                "text_sha256": sha256_bytes(evidence_text.encode("utf-8")),
            }
        ],
        "owned_project": {
            "name": fingerprint["name"],
            "root": fingerprint["root"],
            "repository_id": fingerprint["repository_id"],
            "git_head": fingerprint["git_head"],
            "working_tree_fingerprint": fingerprint["working_tree_fingerprint"],
            "focus_files": [
                {"path": "README.md", "sha256": sha256_file(owned_file), "size": owned_file.stat().st_size}
            ],
        },
        "missing_scopes": [],
    }
    output = root / "outputs" / "learning" / RUN_ID
    queue_path = output / "learning-queue.json"
    write_json_atomic(queue_path, queue)
    registry = root / "data" / "learning" / "projects.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        f"projects:\n  - name: owned\n    path: '{project.resolve()}'\n    repository_id: owned\n    reason: test\n",
        encoding="utf-8",
    )
    return project, queue_path


def decisions(queue_path: Path, project: Path, *, with_connection: bool = True, learning_only: bool = False) -> dict[str, object]:
    quote = "Codex is a coding agent"
    payload: dict[str, object] = {
        "schema_version": "learning-decisions.v1",
        "run_id": RUN_ID,
        "queue_sha256": sha256_file(queue_path),
        "project_understanding": {
            "problem": "Codex helps a developer work with a repository.",
            "core_abstractions": [
                {"claim": "Codex is a local coding agent.", "evidence": [{"evidence_id": EVIDENCE_ID, "quote": quote}]}
            ],
            "architecture": [
                {"claim": "The repository is the working context.", "evidence": [{"evidence_id": EVIDENCE_ID, "quote": quote}]}
            ],
            "non_applicable_conditions": [],
        },
        "risks": {
            "learning_only": learning_only,
            "license_notes": [],
            "security_notes": [],
            "maintenance_notes": [],
        },
        "connections": [],
        "no_connection_reason": "No verified connection yet.",
        "next_actions": ["continue", "watch"],
    }
    if with_connection:
        owned = project / "README.md"
        payload["connections"] = [
            {
                "relation_id": RELATION_ID,
                "title": "Use a repository-aware co-learning task",
                "hypothesis": "Codex repository context can power Ai Notes learning.",
                "external_capability": {
                    "claim": "Codex can inspect a repository.",
                    "evidence": [{"evidence_id": EVIDENCE_ID, "quote": "can inspect a repository"}],
                },
                "owned_project_problem": {
                    "status": "confirmed",
                    "claim": "Ai Notes has not implemented co-learning.",
                    "evidence": [
                        {
                            "path": "README.md",
                            "symbol": None,
                            "start_line": 3,
                            "end_line": 3,
                            "file_sha256": sha256_file(owned),
                            "description": "README states that the capability is not implemented.",
                        }
                    ],
                },
                "expected_benefit": "Connect external project capabilities to current work.",
                "costs": ["Requires a deterministic evidence contract."],
                "risks": [],
                "experiment": None if learning_only else {
                    "objective": "Generate one evidence-gated learning card.",
                    "success_criteria": ["Every claim has matching evidence."],
                },
            }
        ]
        payload["no_connection_reason"] = None
        payload["next_actions"] = ["continue", "ignore", "watch"] if learning_only else [
            "continue",
            "ignore",
            "watch",
            "experiment",
        ]
    return payload


class LearningReviewTests(unittest.TestCase):
    def test_finalize_validates_dual_evidence_writes_manifest_and_operational_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_policy(root)
            project, queue_path = write_prepared_run(root)
            input_path = root / "decision-input.json"
            write_json_atomic(input_path, decisions(queue_path, project))

            result = finalize_learning(root=root, run_id=RUN_ID, input_decisions_path=input_path, now=NOW)
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            ledger = (root / "data" / "learning" / "ledger.jsonl").read_text(encoding="utf-8")
            queue_hash = sha256_file(queue_path)

        self.assertEqual("success", result.status)
        self.assertFalse(result.healthy_no_connection)
        self.assertEqual(RELATION_ID, json.loads(ledger)["relation_ids"][0])
        self.assertEqual(queue_hash, manifest["queue_sha256"])
        self.assertNotIn("Codex helps", ledger)

    def test_success_can_be_a_healthy_no_connection_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_policy(root)
            project, queue_path = write_prepared_run(root)
            input_path = root / "decision-input.json"
            write_json_atomic(input_path, decisions(queue_path, project, with_connection=False))

            result = finalize_learning(root=root, run_id=RUN_ID, input_decisions_path=input_path, now=NOW)

        self.assertEqual("success", result.status)
        self.assertTrue(result.healthy_no_connection)

    def test_mismatched_external_quote_is_review_failed_and_keeps_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_policy(root)
            project, queue_path = write_prepared_run(root)
            payload = decisions(queue_path, project)
            payload["project_understanding"]["architecture"][0]["evidence"][0]["quote"] = "invented quote"
            input_path = root / "decision-input.json"
            write_json_atomic(input_path, payload)

            result = finalize_learning(root=root, run_id=RUN_ID, input_decisions_path=input_path, now=NOW)
            replay = finalize_learning(root=root, run_id=RUN_ID, input_decisions_path=input_path, now=NOW)
            queue_preserved = queue_path.exists()
            canonical_absent = not (queue_path.parent / "learning-decisions.json").exists()
            failure_events = [
                json.loads(line)
                for line in (root / "data" / "learning" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual("review_failed", result.status)
        self.assertEqual("review_failed", replay.status)
        self.assertTrue(queue_preserved)
        self.assertTrue(canonical_absent)
        self.assertIn("does not match", result.validation_errors[0])
        self.assertEqual(1, len(failure_events))
        self.assertEqual("dual_evidence", failure_events[0]["error_class"])

    def test_malformed_decision_json_is_review_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_policy(root)
            _, _ = write_prepared_run(root)
            input_path = root / "decision-input.json"
            input_path.write_text("{not-json", encoding="utf-8")

            result = finalize_learning(root=root, run_id=RUN_ID, input_decisions_path=input_path, now=NOW)

        self.assertEqual("review_failed", result.status)
        self.assertIn("JSONDecodeError", result.validation_errors[0])

    def test_same_finalization_is_idempotent_but_conflicting_replacement_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_policy(root)
            project, queue_path = write_prepared_run(root)
            payload = decisions(queue_path, project)
            input_path = root / "decision-input.json"
            write_json_atomic(input_path, payload)
            first = finalize_learning(root=root, run_id=RUN_ID, input_decisions_path=input_path, now=NOW)
            replay = finalize_learning(root=root, run_id=RUN_ID, input_decisions_path=input_path, now=NOW)
            payload["project_understanding"]["problem"] = "Conflicting replacement"
            write_json_atomic(input_path, payload)
            with self.assertRaises(ValueError):
                finalize_learning(root=root, run_id=RUN_ID, input_decisions_path=input_path, now=NOW)
            events = (root / "data" / "learning" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()

        self.assertEqual("success", first.status)
        self.assertEqual("success", replay.status)
        self.assertEqual(1, len(events))

    def test_changed_owned_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_policy(root)
            project, queue_path = write_prepared_run(root)
            payload = decisions(queue_path, project)
            (project / "README.md").write_text("changed\n", encoding="utf-8")
            input_path = root / "decision-input.json"
            write_json_atomic(input_path, payload)

            result = finalize_learning(root=root, run_id=RUN_ID, input_decisions_path=input_path, now=NOW)

        self.assertEqual("review_failed", result.status)
        self.assertIn("working tree changed", result.validation_errors[0])

    def test_already_dirty_owned_file_content_change_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_policy(root)
            project, _ = write_prepared_run(root)
            owned = project / "README.md"
            owned.write_text("first dirty version\n", encoding="utf-8")
            fingerprint = project_fingerprint(project)
            run_dir = root / "outputs" / "learning" / RUN_ID
            queue_path = run_dir / "learning-queue.json"
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            queue["owned_project"]["working_tree_fingerprint"] = fingerprint["working_tree_fingerprint"]
            queue["owned_project"]["focus_files"][0]["sha256"] = sha256_file(owned)
            queue["owned_project"]["focus_files"][0]["size"] = owned.stat().st_size
            write_json_atomic(queue_path, queue)
            payload = decisions(queue_path, project)
            owned.write_text("second dirty version\n", encoding="utf-8")
            input_path = root / "decision-input.json"
            write_json_atomic(input_path, payload)

            result = finalize_learning(root=root, run_id=RUN_ID, input_decisions_path=input_path, now=NOW)

        self.assertEqual("review_failed", result.status)
        self.assertIn("working tree changed", result.validation_errors[0])

    def test_unregistered_owned_project_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_policy(root)
            project, queue_path = write_prepared_run(root)
            (root / "data" / "learning" / "projects.yaml").unlink()
            input_path = root / "decision-input.json"
            write_json_atomic(input_path, decisions(queue_path, project))

            result = finalize_learning(root=root, run_id=RUN_ID, input_decisions_path=input_path, now=NOW)

        self.assertEqual("review_failed", result.status)
        self.assertIn("authorization registry", result.validation_errors[0])

    def test_learning_only_source_blocks_experiment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_policy(root)
            project, queue_path = write_prepared_run(root, learning_only=True)
            payload = decisions(queue_path, project, learning_only=False)
            input_path = root / "decision-input.json"
            write_json_atomic(input_path, payload)

            result = finalize_learning(root=root, run_id=RUN_ID, input_decisions_path=input_path, now=NOW)

        self.assertEqual("review_failed", result.status)
        self.assertIn("learning-only", result.validation_errors[0])

    def test_explicit_feedback_is_idempotent_and_ignore_sets_relation_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_policy(root)
            project, queue_path = write_prepared_run(root)
            input_path = root / "decision-input.json"
            write_json_atomic(input_path, decisions(queue_path, project))
            finalize_learning(root=root, run_id=RUN_ID, input_decisions_path=input_path, now=NOW)

            first = record_feedback(root=root, run_id=RUN_ID, feedback="ignore", now=NOW)
            replay = record_feedback(root=root, run_id=RUN_ID, feedback="ignore", now=NOW)
            events = [
                json.loads(line)
                for line in (root / "data" / "learning" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertTrue(first.recorded)
        self.assertFalse(replay.recorded)
        self.assertEqual(RELATION_ID, first.relation_id)
        self.assertEqual("2026-10-02T08:31:00Z", events[-1]["cooldown_until"])
        self.assertEqual(2, len(events))

    def test_experiment_feedback_requires_a_validated_experiment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_policy(root)
            project, queue_path = write_prepared_run(root)
            payload = decisions(queue_path, project)
            payload["connections"][0]["experiment"] = None
            payload["next_actions"] = ["continue", "ignore", "watch"]
            input_path = root / "decision-input.json"
            write_json_atomic(input_path, payload)
            finalize_learning(root=root, run_id=RUN_ID, input_decisions_path=input_path, now=NOW)

            with self.assertRaisesRegex(ValueError, "validated experiment"):
                record_feedback(root=root, run_id=RUN_ID, feedback="experiment", now=NOW)


if __name__ == "__main__":
    unittest.main()
