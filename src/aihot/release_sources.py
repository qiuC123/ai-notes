from __future__ import annotations

import html
import hashlib
import json
import os
import re
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlsplit

import yaml


_SPACE = re.compile(r"\s+")
_PRERELEASE = re.compile(r"(?:^|[-._])(nightly|alpha|beta|rc|preview|dev)(?:[-._0-9]|$)", re.IGNORECASE)
_SOURCE_ID = re.compile(r"[a-z][a-z0-9_]{0,63}")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "template"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "template"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


@dataclass(frozen=True, slots=True)
class ReleaseSource:
    source_id: str
    repository: str
    category: str
    feed_url: str
    accepted_tag_patterns: tuple[str, ...]
    excluded_tag_patterns: tuple[str, ...] = ()
    tier: str = "trial"
    policy_version: str = "1"
    package_aliases: tuple[str, ...] = ()

    @property
    def url(self) -> str:
        return self.feed_url


@dataclass(frozen=True, slots=True)
class ReleaseRecord:
    release_key: str
    source_id: str
    repository: str
    release_tag: str
    title: str
    release_notes_html: str
    release_notes_text: str
    url: str
    published_at: str
    discovered_at: str
    raw_ref: str
    item_type: str = "release"
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReleaseFilterResult:
    eligible: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BackfillResult:
    records: tuple[ReleaseRecord, ...]
    closed: bool
    pages_fetched: int


class UrlFetcher(Protocol):
    def fetch_url(self, url: str) -> bytes:
        ...


@dataclass(slots=True)
class GitHubReleaseRestBackfiller:
    fetcher: UrlFetcher
    per_page: int = 100
    max_pages: int = 10
    cache_dir: Path | None = None

    def _fetch_page(self, url: str) -> bytes:
        if self.cache_dir is None:
            return self.fetcher.fetch_url(url)
        cache_path = self.cache_dir / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.json"
        if cache_path.exists():
            return cache_path.read_bytes()
        payload = self.fetcher.fetch_url(url)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=cache_path.parent, prefix=f".{cache_path.name}.") as handle:
            handle.write(payload)
            temporary = Path(handle.name)
        os.replace(temporary, cache_path)
        return payload

    def fetch_missing(
        self,
        source: ReleaseSource,
        *,
        known_keys: set[str],
        last_success_at: str,
        discovered_at: str,
        raw_ref: str,
    ) -> BackfillResult:
        missing: list[ReleaseRecord] = []
        for page in range(1, self.max_pages + 1):
            url = f"https://api.github.com/repos/{source.repository}/releases?per_page={self.per_page}&page={page}"
            payload = json.loads(self._fetch_page(url).decode("utf-8"))
            if not isinstance(payload, list):
                raise ValueError(f"GitHub releases response for {source.repository} is not a list")
            records = parse_release_rest(
                payload,
                source=source,
                discovered_at=discovered_at,
                raw_ref=raw_ref,
            )
            for record in records:
                if record.release_key in known_keys:
                    return BackfillResult(tuple(missing), True, page)
                if record.published_at <= last_success_at:
                    return BackfillResult(tuple(missing), True, page)
                missing.append(record)
            if len(payload) < self.per_page:
                return BackfillResult(tuple(missing), not known_keys, page)
        return BackfillResult(tuple(missing), False, self.max_pages)


def load_release_sources(path: Path) -> list[ReleaseSource]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        raise ValueError(f"Invalid Ai Notes source registry: {path}")
    sources: list[ReleaseSource] = []
    seen: set[str] = set()
    for item in payload["sources"]:
        if not isinstance(item, dict):
            raise ValueError("Each Ai Notes source must be a mapping")
        source_id = str(item["id"])
        repository = str(item["repository"])
        feed_url = str(item.get("feed_url") or "")
        expected_feed_url = f"https://github.com/{repository}/releases.atom"
        patterns = tuple(str(pattern) for pattern in item.get("accepted_tag_patterns", []))
        if (
            source_id in seen
            or not _SOURCE_ID.fullmatch(source_id)
            or not _REPOSITORY.fullmatch(repository)
            or feed_url != expected_feed_url
            or not patterns
            or "policy_version" not in item
            or "excluded_tag_patterns" not in item
        ):
            raise ValueError(f"Invalid or duplicate Ai Notes source: {source_id}")
        try:
            for pattern in patterns:
                re.compile(pattern)
        except re.error as error:
            raise ValueError(f"Invalid tag pattern for {source_id}: {error}") from error
        seen.add(source_id)
        sources.append(
            ReleaseSource(
                source_id=source_id,
                repository=repository,
                category=str(item["category"]),
                feed_url=feed_url,
                accepted_tag_patterns=patterns,
                excluded_tag_patterns=tuple(str(pattern) for pattern in item.get("excluded_tag_patterns", [])),
                tier=str(item.get("tier", "trial")),
                policy_version=str(item["policy_version"]),
                package_aliases=tuple(str(alias) for alias in item.get("package_aliases", [])),
            )
        )
    return sources


