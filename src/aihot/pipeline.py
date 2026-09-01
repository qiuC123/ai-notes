from __future__ import annotations

import hashlib
import json
import os
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import UTC, date as date_type, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import yaml

from aihot.cluster import cluster_items
from aihot.collectors.parsers import (
    parse_aihot_hot_topics,
    parse_aihot_selected,
    parse_arxiv_atom,
    parse_atom,
    parse_huggingface_models,
    parse_rss,
)
from aihot.http import PublicHttpFetcher
from aihot.models import NormalizedItem, RawRecord
from aihot.normalize import normalize_title, normalize_url
from aihot.report import candidate_to_dict, event_to_dict, render_markdown
from aihot.score import ScoreResult, load_scoring_config, score_event


@dataclass(frozen=True, slots=True)
class SourceSpec:
    source_id: str
    url: str
    parser: str
    raw_format: str
    source_class: str
    owner: str
    evidence_allowed: bool


class Fetcher(Protocol):
    def fetch(self, source: SourceSpec) -> bytes:
        ...


class PipelineRunError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RunResult:
    events_path: Path
    top_candidates_path: Path
    markdown_path: Path
    manifest_path: Path


_PARSER_BY_NAME = {
    "aihot_selected": parse_aihot_selected,
    "aihot_hot_topics": parse_aihot_hot_topics,
    "rss": parse_rss,
    "atom": parse_atom,
    "huggingface_models": parse_huggingface_models,
    "arxiv_atom": parse_arxiv_atom,
}


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_bytes_atomically(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=path.parent, prefix=f".{path.name}.") as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_bytes_atomically(path, (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _write_text(path: Path, payload: str) -> None:
    _write_bytes_atomically(path, payload.encode("utf-8"))


def _source_specs_from_payload(payload: Any, path: Path) -> list[SourceSpec]:
    sources = payload.get("sources", []) if isinstance(payload, dict) else []
    parsed: list[SourceSpec] = []
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Each source must be a mapping")
        parser = str(source.get("parser", ""))
        raw_format = str(source.get("format", ""))
        if parser not in _PARSER_BY_NAME or raw_format not in {"json", "xml"}:
            raise ValueError(f"Unsupported source definition: {source.get('id')}")
        parsed.append(
            SourceSpec(
                source_id=str(source["id"]),
                url=str(source["url"]),
                parser=parser,
                raw_format=raw_format,
                source_class=str(source["source_class"]),
                owner=str(source.get("owner", "")),
                evidence_allowed=bool(source.get("evidence_allowed", True)),
            )
        )
    if not parsed:
        raise ValueError(f"No sources configured in {path}")
    return parsed


def load_pipeline_config(path: Path) -> tuple[list[SourceSpec], dict[str, float]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    sources = _source_specs_from_payload(payload, path)
    clustering = payload.get("clustering", {}) if isinstance(payload, dict) else {}
    if not isinstance(clustering, dict):
        raise ValueError(f"Invalid clustering configuration in {path}")
    settings = {
        "title_threshold": float(clustering.get("title_threshold", 0.48)),
        "hours_window": float(clustering.get("hours_window", 72)),
    }
    if not 0 < settings["title_threshold"] <= 1 or settings["hours_window"] <= 0:
        raise ValueError(f"Invalid clustering values in {path}")
    return sources, settings


def load_sources(path: Path) -> list[SourceSpec]:
    return load_pipeline_config(path)[0]


def _xml_root_name(xml_text: str) -> str:
    try:
        return ET.fromstring(xml_text).tag.rsplit("}", 1)[-1]
    except ET.ParseError as error:
        raise ValueError(f"Invalid XML: {error}") from error


def _validate_source_payload(source: SourceSpec, payload: Any) -> None:
    if source.parser in {"aihot_selected", "aihot_hot_topics"}:
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError(f"{source.parser} requires an object with an items list")
        return
    if source.parser == "huggingface_models":
        if not isinstance(payload, list):
            raise ValueError("huggingface_models requires a JSON array")
        return
    expected_root = "rss" if source.parser == "rss" else "feed"
    actual_root = _xml_root_name(payload)
    if actual_root != expected_root:
        raise ValueError(f"{source.parser} requires <{expected_root}> XML, got <{actual_root}>")


def _parse_source(source: SourceSpec, payload: bytes) -> list[RawRecord]:
    parser = _PARSER_BY_NAME[source.parser]
    if source.raw_format == "json":
        parsed_payload = json.loads(payload.decode("utf-8"))
    else:
        parsed_payload = payload.decode("utf-8")
    _validate_source_payload(source, parsed_payload)
    records = parser(parsed_payload)
    if not isinstance(records, list):
        raise ValueError(f"Parser {source.parser} returned a non-list value")
    return records


def _normalize_record(record: RawRecord, source: SourceSpec, run_date: str) -> NormalizedItem:
    title = " ".join(record.title.split())
    canonical_url = normalize_url(record.url)
    identity = canonical_url or normalize_title(title)
    item_id = hashlib.sha256(f"{source.source_id}\n{identity}".encode("utf-8")).hexdigest()
    raw_ref = f"data/raw/{run_date}/{source.source_id}.{source.raw_format}"
    categories = sorted({" ".join(category.split()) for category in record.categories if category.strip()}, key=str.casefold)
    owner = record.owner or source.owner
    source_links: list[dict[str, str]] = []

    def add_link(url: str, provenance: str) -> None:
        normalized_url = normalize_url(url)
        if not normalized_url:
            return
        link = {
            "url": normalized_url,
            "source_id": source.source_id,
            "source_class": source.source_class,
            "provenance": provenance,
        }
        if link not in source_links:
            source_links.append(link)

    if canonical_url:
        primary_provenance = "direct_source"
        if source.source_class == "aggregator":
            primary_provenance = (
                "original_link_from_aggregator" if record.link_relation == "original_link" else "discovery_link"
            )
        add_link(
            canonical_url,
            primary_provenance,
        )
    for extra_url in record.extra_urls:
        add_link(extra_url, "discovery_link" if source.source_class == "aggregator" else "related_source")
    return NormalizedItem(
        item_id=item_id,
        title=title,
        summary=" ".join(record.summary.split()),
        url=canonical_url,
        source_id=source.source_id,
        source_class=source.source_class,
        owner=owner,
        published_at=record.published_at,
        discovered_at=f"{run_date}T00:00:00Z",
        categories=categories,
        entities=[owner] if owner else [],
        evidence_allowed=source.evidence_allowed,
        raw_ref=raw_ref,
        source_links=source_links,
    )


def _recent_decision_titles(root: Path, run_date: str) -> list[str]:
    current = date_type.fromisoformat(run_date)
    titles: list[str] = []
    for offset in range(1, 15):
        decision_path = root / "data" / "decisions" / (current - timedelta(days=offset)).isoformat() / "decisions.json"
        if not decision_path.exists():
            continue
        try:
            payload = json.loads(decision_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for decision in payload.get("decisions", []):
            if isinstance(decision, dict) and isinstance(decision.get("title"), str):
                titles.append(decision["title"])
    return titles


class DailyPipeline:
    def __init__(
        self,
        *,
        root: Path,
        sources: list[SourceSpec],
        scoring_config: dict[str, Any],
        fetcher: Fetcher,
        title_threshold: float = 0.48,
        hours_window: float = 72,
    ) -> None:
        self.root = root
        self.sources = sources
        self.scoring_config = scoring_config
        self.fetcher = fetcher
        self.title_threshold = title_threshold
        self.hours_window = hours_window

    def _config_hash(self) -> str:
        payload = json.dumps(
            {
                "sources": [asdict(source) for source in self.sources],
                "scoring": self.scoring_config,
                "title_threshold": self.title_threshold,
                "hours_window": self.hours_window,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _manifest(
        self,
        *,
        run_date: str,
        started_at: str,
        successful_sources: list[str],
        failed_sources: list[dict[str, str]],
        raw_item_count: int,
        normalized_count: int,
        event_count: int,
        top_n: int,
        exit_status: str,
        reused_raw_sources: list[str],
    ) -> dict[str, Any]:
        config_hash = self._config_hash()
        return {
            "run_id": f"daily-{run_date}-{config_hash[:12]}",
            "date": run_date,
            "started_at": started_at,
            "finished_at": _utc_now(),
            "config_hash": config_hash,
            "successful_sources": successful_sources,
            "reused_raw_sources": reused_raw_sources,
            "failed_sources": failed_sources,
            "raw_item_count": raw_item_count,
            "normalized_count": normalized_count,
            "event_count": event_count,
            "top_n": top_n,
            "exit_status": exit_status,
        }

    def _reusable_raw_snapshot(self, run_date: str) -> dict[str, bytes] | None:
        """Reuse a complete successful daily snapshot so repeated dates are deterministic."""
        manifest_path = self.root / "outputs" / run_date / "run-manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        expected_sources = [source.source_id for source in self.sources]
        if (
            manifest.get("exit_status") != "success"
            or manifest.get("config_hash") != self._config_hash()
            or manifest.get("successful_sources") != expected_sources
        ):
            return None
        snapshot: dict[str, bytes] = {}
        for source in self.sources:
            raw_path = self.root / "data" / "raw" / run_date / f"{source.source_id}.{source.raw_format}"
            try:
                snapshot[source.source_id] = raw_path.read_bytes()
            except OSError:
                return None
        return snapshot

    def _write_failed_artifacts(
        self,
        *,
        run_date: str,
        requested_top: int,
        reason: str,
        event_path: Path,
        top_path: Path,
        markdown_path: Path,
    ) -> None:
        _write_json(event_path, {"date": run_date, "exit_status": "failed", "events": []})
        _write_json(
            top_path,
            {
                "date": run_date,
                "exit_status": "failed",
                "requested_top_n": requested_top,
                "top_n": 0,
                "candidate_count": 0,
                "failure_reason": reason,
                "candidates": [],
            },
        )
        _write_text(markdown_path, f"# Amesi 每日候选榜 — {run_date}\n\n> 本次运行失败：{reason}\n")

    def run(self, run_date: str, *, top: int, refresh: bool = False) -> RunResult:
        date_type.fromisoformat(run_date)
        if top <= 0:
            raise ValueError("top must be greater than zero")
        started_at = _utc_now()
        raw_dir = self.root / "data" / "raw" / run_date
        event_path = self.root / "data" / "events" / run_date / "events.json"
        output_dir = self.root / "outputs" / run_date
        top_path = output_dir / "top-candidates.json"
        markdown_path = output_dir / "top-candidates.md"
        manifest_path = output_dir / "run-manifest.json"
        successful_sources: list[str] = []
        failed_sources: list[dict[str, str]] = []
        raw_records: list[tuple[SourceSpec, RawRecord]] = []
        reusable_snapshot = None if refresh else self._reusable_raw_snapshot(run_date)
        reused_raw_sources: list[str] = []
        for source in self.sources:
            try:
                if reusable_snapshot is not None:
                    payload = reusable_snapshot[source.source_id]
                    reused_raw_sources.append(source.source_id)
                else:
                    payload = self.fetcher.fetch(source)
                    _write_bytes_atomically(raw_dir / f"{source.source_id}.{source.raw_format}", payload)
                records = _parse_source(source, payload)
                raw_records.extend((source, record) for record in records)
                successful_sources.append(source.source_id)
            except Exception as error:  # Each source is isolated by design.
                failed_sources.append({"source_id": source.source_id, "error": f"{type(error).__name__}: {error}"})
        normalized = [_normalize_record(record, source, run_date) for source, record in raw_records]
        events = cluster_items(normalized, title_threshold=self.title_threshold, hours_window=self.hours_window) if normalized else []
        linked_events = [event for event in events if any(item.source_links or item.url for item in event.items)]
        if len(successful_sources) < 3 or not linked_events:
            manifest = self._manifest(
                run_date=run_date,
                started_at=started_at,
                successful_sources=successful_sources,
                failed_sources=failed_sources,
                raw_item_count=len(raw_records),
                normalized_count=len(normalized),
                event_count=len(events),
                top_n=0,
                exit_status="failed",
                reused_raw_sources=reused_raw_sources,
            )
            reasons = []
            if len(successful_sources) < 3:
                reasons.append(f"only {len(successful_sources)} sources succeeded")
            if not linked_events:
                reasons.append("no candidate events with source links")
            reason = "; ".join(reasons)
            self._write_failed_artifacts(
                run_date=run_date,
                requested_top=top,
                reason=reason,
                event_path=event_path,
                top_path=top_path,
                markdown_path=markdown_path,
            )
            _write_json(manifest_path, manifest)
            raise PipelineRunError(reason)
        recent_titles = _recent_decision_titles(self.root, run_date)
        scored: list[tuple[Any, ScoreResult]] = [
            (event, score_event(event, self.scoring_config, as_of=run_date, recent_decision_titles=recent_titles))
            for event in linked_events
        ]
        scored.sort(key=lambda pair: (-pair[1].total, pair[0].event_id))
        candidates = [candidate_to_dict(event, score, rank) for rank, (event, score) in enumerate(scored[:top], start=1)]
        _write_json(event_path, {"date": run_date, "exit_status": "success", "events": [event_to_dict(event) for event in events]})
        _write_json(
            top_path,
            {
                "date": run_date,
                "exit_status": "success",
                "requested_top_n": top,
                "top_n": len(candidates),
                "candidate_count": len(candidates),
                "candidates": candidates,
            },
        )
        _write_text(markdown_path, render_markdown(run_date, candidates))
        manifest = self._manifest(
            run_date=run_date,
            started_at=started_at,
            successful_sources=successful_sources,
            failed_sources=failed_sources,
            raw_item_count=len(raw_records),
            normalized_count=len(normalized),
            event_count=len(events),
            top_n=len(candidates),
            exit_status="success",
            reused_raw_sources=reused_raw_sources,
        )
        _write_json(manifest_path, manifest)
        return RunResult(event_path, top_path, markdown_path, manifest_path)


def build_default_pipeline(root: Path) -> DailyPipeline:
    sources, clustering = load_pipeline_config(root / "config" / "sources.yaml")
    return DailyPipeline(
        root=root,
        sources=sources,
        scoring_config=load_scoring_config(root / "config" / "scoring.yaml"),
        fetcher=PublicHttpFetcher(),
        title_threshold=clustering["title_threshold"],
        hours_window=clustering["hours_window"],
    )
