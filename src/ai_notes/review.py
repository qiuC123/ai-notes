from __future__ import annotations

import html
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import yaml

from ai_notes.contracts import (
    DECISIONS_SCHEMA,
    MANIFEST_SCHEMA,
    QUEUE_SCHEMA,
    ContractValidationError,
    validate_contract,
)
from ai_notes.learning import load_learning_policy
from ai_notes.storage import (
    RunLock,
    append_jsonl_atomic,
    project_fingerprint,
    resolve_within,
    sha256_bytes,
    sha256_file,
    utc_now,
    write_json_atomic,
)


_SPACE = re.compile(r"\s+")
_FEEDBACK = {"continue", "ignore", "watch", "experiment"}


def _review_error_class(message: str) -> str:
    evidence_markers = (
        "Queued external evidence hash changed",
        "references unknown external evidence",
        "External quote does not match",
        "Owned-project evidence changed",
        "Owned-project line range",
        "Owned-project evidence requires",
    )
    if any(marker in message for marker in evidence_markers) or (
        "validation failed at" in message and ".evidence" in message
    ):
        return "dual_evidence"
    return "other"


@dataclass(frozen=True, slots=True)
class FinalizeResult:
    run_id: str
    manifest_path: Path
    decisions_path: Path | None
    status: str
    healthy_no_connection: bool
    validation_errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FeedbackResult:
    run_id: str
    feedback: str
    recorded: bool
    relation_id: str | None


