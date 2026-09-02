from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aihot.release_sources import ReleaseRecord, normalize_evidence_text


_DECISION_KEYS = {
    "release_key",
    "decision",
    "change_types",
    "substantive_changes",
    "decision_reason",
    "pending_verification",
}
_CHANGE_KEYS = {"summary_zh", "evidence"}
_CHANGE_TYPES = {"feature", "security", "deprecation", "breaking", "deployment", "high_impact_fix"}


class ReviewValidationError(ValueError):
    pass


class ReviewExecutionError(RuntimeError):
    pass


def review_in_batches(
    reviewer: object,
    records: list[ReleaseRecord],
    *,
    batch_size: int = 5,
) -> dict[str, Any]:
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")
    decisions: list[object] = []
    for start in range(0, len(records), batch_size):
        payload = reviewer.review(records[start:start + batch_size])
        if not isinstance(payload, dict) or set(payload) != {"decisions"} or not isinstance(payload["decisions"], list):
            raise ReviewExecutionError("Reviewer batch did not return a decisions list")
        decisions.extend(payload["decisions"])
    return {"decisions": decisions}


@dataclass(frozen=True, slots=True)
class PendingDecisionReviewer:
    """Stops at the artifact boundary until Codex supplies a decisions file."""

    def review(self, records: list[ReleaseRecord]) -> dict[str, Any]:
        if not records:
            return {"decisions": []}
        raise ReviewExecutionError(
            "Codex review decisions are required; inspect review-queue.json and rerun with --decisions"
        )


@dataclass(frozen=True, slots=True)
class DecisionFileReviewer:
    """Selects strict decisions for each bounded batch without calling a model."""

    path: Path

    def _load(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ReviewExecutionError(f"Decision file could not be read: {type(error).__name__}: {error}") from error
        if not isinstance(payload, dict) or set(payload) != {"decisions"} or not isinstance(payload["decisions"], list):
            raise ReviewExecutionError("Decision file must contain only a decisions list")
        if any(not isinstance(item, dict) for item in payload["decisions"]):
            raise ReviewExecutionError("Decision file contains a non-object decision")
        return payload["decisions"]

    def review(self, records: list[ReleaseRecord]) -> dict[str, Any]:
        requested = {record.release_key for record in records}
        selected = [item for item in self._load() if item.get("release_key") in requested]
        selected_keys = [item.get("release_key") for item in selected]
        if len(selected_keys) != len(set(selected_keys)) or set(selected_keys) != requested:
            missing = sorted(requested - set(selected_keys))
            raise ReviewExecutionError(f"Decision file does not decide the requested batch; missing={missing}")
        return {"decisions": selected}


def finalize_decisions(records: list[ReleaseRecord], payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or set(payload) != {"decisions"} or not isinstance(payload["decisions"], list):
        raise ReviewValidationError("Review output must be an object containing only a decisions list")
    records_by_key = {record.release_key: record for record in records}
    decisions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in payload["decisions"]:
        if not isinstance(raw, dict) or set(raw) != _DECISION_KEYS:
            raise ReviewValidationError("Review decision has missing or unknown fields")
        key = raw.get("release_key")
        if not isinstance(key, str) or key not in records_by_key or key in seen:
            raise ReviewValidationError(f"Unknown or duplicate release_key: {key}")
        seen.add(key)
        decision = raw.get("decision")
        if decision not in {"accept", "reject"}:
            raise ReviewValidationError(f"Invalid review decision for {key}")
        change_types = raw.get("change_types")
        changes = raw.get("substantive_changes")
        if not isinstance(change_types, list) or any(item not in _CHANGE_TYPES for item in change_types):
            raise ReviewValidationError(f"Invalid change types for {key}")
        if not isinstance(changes, list):
            raise ReviewValidationError(f"Invalid substantive changes for {key}")
        release_text = normalize_evidence_text(records_by_key[key].release_notes_text)
        if decision == "accept" and (not 1 <= len(changes) <= 3 or not change_types):
            raise ReviewValidationError(f"Accepted decision requires 1-3 changes and change types for {key}")
        if decision == "reject" and (changes or change_types):
            raise ReviewValidationError(f"Rejected decision cannot contain changes or change types for {key}")
        for change in changes:
            if not isinstance(change, dict) or set(change) != _CHANGE_KEYS:
                raise ReviewValidationError(f"Invalid change object for {key}")
            summary = change.get("summary_zh")
            evidence = change.get("evidence")
            if not isinstance(summary, str) or not summary.strip() or not isinstance(evidence, str):
                raise ReviewValidationError(f"Incomplete change evidence for {key}")
            normalized_evidence = normalize_evidence_text(evidence)
            if not normalized_evidence or normalized_evidence not in release_text:
                raise ReviewValidationError(f"Evidence does not match official release notes for {key}")
        if not isinstance(raw.get("decision_reason"), str) or not raw["decision_reason"].strip():
            raise ReviewValidationError(f"Missing decision reason for {key}")
        pending = raw.get("pending_verification")
        if not isinstance(pending, list) or any(not isinstance(item, str) for item in pending):
            raise ReviewValidationError(f"Invalid pending verification for {key}")
        decisions.append(dict(raw))
    if seen != set(records_by_key):
        raise ReviewValidationError("Review output did not decide every queued release")
    return decisions
