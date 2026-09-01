from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Iterable

from aihot.models import RawRecord
from aihot.normalize import normalize_url


_SPACE = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]+>")
_URL_KEYS = ("original", "originalUrl", "original_url", "canonical", "url", "href", "link")


def _text(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").strip())


def _plain_text(value: Any) -> str:
    return _text(html.unescape(_TAG.sub(" ", str(value or ""))))


def _to_utc_iso(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [_text(item) for item in value if _text(item)]
    text = _text(value)
    return [text] if text else []


def _owner(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "title", "id", "source"):
            if _text(value.get(key)):
                return _text(value[key])
        return ""
    return _text(value)


def _urls(value: Any) -> list[str]:
    """Collect URLs from inconsistent API link shapes in a stable preference order."""
    if isinstance(value, str):
        return [value] if value.startswith(("http://", "https://")) else []
    if isinstance(value, dict):
        found: list[str] = []
        for key in _URL_KEYS:
            found.extend(_urls(value.get(key)))
        for key, nested in value.items():
            if key not in _URL_KEYS:
                found.extend(_urls(nested))
        return found
    if isinstance(value, (list, tuple)):
        found = []
        for nested in value:
            found.extend(_urls(nested))
        return found
    return []


def _first_url(*values: Any) -> tuple[str, list[str]]:
    all_urls: list[str] = []
    for value in values:
        all_urls.extend(_urls(value))
    unique = list(dict.fromkeys(normalize_url(url) for url in all_urls))
    return (unique[0] if unique else "", unique[1:])


def _aihot_links(*values: Any) -> tuple[str, list[str], str]:
    """Keep the distinction between an aggregator page and a declared original URL."""
    original_values: list[str] = []
    for value in values:
        if isinstance(value, dict):
            for key in ("original", "originalUrl", "original_url", "canonical"):
                original_values.extend(_urls(value.get(key)))
    if original_values:
        primary = normalize_url(original_values[0])
        all_urls: list[str] = []
        for value in values:
            all_urls.extend(_urls(value))
        extras = [normalize_url(url) for url in all_urls if normalize_url(url) and normalize_url(url) != primary]
        return primary, list(dict.fromkeys(extras)), "original_link"
    url, extras = _first_url(*values)
    return url, extras, "discovery_link"


def parse_aihot_selected(payload: dict[str, Any]) -> list[RawRecord]:
    records: list[RawRecord] = []
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        url, extra_urls, link_relation = _aihot_links(item.get("links"), item.get("url"))
        title = _text(item.get("title") or item.get("originalTitle"))
        if not title:
            continue
        records.append(
            RawRecord(
                title=title,
                summary=_plain_text(item.get("summary")),
                url=url,
                published_at=_to_utc_iso(item.get("publishedAt")),
                categories=_as_list(item.get("category") or item.get("categories")),
                owner=_owner(item.get("source")),
                extra_urls=extra_urls,
                metadata={"upstream_id": _text(item.get("id"))},
                link_relation=link_relation,
            )
        )
    return records


def parse_aihot_hot_topics(payload: dict[str, Any]) -> list[RawRecord]:
    records: list[RawRecord] = []
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title"))
        if not title:
            continue
        source_count = item.get("sourceCount", 0)
        signal_count = item.get("signalCount", 0)
        url, extra_urls, link_relation = _aihot_links(item.get("links"), item.get("url"))
        records.append(
            RawRecord(
                title=title,
                summary=f"{source_count} sources, {signal_count} signals.",
                url=url,
                published_at=_to_utc_iso(item.get("latestAt")),
                categories=["hot-topic"],
                owner=_owner(item.get("source")),
                extra_urls=extra_urls,
                metadata={"upstream_id": _text(item.get("id")), "rank": item.get("rank")},
                link_relation=link_relation,
            )
        )
    return records


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, name: str) -> Iterable[ET.Element]:
    return (child for child in list(element) if _local_name(child.tag) == name)


def _child_text(element: ET.Element, *names: str) -> str:
    for name in names:
        for child in _children(element, name):
            value = _text(child.text)
            if value:
                return value
    return ""


def _atom_url(entry: ET.Element) -> str:
    links = list(_children(entry, "link"))
    for link in links:
        if link.attrib.get("rel", "alternate") == "alternate" and link.attrib.get("href"):
            return link.attrib["href"]
    for link in links:
        if link.attrib.get("href"):
            return link.attrib["href"]
    return _child_text(entry, "id")


def parse_rss(xml_text: str) -> list[RawRecord]:
    root = ET.fromstring(xml_text)
    channel = next(_children(root, "channel"), root)
    records: list[RawRecord] = []
    for item in _children(channel, "item"):
        title = _child_text(item, "title")
        if not title:
            continue
        url = _child_text(item, "link", "guid")
        categories = [_text(category.text) for category in _children(item, "category") if _text(category.text)]
        records.append(
            RawRecord(
                title=title,
                summary=_plain_text(_child_text(item, "description", "encoded")),
                url=url,
                published_at=_to_utc_iso(_child_text(item, "pubDate", "published", "updated")),
                categories=categories,
                owner=_child_text(channel, "title"),
            )
        )
    return records


def parse_atom(xml_text: str) -> list[RawRecord]:
    root = ET.fromstring(xml_text)
    records: list[RawRecord] = []
    for entry in _children(root, "entry"):
        title = _child_text(entry, "title")
        if not title:
            continue
        author = next(_children(entry, "author"), None)
        categories = [category.attrib.get("term", "") for category in _children(entry, "category")]
        records.append(
            RawRecord(
                title=title,
                summary=_plain_text(_child_text(entry, "summary", "content")),
                url=_atom_url(entry),
                published_at=_to_utc_iso(_child_text(entry, "published", "updated")),
                categories=[category for category in categories if category],
                owner=_child_text(author, "name") if author is not None else "",
            )
        )
    return records


def parse_huggingface_models(payload: list[dict[str, Any]]) -> list[RawRecord]:
    records: list[RawRecord] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        model_id = _text(item.get("modelId") or item.get("id"))
        if not model_id:
            continue
        categories = _as_list(item.get("tags"))
        for value in (item.get("pipeline_tag"), item.get("library_name")):
            if _text(value) and _text(value) not in categories:
                categories.append(_text(value))
        metrics = ", ".join(
            f"{key}={item[key]}" for key in ("trendingScore", "likes", "downloads") if item.get(key) is not None
        )
        records.append(
            RawRecord(
                title=model_id,
                summary=f"Hugging Face model signal: {metrics}" if metrics else "Hugging Face model signal.",
                url=f"https://huggingface.co/{model_id}",
                published_at=_to_utc_iso(item.get("createdAt")),
                categories=categories,
                owner=model_id.split("/", 1)[0] if "/" in model_id else "",
                metadata={key: item.get(key) for key in ("likes", "downloads", "trendingScore")},
            )
        )
    return records


def parse_arxiv_atom(xml_text: str) -> list[RawRecord]:
    records = parse_atom(xml_text)
    for record in records:
        if not record.owner:
            record.owner = "arXiv"
    return records