def normalize_evidence_text(value: str) -> str:
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", html.unescape(value or ""))).strip()


def normalize_html_text(value: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(html.unescape(value or ""))
    return normalize_evidence_text(" ".join(parser.parts))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(element) if _local_name(child.tag) == name]


def _child_text(element: ET.Element, name: str) -> str:
    child = next(iter(_children(element, name)), None)
    return "" if child is None else "".join(child.itertext()).strip()


def _entry_url(entry: ET.Element) -> str:
    for link in _children(entry, "link"):
        if link.attrib.get("rel", "alternate") == "alternate" and link.attrib.get("href"):
            return link.attrib["href"]
    return ""


def _release_tag(entry: ET.Element, url: str) -> str:
    entry_id = _child_text(entry, "id")
    marker = "/releases/tag/"
    if marker in url:
        return unquote(urlsplit(url).path.split(marker, 1)[1]).strip("/")
    if "/" in entry_id:
        return unquote(entry_id.rsplit("/", 1)[-1])
    return ""


def parse_release_atom(
    xml_text: str,
    *,
    source: ReleaseSource,
    discovered_at: str,
    raw_ref: str,
) -> list[ReleaseRecord]:
    root = ET.fromstring(xml_text)
    records: list[ReleaseRecord] = []
    for entry in _children(root, "entry"):
        url = _entry_url(entry)
        release_tag = _release_tag(entry, url)
        title = _child_text(entry, "title")
        notes_html = _child_text(entry, "content")
        published_at = _child_text(entry, "published") or _child_text(entry, "updated")
        parts = urlsplit(url)
        expected_prefix = f"/{source.repository}/releases/tag/".casefold()
        if (
            not release_tag
            or parts.scheme != "https"
            or parts.netloc.casefold() != "github.com"
            or not parts.path.casefold().startswith(expected_prefix)
            or not title
            or not published_at
        ):
            continue
        records.append(
            ReleaseRecord(
                release_key=f"{source.repository}@{release_tag}",
                source_id=source.source_id,
                repository=source.repository,
                release_tag=release_tag,
                title=title,
                release_notes_html=notes_html,
                release_notes_text=normalize_html_text(notes_html),
                url=url,
                published_at=published_at,
                discovered_at=discovered_at,
                raw_ref=raw_ref,
            )
        )
    return records


def parse_release_rest(
    payload: list[object],
    *,
    source: ReleaseSource,
    discovered_at: str,
    raw_ref: str,
) -> list[ReleaseRecord]:
    records: list[ReleaseRecord] = []
    for item in payload:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        release_tag = str(item.get("tag_name") or "").strip()
        url = str(item.get("html_url") or "").strip()
        published_at = str(item.get("published_at") or "").strip()
        title = str(item.get("name") or release_tag).strip()
        notes = str(item.get("body") or "")
        if not release_tag or not url or not published_at:
            continue
        records.append(
            ReleaseRecord(
                release_key=f"{source.repository}@{release_tag}",
                source_id=source.source_id,
                repository=source.repository,
                release_tag=release_tag,
                title=title,
                release_notes_html=notes,
                release_notes_text=normalize_evidence_text(notes),
                url=url,
                published_at=published_at,
                discovered_at=discovered_at,
                raw_ref=raw_ref,
                metadata={"prerelease": bool(item.get("prerelease"))},
            )
        )
    return records


def filter_release(record: ReleaseRecord, source: ReleaseSource) -> ReleaseFilterResult:
    reasons: list[str] = []
    if record.metadata.get("prerelease") is True or _PRERELEASE.search(record.release_tag):
        reasons.append("prerelease_tag")
    if not any(re.fullmatch(pattern, record.release_tag, flags=re.IGNORECASE) for pattern in source.accepted_tag_patterns):
        reasons.append("tag_not_allowed")
    if any(re.search(pattern, record.release_tag, flags=re.IGNORECASE) for pattern in source.excluded_tag_patterns):
        reasons.append("tag_excluded")
    if not record.url or not record.published_at or not record.release_notes_text:
        reasons.append("incomplete_release")
    return ReleaseFilterResult(eligible=not reasons, reasons=tuple(dict.fromkeys(reasons)))
