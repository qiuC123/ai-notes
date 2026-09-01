from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aihot.release_sources import ReleaseRecord


@dataclass(frozen=True, slots=True)
class AtomGap:
    has_gap: bool
    reason: str | None
    oldest_published_at: str | None
    newest_published_at: str | None


def detect_atom_gap(
    records: list[ReleaseRecord],
    *,
    known_keys: set[str],
    last_success_at: str | None,
) -> AtomGap:
    published = sorted(record.published_at for record in records if record.published_at)
    oldest = published[0] if published else None
    newest = published[-1] if published else None
    if not last_success_at or not known_keys:
        return AtomGap(False, None, oldest, newest)
    has_overlap = any(record.release_key in known_keys for record in records)
    if not has_overlap:
        return AtomGap(True, "no_ledger_overlap", oldest, newest)
    if oldest and oldest > last_success_at:
        return AtomGap(True, "feed_window_after_last_success", oldest, newest)
    return AtomGap(False, None, oldest, newest)


class ReleaseLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries: dict[str, dict[str, Any]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                key = entry.get("release_key")
                if not isinstance(key, str) or not key:
                    raise ValueError(f"Invalid release ledger entry in {path}")
                self._entries[key] = entry

    def get(self, release_key: str) -> dict[str, Any] | None:
        entry = self._entries.get(release_key)
        return dict(entry) if entry is not None else None

    def needs_processing(self, record: ReleaseRecord, *, policy_version: str) -> bool:
        entry = self._entries.get(record.release_key)
        if entry is None or entry.get("policy_version") != policy_version:
            return True
        if entry.get("status") in {"pending_review", "review_failed"}:
            return True
        if record.item_type == "security":
            current_hash = hashlib.sha256(record.release_notes_text.encode("utf-8")).hexdigest()
            return entry.get("content_hash") != current_hash
        return False

    def known_keys(self, repository: str | None = None) -> set[str]:
        if repository is None:
            return set(self._entries)
        prefix = f"{repository}@"
        return {key for key in self._entries if key.startswith(prefix)}

    def entries(self) -> list[dict[str, Any]]:
        return [dict(self._entries[key]) for key in sorted(self._entries)]

    def record(
        self,
        record: ReleaseRecord,
        *,
        status: str,
        policy_version: str,
        reasons: list[str],
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._record_in_memory(record, status=status, policy_version=policy_version, reasons=reasons, extra=extra)
        self._write()

    def record_many(
        self,
        updates: list[tuple[ReleaseRecord, str, str, list[str], dict[str, Any] | None]],
    ) -> None:
        for record, status, policy_version, reasons, extra in updates:
            self._record_in_memory(
                record,
                status=status,
                policy_version=policy_version,
                reasons=reasons,
                extra=extra,
            )
        if updates:
            self._write()

    def _record_in_memory(
        self,
        record: ReleaseRecord,
        *,
        status: str,
        policy_version: str,
        reasons: list[str],
        extra: dict[str, Any] | None,
    ) -> None:
        existing = self._entries.get(record.release_key, {})
        processed_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        history = list(existing.get("history", []))
        history.append({"status": status, "processed_at": processed_at, "reasons": list(reasons)})
        entry: dict[str, Any] = {
            "release_key": record.release_key,
            "source_id": record.source_id,
            "repository": record.repository,
            "release_tag": record.release_tag,
            "url": record.url,
            "published_at": record.published_at,
            "discovered_at": record.discovered_at,
            "first_discovered_at": existing.get("first_discovered_at", record.discovered_at),
            "raw_ref": record.raw_ref,
            "last_processed_at": processed_at,
            "status": status,
            "policy_version": policy_version,
            "reasons": list(reasons),
            "content_hash": hashlib.sha256(record.release_notes_text.encode("utf-8")).hexdigest(),
            "history": history,
        }
        if record.item_type == "security":
            entry["advisory_updated_at"] = record.metadata.get("updated_at")
        if extra:
            entry.update(extra)
        self._entries[record.release_key] = entry

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(
            json.dumps(self._entries[key], ensure_ascii=False, sort_keys=True) + "\n"
            for key in sorted(self._entries)
        )
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", delete=False, dir=self.path.parent, prefix=f".{self.path.name}."
        ) as handle:
            handle.write(payload)
            temporary = Path(handle.name)
        os.replace(temporary, self.path)
