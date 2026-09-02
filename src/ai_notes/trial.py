from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TrialStatus:
    completed_runs: int
    nominated_runs: int
    discovered_runs: int
    positive_feedback_runs: int
    dual_evidence_errors: int
    eligible_for_automation: bool
    unmet_requirements: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["unmet_requirements"] = list(self.unmet_requirements)
        return payload


def _events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"Learning ledger line {number} is not a JSON object")
        events.append(payload)
    return events


def evaluate_trial(root: Path) -> TrialStatus:
    events = _events(root.resolve() / "data" / "learning" / "ledger.jsonl")
    successful = {
        str(event["run_id"]): str(event.get("entry_mode", "nominated"))
        for event in events
        if event.get("event") == "learning_finalized" and event.get("status") == "success" and event.get("run_id")
    }
    positive_run_ids = {
        str(event["run_id"])
        for event in events
        if event.get("event") == "feedback_recorded"
        and event.get("feedback") in {"continue", "watch", "experiment"}
        and event.get("run_id") in successful
    }
    error_attempts = {
        (str(event.get("run_id")), str(event.get("attempt_sha256")))
        for event in events
        if event.get("event") == "learning_review_failed" and event.get("error_class") == "dual_evidence"
    }
    nominated = sum(mode == "nominated" for mode in successful.values())
    discovered = sum(mode == "discovered" for mode in successful.values())
    unmet: list[str] = []
    if len(successful) < 10:
        unmet.append("completed_runs<10")
    if nominated < 3:
        unmet.append("nominated_runs<3")
    if discovered < 3:
        unmet.append("discovered_runs<3")
    if len(positive_run_ids) < 6:
        unmet.append("positive_feedback_runs<6")
    if len(error_attempts) > 1:
        unmet.append("dual_evidence_errors>1")
    return TrialStatus(
        completed_runs=len(successful),
        nominated_runs=nominated,
        discovered_runs=discovered,
        positive_feedback_runs=len(positive_run_ids),
        dual_evidence_errors=len(error_attempts),
        eligible_for_automation=not unmet,
        unmet_requirements=tuple(unmet),
    )
