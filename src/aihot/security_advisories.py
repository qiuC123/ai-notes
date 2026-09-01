from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

from aihot.release_sources import ReleaseRecord, ReleaseSource, normalize_evidence_text


class UrlFetcher(Protocol):
    def fetch_url(self, url: str) -> bytes:
        ...


@dataclass(slots=True)
class GitHubAdvisoryFetcher:
    http: UrlFetcher

    def fetch_security(self, updated_since: str) -> bytes:
        query = urlencode(
            {
                "type": "reviewed",
                "updated": f">={updated_since}",
                "per_page": 100,
                "sort": "updated",
                "direction": "desc",
            }
        )
        return self.http.fetch_url(f"https://api.github.com/advisories?{query}")


def _normalized_repo_url(repository: str) -> str:
    return f"https://github.com/{repository}".casefold().rstrip("/")


def _mapped_source(item: dict[str, Any], sources: list[ReleaseSource]) -> ReleaseSource | None:
    location = str(item.get("source_code_location") or "").casefold().rstrip("/")
    package_names = {
        str(vulnerability.get("package", {}).get("name") or "").casefold()
        for vulnerability in item.get("vulnerabilities", [])
        if isinstance(vulnerability, dict) and isinstance(vulnerability.get("package"), dict)
    }
    for source in sources:
        if location == _normalized_repo_url(source.repository):
            return source
        if package_names & {alias.casefold() for alias in source.package_aliases}:
            return source
    return None


def _content_payload(item: dict[str, Any]) -> dict[str, Any]:
    vulnerabilities = []
    for vulnerability in item.get("vulnerabilities", []):
        if not isinstance(vulnerability, dict):
            continue
        package = vulnerability.get("package") if isinstance(vulnerability.get("package"), dict) else {}
        vulnerabilities.append(
            {
                "ecosystem": package.get("ecosystem"),
                "name": package.get("name"),
                "vulnerable_version_range": vulnerability.get("vulnerable_version_range"),
                "first_patched_version": vulnerability.get("first_patched_version"),
            }
        )
    return {
        "type": item.get("type"),
        "summary": item.get("summary"),
        "description": item.get("description"),
        "severity": item.get("severity"),
        "source_code_location": item.get("source_code_location"),
        "withdrawn_at": item.get("withdrawn_at"),
        "vulnerabilities": sorted(vulnerabilities, key=lambda value: json.dumps(value, sort_keys=True)),
    }


def parse_security_advisories(
    payload: list[object],
    *,
    sources: list[ReleaseSource],
    discovered_at: str,
    raw_ref: str,
    known_entries: dict[str, dict[str, Any]] | None = None,
) -> list[ReleaseRecord]:
    known_entries = known_entries or {}
    records: list[ReleaseRecord] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        ghsa_id = str(raw.get("ghsa_id") or "").strip()
        known = known_entries.get(ghsa_id)
        mapped_source = _mapped_source(raw, sources)
        source = mapped_source
        if source is None and known is not None:
            source = next(
                (candidate for candidate in sources if candidate.repository == known.get("repository")),
                None,
            )
        qualifies = (
            raw.get("type") == "reviewed"
            and str(raw.get("severity") or "").lower() in {"high", "critical"}
            and mapped_source is not None
            and not raw.get("withdrawn_at")
        )
        if not qualifies and known is None:
            continue
        url = str(raw.get("html_url") or "").strip()
        published_at = str(raw.get("published_at") or raw.get("updated_at") or "").strip()
        if source is None or not ghsa_id or not url or not published_at:
            continue
        content_payload = _content_payload(raw)
        canonical = json.dumps(content_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        notes = " ".join(
            part
            for part in (
                str(raw.get("summary") or ""),
                str(raw.get("description") or ""),
                canonical,
            )
            if part
        )
        records.append(
            ReleaseRecord(
                release_key=f"github-advisory@{ghsa_id}",
                source_id="github_advisories",
                repository=source.repository,
                release_tag=ghsa_id,
                title=str(raw.get("summary") or ghsa_id),
                release_notes_html=notes,
                release_notes_text=normalize_evidence_text(notes),
                url=url,
                published_at=published_at,
                discovered_at=discovered_at,
                raw_ref=raw_ref,
                item_type="security",
                metadata={
                    "severity": str(raw.get("severity") or "").lower(),
                    "updated_at": raw.get("updated_at"),
                    "withdrawn_at": raw.get("withdrawn_at"),
                    "content_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                    "source_id": source.source_id,
                    "qualifies": qualifies,
                },
            )
        )
    return records
