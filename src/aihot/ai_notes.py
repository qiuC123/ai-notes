from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from aihot.http import PublicHttpFetcher
from aihot.release_ledger import ReleaseLedger, detect_atom_gap
from aihot.release_review import DecisionFileReviewer, PendingDecisionReviewer, finalize_decisions, review_in_batches
from aihot.release_sources import (
    GitHubReleaseRestBackfiller,
    ReleaseRecord,
    ReleaseSource,
    filter_release,
    load_release_sources,
    parse_release_atom,
)
from aihot.security_advisories import GitHubAdvisoryFetcher, parse_security_advisories
from aihot.source_evaluation import HistoricalBacktester


class FeedFetcher(Protocol):
    def fetch(self, source: ReleaseSource) -> bytes:
        ...


class Reviewer(Protocol):
    def review(self, records: list[ReleaseRecord]) -> dict[str, object]:
        ...


class SecurityFetcher(Protocol):
    def fetch_security(self, updated_since: str) -> bytes:
        ...


@dataclass(frozen=True, slots=True)
class AiNotesRunResult:
    review_queue_path: Path
    review_decisions_path: Path
    accepted_information_path: Path
    markdown_path: Path
    manifest_path: Path
    status: str


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=path.parent, prefix=f".{path.name}.") as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _write_json(path: Path, payload: object) -> None:
    _write_bytes(path, (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _write_text(path: Path, payload: str) -> None:
    _write_bytes(path, payload.encode("utf-8"))


def prune_raw_snapshots(root: Path, *, run_date: str, retention_days: int = 30) -> list[str]:
    raw_root = root / "data" / "raw"
    if not raw_root.exists():
        return []
    cutoff = (datetime.fromisoformat(run_date) - timedelta(days=retention_days)).date()
    removed: list[str] = []
    for path in raw_root.iterdir():
        if not path.is_dir():
            continue
        try:
            snapshot_date = datetime.fromisoformat(path.name).date()
        except ValueError:
            continue
        if snapshot_date < cutoff:
            shutil.rmtree(path)
            removed.append(path.name)
    return sorted(removed)


def _render_markdown(run_date: str, information: list[dict[str, object]], status: str) -> str:
    lines = [f"# Ai Notes — {run_date}", "", f"运行状态：`{status}`", ""]
    if not information:
        lines.append("当天没有通过完整质量门槛的 AI 信息。")
    for item in information:
        lines.extend([f"## {item['repository']} {item['release_tag']}", "", f"官方链接：{item['official_url']}", ""])
        for change in item["substantive_changes"]:
            lines.extend([f"- {change['summary_zh']}", f"  - 原文：{change['evidence']}"])
        lines.extend(["", f"判断理由：{item['decision_reason']}", ""])
    return "\n".join(lines).rstrip() + "\n"


class AiNotesPipeline:
    def __init__(
        self,
        *,
        root: Path,
        sources: list[ReleaseSource],
        fetcher: FeedFetcher,
        reviewer: Reviewer,
        backfiller: object | None = None,
        security_fetcher: SecurityFetcher | None = None,
        require_baseline: bool = False,
    ) -> None:
        self.root = root
        self.sources = sources
        self.fetcher = fetcher
        self.reviewer = reviewer
        self.backfiller = backfiller
        self.security_fetcher = security_fetcher
        self.require_baseline = require_baseline

    def run(self, run_date: str, *, refresh: bool = False) -> AiNotesRunResult:
        lock_path = self.root / "data" / "releases" / "ai-notes.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise RuntimeError(f"Ai Notes is already running; lock exists at {lock_path}") from error
        try:
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            os.close(descriptor)
            return self._run_unlocked(run_date, refresh=refresh)
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
            lock_path.unlink(missing_ok=True)

    def _run_unlocked(self, run_date: str, *, refresh: bool = False) -> AiNotesRunResult:
        run_day = datetime.fromisoformat(run_date).date()
        if run_day > datetime.now(UTC).date():
            raise ValueError("run date cannot be in the future")
        started_at = _utc_now()
        discovered_at = started_at
        output_dir = self.root / "outputs" / run_date
        queue_path = output_dir / "review-queue.json"
        decisions_path = output_dir / "review-decisions.json"
        accepted_path = output_dir / "accepted-information.json"
        markdown_path = output_dir / "accepted-information.md"
        manifest_path = output_dir / "run-manifest.json"
        prior_information: list[dict[str, object]] = []
        if accepted_path.exists():
            try:
                prior_payload = json.loads(accepted_path.read_text(encoding="utf-8"))
                if isinstance(prior_payload.get("information"), list):
                    prior_information = [item for item in prior_payload["information"] if isinstance(item, dict)]
            except (OSError, json.JSONDecodeError):
                prior_information = []
        if not refresh and all(
            path.exists() for path in (queue_path, decisions_path, accepted_path, markdown_path, manifest_path)
        ):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("status") == "success":
                return AiNotesRunResult(
                    queue_path,
                    decisions_path,
                    accepted_path,
                    markdown_path,
                    manifest_path,
                    str(manifest["status"]),
                )
        pruned_raw_dates = prune_raw_snapshots(self.root, run_date=run_date)
        ledger = ReleaseLedger(self.root / "data" / "releases" / "release-ledger.jsonl")
        baseline_path = self.root / "data" / "releases" / "backtest-baseline.json"
        baseline_payload = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else {}
        baseline_sources = set(baseline_payload.get("complete_source_ids", []))
        state_path = self.root / "data" / "releases" / "source-state.json"
        source_state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        queue: list[ReleaseRecord] = []
        successful_sources: list[str] = []
        failed_sources: list[dict[str, str]] = []
        known_gaps: list[dict[str, object]] = []
        security_retractions: list[dict[str, object]] = []
        filtered_count = 0

        for source in self.sources:
            try:
                payload = self.fetcher.fetch(source)
                raw_ref = f"data/raw/{run_date}/{source.source_id}.xml"
                _write_bytes(self.root / raw_ref, payload)
                records = parse_release_atom(
                    payload.decode("utf-8"), source=source, discovered_at=discovered_at, raw_ref=raw_ref
                )
                if not records:
                    raise ValueError("Atom feed contains no valid release entries")
                known_keys = ledger.known_keys(source.repository)
                prior_state = source_state.get(source.source_id) or {}
                persistent_gap = prior_state.get("known_gap") if isinstance(prior_state, dict) else None
                if isinstance(persistent_gap, dict):
                    gap_known_keys = set(persistent_gap.get("anchor_keys", []))
                    gap_last_success_at = persistent_gap.get("last_success_at")
                else:
                    anchor = prior_state.get("newest_release_key") if isinstance(prior_state, dict) else None
                    gap_known_keys = {anchor} if anchor else known_keys
                    gap_last_success_at = prior_state.get("last_success_at") if isinstance(prior_state, dict) else None
                gap = detect_atom_gap(records, known_keys=gap_known_keys, last_success_at=gap_last_success_at)
                unresolved_gap: dict[str, object] | None = None
                if gap.has_gap:
                    if self.backfiller is None:
                        unresolved_gap = {
                            "source_id": source.source_id,
                            "reason": gap.reason,
                            "oldest_published_at": gap.oldest_published_at,
                            "newest_published_at": gap.newest_published_at,
                        }
                        known_gaps.append(unresolved_gap)
                    else:
                        try:
                            backfill = self.backfiller.fetch_missing(
                                source,
                                known_keys=gap_known_keys,
                                last_success_at=gap_last_success_at,
                                discovered_at=discovered_at,
                                raw_ref=f"data/raw/{run_date}/{source.source_id}-backfill.json",
                            )
                            merged = {record.release_key: record for record in [*backfill.records, *records]}
                            records = list(merged.values())
                            if not backfill.closed:
                                unresolved_gap = {
                                    "source_id": source.source_id,
                                    "reason": "rest_backfill_unclosed",
                                    "oldest_published_at": gap.oldest_published_at,
                                    "newest_published_at": gap.newest_published_at,
                                    "pages_fetched": backfill.pages_fetched,
                                }
                                known_gaps.append(unresolved_gap)
                        except Exception as error:
                            unresolved_gap = {
                                "source_id": source.source_id,
                                "reason": f"rest_backfill_failed: {type(error).__name__}: {error}",
                                "oldest_published_at": gap.oldest_published_at,
                                "newest_published_at": gap.newest_published_at,
                            }
                            known_gaps.append(unresolved_gap)
                successful_sources.append(source.source_id)
                if self.require_baseline and source.source_id not in baseline_sources:
                    known_gaps.append(
                        {
                            "source_id": source.source_id,
                            "reason": "baseline_not_established",
                            "oldest_published_at": records[-1].published_at if records else None,
                            "newest_published_at": records[0].published_at if records else None,
                        }
                    )
                for record in records:
                    if not ledger.needs_processing(record, policy_version=source.policy_version):
                        continue
                    result = filter_release(record, source)
                    if result.eligible:
                        ledger.record(
                            record,
                            status="pending_review",
                            policy_version=source.policy_version,
                            reasons=[],
                        )
                        queue.append(record)
                    else:
                        filtered_count += 1
                        ledger.record(
                            record,
                            status="filtered",
                            policy_version=source.policy_version,
                            reasons=list(result.reasons),
                        )
                source_state[source.source_id] = {
                    "last_success_at": _utc_now(),
                    "newest_release_key": records[0].release_key if records else None,
                    "oldest_release_key": records[-1].release_key if records else None,
                    "entry_count": len(records),
                }
                if unresolved_gap is not None:
                    source_state[source.source_id]["known_gap"] = {
                        **unresolved_gap,
                        "anchor_keys": sorted(gap_known_keys),
                        "last_success_at": gap_last_success_at,
                    }
            except Exception as error:
                failed_sources.append({"source_id": source.source_id, "error": f"{type(error).__name__}: {error}"})

        if self.security_fetcher is not None:
            try:
                security_state = source_state.get("github_advisories") or {}
                updated_since = security_state.get("last_success_at") or (
                    (datetime.fromisoformat(run_date) - timedelta(days=1)).date().isoformat() + "T00:00:00Z"
                )
                security_payload = self.security_fetcher.fetch_security(updated_since)
                security_ref = f"data/raw/{run_date}/github_advisories.json"
                _write_bytes(self.root / security_ref, security_payload)
                parsed_payload = json.loads(security_payload.decode("utf-8"))
                if not isinstance(parsed_payload, list):
                    raise ValueError("GitHub global advisories response is not a list")
                security_page_complete = len(parsed_payload) < 100
                if not security_page_complete:
                    known_gaps.append(
                        {
                            "source_id": "github_advisories",
                            "reason": "security_page_limit_reached",
                            "updated_since": updated_since,
                        }
                    )
                known_advisories = {
                    str(entry.get("release_tag")): entry
                    for entry in ledger.entries()
                    if str(entry.get("release_key", "")).startswith("github-advisory@")
                }
                security_records = parse_security_advisories(
                    parsed_payload,
                    sources=self.sources,
                    discovered_at=discovered_at,
                    raw_ref=security_ref,
                    known_entries=known_advisories,
                )
                successful_sources.append("github_advisories")
                if security_page_complete:
                    source_state["github_advisories"] = {"last_success_at": _utc_now()}
                for record in security_records:
                    source = next(item for item in self.sources if item.repository == record.repository)
                    if not ledger.needs_processing(record, policy_version=source.policy_version):
                        continue
                    if record.metadata.get("withdrawn_at") or not record.metadata.get("qualifies", True):
                        reason = "advisory_withdrawn" if record.metadata.get("withdrawn_at") else "no_longer_qualifying"
                        ledger.record(
                            record,
                            status="withdrawn" if record.metadata.get("withdrawn_at") else "no_longer_qualifying",
                            policy_version=source.policy_version,
                            reasons=[reason],
                            extra={"withdrawn_at": record.metadata.get("withdrawn_at")},
                        )
                        security_retractions.append(
                            {
                                "ghsa_id": record.release_tag,
                                "repository": record.repository,
                                "official_url": record.url,
                                "withdrawn_at": record.metadata.get("withdrawn_at"),
                                "reason": reason,
                            }
                        )
                        continue
                    ledger.record(
                        record,
                        status="pending_review",
                        policy_version=source.policy_version,
                        reasons=[],
                    )
                    queue.append(record)
            except Exception as error:
                failed_sources.append(
                    {"source_id": "github_advisories", "error": f"{type(error).__name__}: {error}"}
                )

        _write_json(state_path, source_state)

        _write_json(queue_path, {"date": run_date, "records": [asdict(record) for record in queue]})
        review_error: str | None = None
        try:
            raw_decisions = review_in_batches(self.reviewer, queue) if queue else {"decisions": []}
            _write_json(decisions_path, raw_decisions)
            decisions = finalize_decisions(queue, raw_decisions)
        except Exception as error:  # Reviewer/model failures are isolated at this stage boundary.
            review_error = f"{type(error).__name__}: {error}"
            decisions = []
            _write_json(decisions_path, {"decisions": [], "error": review_error})

        records_by_key = {record.release_key: record for record in queue}
        information: list[dict[str, object]] = list(prior_information)
        accepted_keys = {str(item.get("release_key")) for item in information}
        ledger_updates: list[tuple[ReleaseRecord, str, str, list[str], dict[str, object] | None]] = []
        if review_error is None:
            for decision in decisions:
                record = records_by_key[decision["release_key"]]
                source = next(
                    item
                    for item in self.sources
                    if item.source_id == record.source_id or item.repository == record.repository
                )
                status = "accepted" if decision["decision"] == "accept" else "rejected"
                ledger_updates.append(
                    (
                        record,
                        status,
                        source.policy_version,
                        [] if status == "accepted" else [decision["decision_reason"]],
                        {"decision": decision},
                    )
                )
                if status == "accepted" and record.release_key not in accepted_keys:
                    information.append(
                        {
                            "release_key": record.release_key,
                            "source_id": record.source_id,
                            "repository": record.repository,
                            "release_tag": record.release_tag,
                            "published_at": record.published_at,
                            "official_url": record.url,
                            "change_types": decision["change_types"],
                            "substantive_changes": decision["substantive_changes"],
                            "decision_reason": decision["decision_reason"],
                            "pending_verification": decision["pending_verification"],
                        }
                    )
                    accepted_keys.add(record.release_key)
                elif status == "rejected" and record.release_key in accepted_keys:
                    information = [item for item in information if item.get("release_key") != record.release_key]
                    accepted_keys.discard(record.release_key)

        release_source_ids = {source.source_id for source in self.sources}
        release_success_count = sum(source_id in release_source_ids for source_id in successful_sources)
        if review_error:
            status = "review_failed"
        elif release_success_count == 0:
            status = "failed"
        elif failed_sources or known_gaps:
            status = "partial"
        else:
            status = "success"
        healthy_empty = status == "success" and not information
        _write_json(
            accepted_path,
            {
                "date": run_date,
                "status": status,
                "healthy_empty": healthy_empty,
                "information": information,
                "security_retractions": security_retractions,
            },
        )
        _write_text(markdown_path, _render_markdown(run_date, information, status))
        if review_error is None:
            ledger.record_many(ledger_updates)
        _write_json(
            manifest_path,
            {
                "date": run_date,
                "started_at": started_at,
                "finished_at": _utc_now(),
                "status": status,
                "healthy_empty": healthy_empty,
                "successful_sources": successful_sources,
                "failed_sources": failed_sources,
                "known_gaps": known_gaps,
                "queued_count": len(queue),
                "filtered_count": filtered_count,
                "accepted_count": len(information),
                "review_error": review_error,
                "pruned_raw_dates": pruned_raw_dates,
                "security_retractions": security_retractions,
            },
        )
        return AiNotesRunResult(queue_path, decisions_path, accepted_path, markdown_path, manifest_path, status)


def build_default_ai_notes_pipeline(root: Path, *, decisions_path: Path | None = None) -> AiNotesPipeline:
    http = PublicHttpFetcher()
    sources = load_release_sources(root / "config" / "ai_notes_sources.yaml")
    return AiNotesPipeline(
        root=root,
        sources=sources,
        fetcher=http,
        reviewer=DecisionFileReviewer(decisions_path) if decisions_path is not None else PendingDecisionReviewer(),
        backfiller=GitHubReleaseRestBackfiller(fetcher=http, cache_dir=root / "data" / "rest-cache"),
        security_fetcher=GitHubAdvisoryFetcher(http=http),
        require_baseline=True,
    )


def build_default_backtester(root: Path, *, decisions_path: Path | None = None) -> HistoricalBacktester:
    http = PublicHttpFetcher()
    return HistoricalBacktester(
        root=root,
        sources=load_release_sources(root / "config" / "ai_notes_sources.yaml"),
        backfiller=GitHubReleaseRestBackfiller(
            fetcher=http,
            per_page=100,
            max_pages=50,
            cache_dir=root / "data" / "backtests" / "rest-cache",
        ),
        reviewer=DecisionFileReviewer(decisions_path) if decisions_path is not None else PendingDecisionReviewer(),
    )