def normalize_evidence(value: str) -> str:
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", html.unescape(value or ""))).strip()


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def _retained_until(now: datetime, days: int) -> str:
    return (now.astimezone(UTC) + timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_manifest(
    *,
    root: Path,
    path: Path,
    queue: dict[str, Any] | None,
    run_id: str,
    status: str,
    healthy_no_connection: bool,
    decisions_path: Path | None,
    queue_sha256: str | None,
    decisions_sha256: str | None,
    missing_scopes: Iterable[str],
    validation_errors: Iterable[str],
    now: datetime,
    retention_days: int,
) -> dict[str, Any]:
    payload = {
        "schema_version": MANIFEST_SCHEMA,
        "run_id": run_id,
        "started_at": str(queue["created_at"]) if queue is not None else now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "finished_at": now.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": status,
        "healthy_no_connection": healthy_no_connection,
        "policy_version": str(queue["policy_version"]) if queue is not None else "1",
        "queue_path": _relative(root, root / "outputs" / "learning" / run_id / "learning-queue.json"),
        "decisions_path": _relative(root, decisions_path) if decisions_path is not None else None,
        "queue_sha256": queue_sha256,
        "decisions_sha256": decisions_sha256,
        "missing_scopes": sorted(set(missing_scopes)),
        "validation_errors": list(validation_errors),
        "retained_until": _retained_until(now, retention_days),
    }
    validate_contract(MANIFEST_SCHEMA, payload)
    write_json_atomic(path, payload)
    return payload


def _evidence_references(decisions: dict[str, Any]) -> Iterable[dict[str, str]]:
    understanding = decisions["project_understanding"]
    for group in (understanding["core_abstractions"], understanding["architecture"]):
        for claim in group:
            yield from claim["evidence"]
    for connection in decisions["connections"]:
        yield from connection["external_capability"]["evidence"]


def _validate_cross_contracts(root: Path, queue: dict[str, Any], decisions: dict[str, Any], queue_hash: str) -> None:
    if decisions["run_id"] != queue["run_id"]:
        raise ContractValidationError("Decision run_id does not match the prepared queue")
    if decisions["queue_sha256"] != queue_hash:
        raise ContractValidationError("Decision queue_sha256 does not match the prepared queue")

    evidence_by_id = {item["evidence_id"]: item for item in queue["external_evidence"]}
    for item in evidence_by_id.values():
        if sha256_bytes(item["text"].encode("utf-8")) != item["text_sha256"]:
            raise ContractValidationError(f"Queued external evidence hash changed: {item['evidence_id']}")
    for reference in _evidence_references(decisions):
        evidence_id = reference["evidence_id"]
        source = evidence_by_id.get(evidence_id)
        if source is None:
            raise ContractValidationError(f"Decision references unknown external evidence: {evidence_id}")
        quote = normalize_evidence(reference["quote"])
        if not quote or quote not in normalize_evidence(source["text"]):
            raise ContractValidationError(f"External quote does not match queued evidence: {evidence_id}")

    connections = decisions["connections"]
    no_connection_reason = decisions["no_connection_reason"]
    if connections and no_connection_reason is not None:
        raise ContractValidationError("A connected decision must set no_connection_reason to null")
    if not connections and (not isinstance(no_connection_reason, str) or not no_connection_reason.strip()):
        raise ContractValidationError("A decision without a connection requires no_connection_reason")
    if not connections and any(action in {"ignore", "experiment"} for action in decisions["next_actions"]):
        raise ContractValidationError("A decision without a relation cannot suggest ignore or experiment")

    owned_root = Path(queue["owned_project"]["root"]).resolve()
    registry_path = root / "data" / "learning" / "projects.yaml"
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8")) if registry_path.exists() else {}
    registered_paths = {
        str(Path(item["path"]).resolve())
        for item in registry.get("projects", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    } if isinstance(registry, dict) else set()
    if str(owned_root) not in registered_paths:
        raise ContractValidationError(f"Owned project is not present in the local authorization registry: {owned_root}")
    current_project = project_fingerprint(owned_root)
    if current_project["git_head"] != queue["owned_project"]["git_head"]:
        raise ContractValidationError("Owned-project Git HEAD changed after the learning queue was prepared")
    if current_project["working_tree_fingerprint"] != queue["owned_project"]["working_tree_fingerprint"]:
        raise ContractValidationError("Owned-project working tree changed after the learning queue was prepared")
    for connection in connections:
        for reference in connection["owned_project_problem"]["evidence"]:
            path = resolve_within(owned_root, reference["path"])
            if sha256_file(path) != reference["file_sha256"]:
                raise ContractValidationError(f"Owned-project evidence changed: {reference['path']}")
            start = reference["start_line"]
            end = reference["end_line"]
            symbol = reference["symbol"]
            if (start is None) != (end is None):
                raise ContractValidationError(f"Owned-project line range must include both endpoints: {reference['path']}")
            if start is None and not symbol:
                raise ContractValidationError(f"Owned-project evidence requires a symbol or line range: {reference['path']}")
            if start is not None:
                line_count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
                if end < start or end > max(line_count, 1):
                    raise ContractValidationError(f"Owned-project evidence line range is invalid: {reference['path']}")

    source_learning_only = bool(queue["source_risk"]["learning_only"])
    decision_learning_only = bool(decisions["risks"]["learning_only"])
    if source_learning_only and not decision_learning_only:
        raise ContractValidationError("Source risk requires the decision to remain learning-only")
    if source_learning_only or decision_learning_only:
        if "experiment" in decisions["next_actions"]:
            raise ContractValidationError("Learning-only decisions cannot suggest an experiment")
        if any(connection["experiment"] is not None for connection in connections):
            raise ContractValidationError("Learning-only connections cannot define an experiment")


def finalize_learning(
    *,
    root: Path,
    run_id: str,
    input_decisions_path: Path,
    now: datetime | None = None,
) -> FinalizeResult:
    resolved_root = root.resolve()
    current = now or datetime.now(UTC)
    policy = load_learning_policy(resolved_root / "config" / "ai_notes_learning.yaml")
    output_dir = resolved_root / "outputs" / "learning" / run_id
    queue_path = output_dir / "learning-queue.json"
    canonical_decisions_path = output_dir / "learning-decisions.json"
    manifest_path = output_dir / "learning-run-manifest.json"
    lock_path = resolved_root / "data" / "learning" / "ai-notes-learning.lock"

    with RunLock(lock_path):
        if not queue_path.exists():
            raise ValueError(f"Prepared learning queue does not exist: {run_id}")
        queue_hash = sha256_file(queue_path)
        queue: dict[str, Any] | None = None
        raw_decisions = b""
        try:
            queue = validate_contract(QUEUE_SCHEMA, json.loads(queue_path.read_text(encoding="utf-8")))
            raw_decisions = input_decisions_path.read_bytes()
            decisions = validate_contract(DECISIONS_SCHEMA, json.loads(raw_decisions.decode("utf-8")))
            _validate_cross_contracts(resolved_root, queue, decisions, queue_hash)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ContractValidationError, ValueError) as error:
            message = f"{type(error).__name__}: {error}"
            _write_manifest(
                root=resolved_root,
                path=manifest_path,
                queue=queue,
                run_id=run_id,
                status="review_failed",
                healthy_no_connection=False,
                decisions_path=None,
                queue_sha256=queue_hash,
                decisions_sha256=None,
                missing_scopes=queue["missing_scopes"] if queue is not None else [],
                validation_errors=[message],
                now=current,
                retention_days=policy.retention_days,
            )
            attempt_sha256 = sha256_bytes(raw_decisions or message.encode("utf-8"))
            failure_event = {
                "event": "learning_review_failed",
                "recorded_at": current.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "run_id": run_id,
                "attempt_sha256": attempt_sha256,
                "error_class": _review_error_class(message),
            }
            prior_events = _ledger_events(resolved_root / "data" / "learning" / "ledger.jsonl")
            if not any(
                item.get("event") == "learning_review_failed"
                and item.get("run_id") == run_id
                and item.get("attempt_sha256") == attempt_sha256
                for item in prior_events
            ):
                append_jsonl_atomic(resolved_root / "data" / "learning" / "ledger.jsonl", failure_event)
            return FinalizeResult(run_id, manifest_path, None, "review_failed", False, (message,))

        assert queue is not None
        if manifest_path.exists() and canonical_decisions_path.exists():
            existing_manifest = validate_contract(
                MANIFEST_SCHEMA, json.loads(manifest_path.read_text(encoding="utf-8"))
            )
            rendered_hash = sha256_bytes(
                (json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
            )
            if existing_manifest["decisions_sha256"] == rendered_hash:
                return FinalizeResult(
                    run_id,
                    manifest_path,
                    canonical_decisions_path,
                    str(existing_manifest["status"]),
                    bool(existing_manifest["healthy_no_connection"]),
                    tuple(existing_manifest["validation_errors"]),
                )
            raise ValueError("Finalized learning run cannot be replaced with conflicting decisions")

        write_json_atomic(canonical_decisions_path, decisions)
        decisions_hash = sha256_file(canonical_decisions_path)
        status = "partial" if queue["missing_scopes"] else "success"
        healthy_no_connection = status == "success" and not decisions["connections"]
        _write_manifest(
            root=resolved_root,
            path=manifest_path,
            queue=queue,
            run_id=run_id,
            status=status,
            healthy_no_connection=healthy_no_connection,
            decisions_path=canonical_decisions_path,
            queue_sha256=queue_hash,
            decisions_sha256=decisions_hash,
            missing_scopes=queue["missing_scopes"],
            validation_errors=[],
            now=current,
            retention_days=policy.retention_days,
        )
        append_jsonl_atomic(
            resolved_root / "data" / "learning" / "ledger.jsonl",
            {
                "event": "learning_finalized",
                "recorded_at": current.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "run_id": run_id,
                "repository": queue["input"]["canonical_repository"],
                "entry_mode": queue["input"]["entry_mode"],
                "commit_sha": queue["verified_target"]["commit_sha"],
                "status": status,
                "relation_ids": [item["relation_id"] for item in decisions["connections"]],
                "external_evidence_ids": sorted({item["evidence_id"] for item in _evidence_references(decisions)}),
            },
        )
        return FinalizeResult(run_id, manifest_path, canonical_decisions_path, status, healthy_no_connection, ())


def _ledger_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            events.append(payload)
    return events


def record_feedback(
    *,
    root: Path,
    run_id: str,
    feedback: str,
    now: datetime | None = None,
) -> FeedbackResult:
    resolved_root = root.resolve()
    with RunLock(resolved_root / "data" / "learning" / "ai-notes-learning.lock"):
        return _record_feedback_unlocked(root=resolved_root, run_id=run_id, feedback=feedback, now=now)


def _record_feedback_unlocked(
    *,
    root: Path,
    run_id: str,
    feedback: str,
    now: datetime | None = None,
) -> FeedbackResult:
    if feedback not in _FEEDBACK:
        raise ValueError(f"Unsupported feedback: {feedback}")
    resolved_root = root.resolve()
    current = now or datetime.now(UTC)
    output_dir = resolved_root / "outputs" / "learning" / run_id
    queue = validate_contract(QUEUE_SCHEMA, json.loads((output_dir / "learning-queue.json").read_text(encoding="utf-8")))
    decisions = validate_contract(
        DECISIONS_SCHEMA, json.loads((output_dir / "learning-decisions.json").read_text(encoding="utf-8"))
    )
    manifest = validate_contract(
        MANIFEST_SCHEMA, json.loads((output_dir / "learning-run-manifest.json").read_text(encoding="utf-8"))
    )
    if manifest["status"] not in {"success", "partial"}:
        raise ValueError("Feedback requires a successfully finalized learning run")
    connections = decisions["connections"]
    relation_id = connections[0]["relation_id"] if connections else None
    if feedback in {"ignore", "experiment"} and relation_id is None:
        raise ValueError(f"{feedback} feedback requires a concrete relation")
    if feedback == "experiment" and connections[0]["experiment"] is None:
        raise ValueError("experiment feedback requires a validated experiment in the learning decision")
    if feedback == "experiment" and (queue["source_risk"]["learning_only"] or decisions["risks"]["learning_only"]):
        raise ValueError("Learning-only projects cannot be moved to experiment")

    ledger_path = resolved_root / "data" / "learning" / "ledger.jsonl"
    event = {
        "event": "feedback_recorded",
        "recorded_at": current.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "run_id": run_id,
        "feedback": feedback,
        "relation_id": relation_id,
        "repository": queue["input"]["canonical_repository"],
        "cooldown_until": (
            (current.astimezone(UTC) + timedelta(days=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            if feedback == "ignore"
            else None
        ),
    }
    comparable = {key: event[key] for key in ("event", "run_id", "feedback", "relation_id", "repository")}
    for prior in _ledger_events(ledger_path):
        if all(prior.get(key) == value for key, value in comparable.items()):
            return FeedbackResult(run_id, feedback, False, relation_id)
    append_jsonl_atomic(ledger_path, event)
    return FeedbackResult(run_id, feedback, True, relation_id)
