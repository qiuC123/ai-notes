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

    def qualifies(event: dict[str, Any]) -> bool:
        if event.get("event") != "learning_finalized" or event.get("status") != "success" or not event.get("run_id"):
            return False
        if event.get("entry_mode", "nominated") != "discovered":
            return True
        metrics = event.get("discovery_metrics")
        if not isinstance(metrics, dict):
            return False
        screened = metrics.get("screened_candidate_count")
        deep_reads = metrics.get("deep_read_count")
        direct = metrics.get("direct_candidate_count")
        adjacent = metrics.get("adjacent_candidate_count")
        queries = metrics.get("search_query_count")
        return (
            isinstance(screened, int)
            and 1 <= screened <= 20
            and isinstance(deep_reads, int)
            and 1 <= deep_reads <= 5
            and isinstance(queries, int)
            and 1 <= queries <= 5
            and isinstance(direct, int)
            and isinstance(adjacent, int)
            and direct + adjacent == screened
            and (adjacent == 0 or direct >= 4 * adjacent)
            and isinstance(metrics.get("selected_repository"), str)
        )

    successful = {
        str(event["run_id"]): str(event.get("entry_mode", "nominated"))
        for event in events
        if qualifies(event)
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
