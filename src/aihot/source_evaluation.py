from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from aihot.release_review import finalize_decisions, review_in_batches
from aihot.release_ledger import ReleaseLedger
from aihot.release_sources import ReleaseRecord, ReleaseSource, filter_release


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent, prefix=f".{path.name}.") as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


@dataclass(slots=True)
class HistoricalBacktester:
    root: Path
    sources: list[ReleaseSource]
    backfiller: object
    reviewer: object

    def run(self, *, as_of: str, days: int = 90) -> dict[str, Any]:
        cutoff = (datetime.fromisoformat(as_of) - timedelta(days=days)).date().isoformat() + "T00:00:00Z"
        discovered_at = f"{as_of}T00:00:00Z"
        eligible: list[ReleaseRecord] = []
        source_rows: list[dict[str, Any]] = []
        for source in self.sources:
            result = self.backfiller.fetch_missing(
                source,
                known_keys=set(),
                last_success_at=cutoff,
                discovered_at=discovered_at,
                raw_ref=f"data/backtests/{as_of}/{source.source_id}.json",
            )
            rejected_reasons: dict[str, int] = {}
            source_eligible: list[ReleaseRecord] = []
            for record in result.records:
                filtered = filter_release(record, source)
                if filtered.eligible:
                    source_eligible.append(record)
                    eligible.append(record)
                else:
                    for reason in filtered.reasons:
                        rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1
            source_rows.append(
                {
                    "source_id": source.source_id,
                    "repository": source.repository,
                    "observed_count": len(result.records),
                    "eligible_count": len(source_eligible),
                    "accepted_count": 0,
                    "rejected_count": 0,
                    "hard_filter_reasons": rejected_reasons,
                    "coverage_complete": bool(result.closed),
                    "pages_fetched": result.pages_fetched,
                }
            )

        output_dir = self.root / "outputs" / "backtests" / f"{as_of}-{days}d"
        _write_text_atomic(
            output_dir / "review-queue.json",
            json.dumps(
                {"as_of": as_of, "days": days, "records": [asdict(record) for record in eligible]},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        raw_decisions = review_in_batches(self.reviewer, eligible) if eligible else {"decisions": []}
        decisions = finalize_decisions(eligible, raw_decisions)
        rows_by_source = {row["source_id"]: row for row in source_rows}
        records_by_key = {record.release_key: record for record in eligible}
        for decision in decisions:
            record = records_by_key[decision["release_key"]]
            row = rows_by_source[record.source_id]
            if decision["decision"] == "accept":
                row["accepted_count"] += 1
            else:
                row["rejected_count"] += 1

        report = {
            "as_of": as_of,
            "days": days,
            "cutoff": cutoff,
            "eligible_count": len(eligible),
            "sources": source_rows,
            "decisions": decisions,
        }
        _write_text_atomic(
            output_dir / "backtest.json", json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        lines = [f"# Ai Notes {days} 天历史回测 — {as_of}", ""]
        for row in source_rows:
            lines.append(
                f"- {row['source_id']}: observed {row['observed_count']}, eligible {row['eligible_count']}, "
                f"accepted {row['accepted_count']}, coverage_complete={str(row['coverage_complete']).lower()}"
            )
        _write_text_atomic(output_dir / "backtest.md", "\n".join(lines) + "\n")
        complete_source_ids = [row["source_id"] for row in source_rows if row["coverage_complete"]]
        _write_text_atomic(
            self.root / "data" / "releases" / "backtest-baseline.json",
            json.dumps(
                {"as_of": as_of, "days": days, "complete_source_ids": complete_source_ids},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        return report


def build_trial_evaluation(
    *,
    source_ids: list[str],
    fetch_days: dict[str, int],
    eligible_counts: dict[str, int],
    accepted_counts: dict[str, int],
    prerelease_leaks: dict[str, int],
    complete_evidence_counts: dict[str, int],
    max_latency_hours: dict[str, float],
    trial_days: int,
) -> dict[str, Any]:
    if trial_days != 14:
        raise ValueError("Ai Notes source promotion requires exactly 14 trial days")
    sources: list[dict[str, Any]] = []
    for source_id in source_ids:
        fetched = int(fetch_days.get(source_id, 0))
        eligible = int(eligible_counts.get(source_id, 0))
        accepted = int(accepted_counts.get(source_id, 0))
        prerelease = int(prerelease_leaks.get(source_id, 0))
        evidence_complete = int(complete_evidence_counts.get(source_id, 0))
        latency = float(max_latency_hours.get(source_id, 0.0))
        reasons: list[str] = []
        reliable = True
        if fetched < max(0, trial_days - 1):
            reliable = False
            reasons.append("fetch_success_below_13_of_14")
        if prerelease:
            reliable = False
            reasons.append("prerelease_leak")
        if latency > 24:
            reliable = False
            reasons.append("latency_above_24_hours")
        if evidence_complete != accepted:
            reliable = False
            reasons.append("accepted_evidence_incomplete")

        if not reliable:
            decision = "demote_watchlist"
        elif eligible < 3:
            decision = "continue_trial"
            reasons.append("insufficient_eligible_sample")
        elif accepted < 1:
            decision = "demote_watchlist"
            reasons.append("no_substantive_change_accepted")
        else:
            decision = "promote_core"

        sources.append(
            {
                "source_id": source_id,
                "decision": decision,
                "reasons": reasons,
                "metrics": {
                    "fetch_days": fetched,
                    "trial_days": trial_days,
                    "eligible_count": eligible,
                    "accepted_count": accepted,
                    "prerelease_leaks": prerelease,
                    "complete_evidence_count": evidence_complete,
                    "max_latency_hours": latency,
                },
            }
        )
    return {"trial_days": trial_days, "sources": sources}


def write_trial_evaluation(
    *,
    root: Path,
    sources: list[ReleaseSource],
    start_date: str,
    days: int = 14,
) -> tuple[dict[str, Any], Path, Path]:
    start = datetime.fromisoformat(start_date).date()
    end = start + timedelta(days=days)
    fetch_days = {source.source_id: 0 for source in sources}
    for offset in range(days):
        day = (start + timedelta(days=offset)).isoformat()
        manifest_path = root / "outputs" / day / "run-manifest.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        successful = set(manifest.get("successful_sources", []))
        gapped = {
            item.get("source_id")
            for item in manifest.get("known_gaps", [])
            if isinstance(item, dict)
        }
        for source_id in fetch_days:
            if source_id in successful and source_id not in gapped:
                fetch_days[source_id] += 1

    eligible_counts = {source.source_id: 0 for source in sources}
    accepted_counts = {source.source_id: 0 for source in sources}
    prerelease_leaks = {source.source_id: 0 for source in sources}
    complete_evidence_counts = {source.source_id: 0 for source in sources}
    max_latency_hours = {source.source_id: 0.0 for source in sources}
    source_ids = set(eligible_counts)
    ledger = ReleaseLedger(root / "data" / "releases" / "release-ledger.jsonl")
    for entry in ledger.entries():
        source_id = entry.get("source_id")
        discovered = str(entry.get("discovered_at") or "")[:10]
        if source_id not in source_ids or not discovered or not (start.isoformat() <= discovered < end.isoformat()):
            continue
        if entry.get("status") not in {"pending_review", "accepted", "rejected"}:
            continue
        eligible_counts[source_id] += 1
        tag = str(entry.get("release_tag") or "")
        if re.search(r"(?:^|[-._])(nightly|alpha|beta|rc|preview|dev)(?:[-._0-9]|$)", tag, re.IGNORECASE):
            prerelease_leaks[source_id] += 1
        if entry.get("status") == "accepted":
            accepted_counts[source_id] += 1
            changes = (entry.get("decision") or {}).get("substantive_changes", [])
            if changes and all(isinstance(change, dict) and str(change.get("evidence") or "").strip() for change in changes):
                complete_evidence_counts[source_id] += 1
        try:
            published = datetime.fromisoformat(str(entry["published_at"]).replace("Z", "+00:00"))
            discovered_at = datetime.fromisoformat(str(entry["discovered_at"]).replace("Z", "+00:00"))
            latency = max(0.0, (discovered_at - published).total_seconds() / 3600)
            max_latency_hours[source_id] = max(max_latency_hours[source_id], latency)
        except (KeyError, ValueError):
            max_latency_hours[source_id] = float("inf")

    report = build_trial_evaluation(
        source_ids=[source.source_id for source in sources],
        fetch_days=fetch_days,
        eligible_counts=eligible_counts,
        accepted_counts=accepted_counts,
        prerelease_leaks=prerelease_leaks,
        complete_evidence_counts=complete_evidence_counts,
        max_latency_hours=max_latency_hours,
        trial_days=days,
    )
    report.update({"start_date": start_date, "end_date": end.isoformat()})
    output_dir = root / "outputs" / "trials" / f"{start_date}-{days}d"
    json_path = output_dir / "source-evaluation.json"
    markdown_path = output_dir / "source-evaluation.md"
    _write_text_atomic(json_path, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    lines = [f"# Ai Notes 来源评估 — {start_date} 起 {days} 天", ""]
    for item in report["sources"]:
        lines.append(f"- {item['source_id']}: **{item['decision']}** — {', '.join(item['reasons']) or 'meets criteria'}")
    _write_text_atomic(markdown_path, "\n".join(lines) + "\n")
    return report, json_path, markdown_path
