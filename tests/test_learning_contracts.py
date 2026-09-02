from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_notes.contracts import (
    DECISIONS_SCHEMA,
    MANIFEST_SCHEMA,
    QUEUE_SCHEMA,
    ContractValidationError,
    validate_contract,
)
from ai_notes.storage import append_jsonl_atomic, prune_learning_artifacts, resolve_within


RUN_ID = "20260902T083000Z-0123abcd"
SHA40 = "a" * 40
SHA64 = "b" * 64


def valid_queue() -> dict[str, object]:
    return {
        "schema_version": QUEUE_SCHEMA,
        "run_id": RUN_ID,
        "created_at": "2026-09-02T08:30:00Z",
        "policy_version": "1",
        "input": {
            "url": "https://github.com/openai/codex",
            "kind": "repository",
            "canonical_repository": "openai/codex",
            "canonical_url": "https://github.com/openai/codex",
        },
        "verified_target": {
            "commit_sha": SHA40,
            "ref": "main",
            "object_url": "https://github.com/openai/codex/tree/" + SHA40,
            "verified_at": "2026-09-02T08:30:00Z",
        },
        "source_risk": {
            "fork": False,
            "upstream_repository": None,
            "archived": False,
            "license_spdx": "Apache-2.0",
            "learning_only": False,
            "reasons": [],
        },
        "external_evidence": [
            {
                "evidence_id": "ext-0123456789ab",
                "kind": "readme",
                "official_url": "https://github.com/openai/codex/blob/" + SHA40 + "/README.md",
                "commit_sha": SHA40,
                "title": "README.md",
                "text": "Codex is a coding agent.",
                "text_sha256": SHA64,
            }
        ],
        "owned_project": {
            "name": "ai-notes",
            "root": "E:/devlop/ai-notes",
            "repository_id": "ai-notes",
            "git_head": SHA40,
            "working_tree_fingerprint": SHA64,
            "focus_files": [{"path": "README.md", "sha256": SHA64, "size": 100}],
        },
        "missing_scopes": [],
    }


def valid_decisions() -> dict[str, object]:
    return {
        "schema_version": DECISIONS_SCHEMA,
        "run_id": RUN_ID,
        "queue_sha256": SHA64,
        "project_understanding": {
            "problem": "帮助开发者使用 Agent 完成编码工作。",
            "core_abstractions": [
                {
                    "claim": "任务由 Agent 执行。",
                    "evidence": [{"evidence_id": "ext-0123456789ab", "quote": "Codex is a coding agent."}],
                }
            ],
            "architecture": [
                {
                    "claim": "CLI 是主要入口。",
                    "evidence": [{"evidence_id": "ext-0123456789ab", "quote": "Codex is a coding agent."}],
                }
            ],
            "non_applicable_conditions": [],
        },
        "risks": {
            "learning_only": False,
            "license_notes": [],
            "security_notes": [],
            "maintenance_notes": [],
        },
        "connections": [],
        "no_connection_reason": "尚无双侧证据。",
        "next_actions": ["continue", "ignore", "watch"],
    }


def valid_manifest() -> dict[str, object]:
    return {
        "schema_version": MANIFEST_SCHEMA,
        "run_id": RUN_ID,
        "started_at": "2026-09-02T08:30:00Z",
        "finished_at": "2026-09-02T08:31:00Z",
        "status": "success",
        "healthy_no_connection": True,
        "policy_version": "1",
        "queue_path": "outputs/learning/run/learning-queue.json",
        "decisions_path": "outputs/learning/run/learning-decisions.json",
        "queue_sha256": SHA64,
        "decisions_sha256": SHA64,
        "missing_scopes": [],
        "validation_errors": [],
        "retained_until": "2026-10-02T08:30:00Z",
    }


class LearningContractTests(unittest.TestCase):
    def test_all_v1_contracts_accept_strict_valid_payloads(self) -> None:
        self.assertEqual(RUN_ID, validate_contract(QUEUE_SCHEMA, valid_queue())["run_id"])
        self.assertEqual(RUN_ID, validate_contract(DECISIONS_SCHEMA, valid_decisions())["run_id"])
        self.assertEqual(RUN_ID, validate_contract(MANIFEST_SCHEMA, valid_manifest())["run_id"])

    def test_contracts_reject_unknown_fields(self) -> None:
        payload = valid_queue()
        payload["unexpected"] = True

        with self.assertRaises(ContractValidationError):
            validate_contract(QUEUE_SCHEMA, payload)

    def test_owned_project_paths_cannot_escape_authorized_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "inside.txt").write_text("inside", encoding="utf-8")
            self.assertEqual((root / "inside.txt").resolve(), resolve_within(root, "inside.txt"))
            with self.assertRaises(ValueError):
                resolve_within(root, "../outside.txt")

    def test_jsonl_append_is_valid_and_artifact_pruning_uses_created_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / "data" / "learning" / "ledger.jsonl"
            append_jsonl_atomic(ledger, {"event": "one"})
            append_jsonl_atomic(ledger, {"event": "two"})
            events = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]

            old = root / "data" / "learning" / "raw" / "old-run"
            recent = root / "outputs" / "learning" / "recent-run"
            old.mkdir(parents=True)
            recent.mkdir(parents=True)
            (old / "created-at.txt").write_text("2026-07-01T00:00:00Z", encoding="utf-8")
            (recent / "created-at.txt").write_text("2026-08-20T00:00:00Z", encoding="utf-8")

            removed = prune_learning_artifacts(root, as_of=date(2026, 9, 2), retention_days=30)

        self.assertEqual([{"event": "one"}, {"event": "two"}], events)
        self.assertEqual(["data/learning/raw/old-run"], removed)


if __name__ == "__main__":
    unittest.main()
