"""Shared candidate ledger and daily/weekly/monthly Codex digest archives.

This module validates editorial records, not the truth of web evidence. It does
not fetch sources, call a model, send messages, or publish an article.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


CATEGORIES = (
    "开源项目", "Skills", "AI 应用", "Agent 框架与编排",
    "MCP 服务与连接器", "模型与运行工具", "游戏", "博客、帖子与访谈",
)
KINDS = ("project", "update", "reading", "news")
BEIJING = timezone(timedelta(hours=8))
DB_PATH = Path("data/weekly_digest/digest.sqlite3")
ARTICLE_DIR = Path("outputs/digest")
RANKINGS = ("daily", "weekly", "monthly")
RANKING_NAMES = {"daily": "日榜", "weekly": "周榜", "monthly": "月榜"}
SCHEMA_VERSION = 3


class DigestError(ValueError):
    """Invalid or conflicting input; safe to show as a structured CLI error."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text(value: Any, label: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise DigestError(f"{label} must be {'a string' if empty else 'a nonempty string'}")
    return value


def _object(value: Any, required: tuple[str, ...], label: str) -> dict:
    if not isinstance(value, dict):
        raise DigestError(f"{label} must be an object")
    missing = set(required) - value.keys()
    if missing:
        raise DigestError(f"{label} missing fields: {', '.join(sorted(missing))}")
    return value


def _list(value: Any, label: str, *, nonempty: bool = False) -> list:
    if not isinstance(value, list) or (nonempty and not value):
        raise DigestError(f"{label} must be {'a nonempty' if nonempty else 'an'} array")
    return value


def _date(value: Any, label: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise DigestError(f"{label} must be an ISO date (YYYY-MM-DD)")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DigestError(f"{label} is not a valid date") from exc


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}T", value):
        raise DigestError(f"{label} must be an ISO timestamp with a timezone")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError("missing timezone")
        return result
    except ValueError as exc:
        raise DigestError(f"{label} must be an ISO timestamp with a timezone") from exc


def _url(value: Any, label: str = "url") -> str:
    _text(value, label)
    try:
        parsed = urlsplit(value)
        if (parsed.scheme.lower() not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or any(character.isspace() for character in value)):
            raise ValueError("not an HTTP(S) URL")
        parsed.port  # Validate malformed ports without network access.
    except ValueError as exc:
        raise DigestError(f"{label} must be an HTTP(S) URL without credentials") from exc
    return value


def canonical_url(value: str, kind: str = "project") -> str:
    """Normalize identity; GitHub project/update pages share repository identity."""
    parsed = urlsplit(_url(value))
    host = parsed.hostname.lower()
    path = parsed.path
    if host in ("github.com", "www.github.com"):
        pieces = [part for part in path.split("/") if part]
        if len(pieces) < 2:
            raise DigestError("GitHub candidate URL must identify a repository or an article within it")
        pieces[0] = pieces[0].lower()
        pieces[1] = re.sub(r"\.git$", "", pieces[1], flags=re.IGNORECASE).lower()
        if not pieces[1]:
            raise DigestError("GitHub repository name is empty")
        if kind not in ("reading", "news"):
            return "https://github.com/" + "/".join(pieces[:2])
        host, path = "github.com", "/" + "/".join(pieces)
    elif ":" in host:  # Preserve valid IPv6 netloc formatting.
        host = f"[{host}]"
    port = parsed.port
    if port and not ((parsed.scheme == "https" and port == 443) or (parsed.scheme == "http" and port == 80)):
        host += f":{port}"
    query = [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.lower().startswith("utm_")
             and key.lower() not in ("fbclid", "gclid", "msclkid", "igshid")]
    scheme = "https" if host == "github.com" else parsed.scheme.lower()
    return urlunsplit((scheme, host, path or "/", urlencode(sorted(query)), ""))


def _urls(value: Any, label: str, *, nonempty: bool = False) -> None:
    for index, item in enumerate(_list(value, label, nonempty=nonempty)):
        _url(item, f"{label}[{index}]")


def _validate_batch(batch: Any) -> list[str]:
    _object(batch, ("schema_version", "run_id", "collected_at", "sources", "candidates"), "batch")
    if batch["schema_version"] not in ("digest-batch.v1", "digest-batch.v2"):
        raise DigestError("unsupported batch schema_version")
    _text(batch["run_id"], "run_id")
    _timestamp(batch["collected_at"], "collected_at")
    for source in _list(batch["sources"], "sources", nonempty=True):
        _object(source, ("name", "url", "status", "detail"), "source")
        _text(source["name"], "source.name")
        _url(source["url"], "source.url")
        if source["status"] not in ("ok", "empty", "failed"):
            raise DigestError("source.status must be ok, empty, or failed")
        _text(source["detail"], "source.detail", empty=True)
    identities = []
    for candidate in _list(batch["candidates"], "candidates"):
        _object(candidate, ("url", "title", "category", "summary", "reason", "source_urls",
                            "published_at", "kind", "evidence_status", "evidence_urls", "change_note"), "candidate")
        for key in ("title", "summary", "reason"):
            _text(candidate[key], f"candidate.{key}")
        if candidate["category"] not in CATEGORIES:
            raise DigestError("candidate.category must be one of the eight digest categories")
        if candidate["kind"] not in KINDS:
            raise DigestError("candidate.kind must be project, update, reading, or news")
        if candidate["evidence_status"] not in ("discovered", "verified"):
            raise DigestError("candidate.evidence_status must be discovered or verified")
        _urls(candidate["source_urls"], "candidate.source_urls", nonempty=True)
        _urls(candidate["evidence_urls"], "candidate.evidence_urls", nonempty=candidate["evidence_status"] == "verified")
        _text(candidate["change_note"], "candidate.change_note", empty=True)
        published = candidate["published_at"]
        if published is not None:
            if isinstance(published, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", published):
                _date(published, "candidate.published_at")
            else:
                _timestamp(published, "candidate.published_at")
        collected = _timestamp(batch["collected_at"], "collected_at")
        if batch["schema_version"] == "digest-batch.v2" or any(key in candidate for key in ("discovered_at", "verified_at", "verification_level")):
            _object(candidate, ("discovered_at", "verified_at", "verification_level"), "v2 candidate")
            discovered = _timestamp(candidate["discovered_at"], "candidate.discovered_at")
            if discovered > collected:
                raise DigestError("discovered_at cannot be after collected_at")
            if candidate["evidence_status"] == "verified":
                verified = _timestamp(candidate["verified_at"], "candidate.verified_at")
                if not discovered <= verified <= collected:
                    raise DigestError("verified_at must be between discovered_at and collected_at")
            elif candidate["verified_at"] is not None:
                raise DigestError("unverified candidates require verified_at=null")
            if candidate["verification_level"] not in ("documented", "demo", "tested"):
                raise DigestError("invalid verification_level")
            if candidate["kind"] == "update" and candidate["evidence_status"] == "verified" and not candidate.get("event"):
                raise DigestError("a verified update requires a stable event")
        if candidate.get("event"):
            _event(candidate["event"], candidate["kind"], collected)
        if candidate["kind"] == "news" and candidate["evidence_status"] == "verified":
            if not candidate.get("event"):
                raise DigestError("verified news requires a stable event")
            _event(candidate["event"], "news", _timestamp(candidate.get("verified_at") or batch["collected_at"], "news verification"))
            if _event_url(candidate["event"]["url"]) not in {_event_url(url) for url in candidate["evidence_urls"]}:
                raise DigestError("news event URL must be included in original evidence_urls")
        identities.append(canonical_url(candidate["url"], candidate["kind"]))
    return identities


def _now() -> datetime:
    return datetime.now(BEIJING)


def _event(value: Any, kind: str, observed: datetime) -> dict:
    _object(value, ("id", "url", "type"), "event")
    _text(value["id"], "event.id")
    _url(value["url"], "event.url")
    if ("occurred_at" in value) == ("occurred_on" in value):
        raise DigestError("event requires exactly one of occurred_at or occurred_on")
    if "occurred_on" in value:
        if kind != "news" or value.get("date_precision") != "date" or value.get("timezone") != "unknown":
            raise DigestError("date-only events require news, date_precision=date, and timezone=unknown")
    elif "date_precision" in value or "timezone" in value:
        raise DigestError("timestamp events cannot also declare date-only precision or timezone")
    earliest, _ = _event_bounds(value)
    if earliest > observed:
        raise DigestError("event cannot occur after its observation")
    allowed = ("news",) if kind == "news" else ("update",) if kind == "update" else ("release", "update")
    if value["type"] not in allowed:
        raise DigestError("event.type must match its kind: news requires news; update requires update; other kinds require release or update")
    return value


def _event_bounds(event: dict) -> tuple[datetime, datetime]:
    """Conservative closed UTC interval, never an invented event timestamp.

    An unknown source timezone spans civil UTC-12 through UTC+14. A source
    calendar date can therefore touch three Beijing dates. These bounds are
    only for containment/order checks and are never persisted as occurred_at.
    """
    if "occurred_at" in event:
        moment = _timestamp(event["occurred_at"], "event.occurred_at").astimezone(timezone.utc)
        return moment, moment
    day = _date(event.get("occurred_on"), "event.occurred_on")
    try:
        midnight = datetime.combine(day, datetime.min.time(), timezone.utc)
        return midnight - timedelta(hours=14), midnight + timedelta(days=1, hours=12) - timedelta(microseconds=1)
    except OverflowError as exc:
        raise DigestError("event date is outside the supported uncertainty range") from exc


def _event_time_key(event: dict) -> tuple[str, ...]:
    """Serializable precision-aware identity; use bounds, not this key, to sort."""
    if "occurred_at" in event:
        return ("timestamp", _timestamp(event["occurred_at"], "event.occurred_at").astimezone(timezone.utc).isoformat())
    return ("date", _date(event.get("occurred_on"), "event.occurred_on").isoformat(),
            event.get("date_precision"), event.get("timezone"))


def _event_in_period(event: dict, start: date, end: date) -> bool:
    earliest, latest = _event_bounds(event)
    period_start = datetime.combine(start, datetime.min.time(), BEIJING)
    period_end = datetime.combine(end + timedelta(days=1), datetime.min.time(), BEIJING)
    return period_start <= earliest and latest < period_end


def _event_url(value: str) -> str:
    # A release/changelog anchor is part of the event identity; tracking queries are not.
    parsed = urlsplit(value)
    base = canonical_url(value, "reading")
    return base + ("#" + parsed.fragment if parsed.fragment else "")


def period_window(ranking_type: str, period: str) -> tuple[date, date, datetime]:
    if ranking_type not in RANKINGS:
        raise DigestError("ranking_type must be daily, weekly, or monthly")
    if ranking_type == "monthly":
        if not isinstance(period, str) or not re.fullmatch(r"\d{4}-\d{2}", period):
            raise DigestError("monthly period must be YYYY-MM")
        start = _date(period + "-01", "period")
        following = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = following - timedelta(days=1)
    else:
        start = _date(period, "period")
        if ranking_type == "weekly" and start.weekday() != 0:
            raise DigestError("weekly period must be a Monday")
        end = start + timedelta(days=6 if ranking_type == "weekly" else 0)
    due_at = datetime.combine(end + timedelta(days=1), datetime.min.time(), BEIJING).replace(
        hour={"daily": 9, "weekly": 10, "monthly": 11}[ranking_type])
    return start, end, due_at


def _has_table(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _migrate(connection: sqlite3.Connection, path: Path, existed: bool) -> str | None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise DigestError("database schema is newer than this program")
    if version == SCHEMA_VERSION:
        return None
    backup = None
    before = {}
    if existed and _has_table(connection, "batches"):
        before = {name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                  for name in ("batches", "observations", "issues", "selections", "ranked_issues", "ranked_selections", "drafts")
                  if _has_table(connection, name)}
        backup_dir = path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"digest-v{version}-before-v{SCHEMA_VERSION}-{_now().strftime('%Y%m%dT%H%M%S%f')}.sqlite3"
        target = sqlite3.connect(backup)
        try:
            connection.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise DigestError("migration backup failed integrity check")
        finally:
            target.close()
    definitions = [
        "CREATE TABLE IF NOT EXISTS batches (run_id TEXT PRIMARY KEY,collected_at TEXT NOT NULL,collected_epoch REAL NOT NULL,local_date TEXT NOT NULL,content_hash TEXT NOT NULL,payload TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY,run_id TEXT NOT NULL REFERENCES batches(run_id),canonical_url TEXT NOT NULL,payload TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS observations_identity ON observations(canonical_url)",
        "CREATE INDEX IF NOT EXISTS batches_date ON batches(local_date)",
        "CREATE TABLE IF NOT EXISTS issues (date TEXT PRIMARY KEY,title TEXT NOT NULL,manifest TEXT NOT NULL,content_hash TEXT NOT NULL,article TEXT NOT NULL,article_hash TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS selections (issue_date TEXT NOT NULL REFERENCES issues(date),canonical_url TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(issue_date,canonical_url))",
        "CREATE TABLE ranked_issues (issue_id TEXT PRIMARY KEY,ranking_type TEXT NOT NULL,period TEXT NOT NULL,title TEXT NOT NULL,input_hash TEXT NOT NULL,manifest TEXT NOT NULL,content_hash TEXT NOT NULL,article TEXT NOT NULL,article_hash TEXT NOT NULL,archived_at TEXT NOT NULL,UNIQUE(ranking_type,period))",
        "CREATE TABLE ranked_selections (issue_id TEXT NOT NULL REFERENCES ranked_issues(issue_id),canonical_url TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(issue_id,canonical_url))",
        "CREATE INDEX ranked_selection_identity ON ranked_selections(canonical_url)",
        "CREATE TABLE drafts (issue_id TEXT PRIMARY KEY,ranking_type TEXT NOT NULL,period TEXT NOT NULL,payload TEXT NOT NULL,article TEXT NOT NULL,updated_at TEXT NOT NULL)",
    ]
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        if version < 2:
            for definition in definitions:
                connection.execute(definition)
        # Preserve legacy deliveries and their text verbatim. They count in weekly
        # all-history checks even if their old period was not a natural week.
        for row in connection.execute("SELECT * FROM issues").fetchall() if version < 2 else []:
            issue_id = "weekly:legacy-" + row["date"]
            connection.execute("INSERT INTO ranked_issues VALUES (?,?,?,?,?,?,?,?,?,?)", (
                issue_id, "weekly", "legacy-" + row["date"], row["title"], row["content_hash"],
                row["manifest"], row["content_hash"], row["article"], row["article_hash"], row["date"]))
            connection.execute("INSERT INTO ranked_selections SELECT ?,canonical_url,payload FROM selections WHERE issue_date=?", (issue_id, row["date"]))
        # Formal archives already retain exact text and hashes. Version 3 gives
        # drafts the same recovery boundary, plus known prior export hashes for
        # a draft update interrupted between its two file replacements.
        for name in ("manifest", "manifest_hash", "article_hash", "previous_article_hash", "previous_manifest_hash"):
            connection.execute(f"ALTER TABLE drafts ADD COLUMN {name} TEXT")
        for row in connection.execute("SELECT issue_id,article FROM drafts").fetchall():
            connection.execute("UPDATE drafts SET article_hash=? WHERE issue_id=?", (_hash(row["article"]), row["issue_id"]))
        for name, count in before.items():
            if connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] != count:
                raise DigestError("migration changed existing row counts")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or connection.execute("PRAGMA foreign_key_check").fetchone():
            raise DigestError("migration validation failed; original backup is available")
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    return str(backup.resolve()) if backup else None


def _connect(root: Path, *, create: bool = False) -> sqlite3.Connection | None:
    path = root / DB_PATH
    existed = path.exists()
    if not create and not existed:
        return None
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=30)
    else:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        if create:
            _migrate(connection, path, existed)
        elif connection.execute("PRAGMA user_version").fetchone()[0] > SCHEMA_VERSION:
            raise DigestError("database schema is newer than this program")
    except Exception:
        connection.close()


        raise
    return connection


def migrate(root: Path) -> dict:
    path = root / DB_PATH
    if not path.exists():
        connection = _connect(root, create=True)
        connection.close()
        return {"status": "created", "schema_version": SCHEMA_VERSION, "backup_path": None}
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        old_version = connection.execute("PRAGMA user_version").fetchone()[0]
        backup = _migrate(connection, path, True)
        return {"status": "unchanged" if old_version == SCHEMA_VERSION else "migrated", "schema_version": SCHEMA_VERSION, "backup_path": backup}
    finally:
        connection.close()


def ingest(root: Path, batch: dict) -> dict:
    identities = _validate_batch(batch)
    payload = _json(batch)
    collected = _timestamp(batch["collected_at"], "collected_at")
    if collected > _now():
        raise DigestError("collected_at cannot be in the future")
    connection = _connect(root, create=True)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT content_hash FROM batches WHERE run_id=?", (batch["run_id"],)).fetchone()
            if previous:
                if previous["content_hash"] != _hash(payload):
                    raise DigestError("run_id already exists with different content; use a new run_id")
                return {"status": "unchanged", "run_id": batch["run_id"], "observations": len(identities)}
            connection.execute("INSERT INTO batches VALUES (?,?,?,?,?,?)", (
                batch["run_id"], batch["collected_at"], collected.timestamp(),
                collected.astimezone(BEIJING).date().isoformat(), _hash(payload), payload))
            connection.executemany("INSERT INTO observations (run_id,canonical_url,payload) VALUES (?,?,?)", [
                (batch["run_id"], identity, _json(item)) for identity, item in zip(identities, batch["candidates"])])
        return {"status": "ingested", "run_id": batch["run_id"], "observations": len(identities)}
    finally:
        connection.close()


def _runs(connection: sqlite3.Connection | None, since: str, until: str) -> list[dict]:
    if connection is None:
        return []
    result = []
    for row in connection.execute("SELECT payload,local_date FROM batches WHERE local_date BETWEEN ? AND ? ORDER BY collected_epoch,run_id", (since, until)):
        batch = json.loads(row["payload"])
        result.append({"run_id": batch["run_id"], "collected_at": batch["collected_at"], "collection_date": row["local_date"],
                       "sources": batch["sources"], "observation_count": len(batch["candidates"])})
    return result


def _coverage(runs: list[dict], start: date, end: date) -> dict:
    dates = {run["collection_date"] for run in runs}
    missing = [(start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)
               if (start + timedelta(days=offset)).isoformat() not in dates]
    sources = [{"run_id": run["run_id"], "collected_at": run["collected_at"], **source} for run in runs for source in run["sources"]]
    return {"runs": runs, "source_status": sources, "missing_collection_dates": missing,
            "source_gaps": [source for source in sources if source["status"] == "failed"],
            "empty_sources": [source for source in sources if source["status"] == "empty"]}


def _observations(connection: sqlite3.Connection, identity: str, as_of: datetime | None = None) -> list[dict]:
    rows = connection.execute("""SELECT o.payload,o.run_id,b.collected_at,b.local_date,b.collected_epoch
        FROM observations o JOIN batches b USING(run_id) WHERE o.canonical_url=?
        ORDER BY b.collected_epoch DESC,o.id DESC""", (identity,)).fetchall()
    result = []
    for row in rows:
        if as_of and row["collected_epoch"] > as_of.timestamp():
            continue
        item = json.loads(row["payload"])
        item.update(run_id=row["run_id"], collected_at=row["collected_at"], collection_date=row["local_date"])
        item.setdefault("discovered_at", row["collected_at"])
        item.setdefault("verified_at", row["collected_at"] if item["evidence_status"] == "verified" else None)
        item.setdefault("verification_level", "documented")
        result.append(item)
    return result


def _first_discovered(history: list[dict]) -> str:
    return min((record["discovered_at"] for record in history), key=lambda value: _timestamp(value, "discovered_at"))


def _eligible(record: dict, first_discovered: str, start: date, end: date) -> bool:
    event = record.get("event")
    if record["kind"] == "news" and not event:
        # Initial source-review eligibility is not final Q12 eligibility. A late
        # discovery may describe an earlier event; publication metadata alone
        # cannot establish that event. Verified news and issues require it.
        return record["evidence_status"] == "discovered"
    # News belongs to the event's actual natural period, not a later discovery
    # or re-verification date. Undated discoveries can still reach source review.
    if record["kind"] == "news" and event and not _event_in_period(event, start, end):
        return False
    if event and _event_bounds(event)[0].astimezone(BEIJING).date() > end:
        return False
    if _timestamp(first_discovered, "discovered_at").astimezone(BEIJING).date() <= end:
        return True
    # Q12 late backfill requires a real event in the original period and an
    # original event URL among the evidence, with actual collection unchanged.
    return bool(event and _event_in_period(event, start, end)
                and _event_url(event["url"]) in {_event_url(url) for url in record["evidence_urls"]})


def _issue_rows(connection: sqlite3.Connection) -> list[dict]:
    if _has_table(connection, "ranked_issues"):
        return [dict(row) for row in connection.execute("SELECT * FROM ranked_issues ORDER BY archived_at DESC,issue_id")]
    return [{**dict(row), "issue_id": "weekly:legacy-" + row["date"], "ranking_type": "weekly", "period": "legacy-" + row["date"]}
            for row in connection.execute("SELECT * FROM issues ORDER BY date DESC")]


def _selection_history(connection: sqlite3.Connection, identity: str, ranking_type: str | None = None) -> list[dict]:
    if _has_table(connection, "ranked_selections"):
        rows = connection.execute("""SELECT s.payload,i.issue_id,i.ranking_type,i.period FROM ranked_selections s
            JOIN ranked_issues i USING(issue_id) WHERE canonical_url=? ORDER BY i.archived_at DESC""", (identity,)).fetchall()
        return [{"issue_id": row["issue_id"], "ranking_type": row["ranking_type"], "period": row["period"], **json.loads(row["payload"])}
                for row in rows if ranking_type is None or row["ranking_type"] == ranking_type]
    if ranking_type not in (None, "weekly"):
        return []
    return [{"issue_id": "weekly:legacy-" + row["issue_date"], "ranking_type": "weekly", "period": "legacy-" + row["issue_date"], **json.loads(row["payload"])}
            for row in connection.execute("SELECT * FROM selections WHERE canonical_url=?", (identity,))]


def _reported_event_urls(connection: sqlite3.Connection, ranking_type: str) -> set[str]:
    """Catch an event retold under another candidate URL or content kind."""
    if _has_table(connection, "ranked_selections"):
        rows = connection.execute("""SELECT s.payload FROM ranked_selections s
            JOIN ranked_issues i USING(issue_id) WHERE i.ranking_type=?""", (ranking_type,))
    elif ranking_type == "weekly":
        rows = connection.execute("SELECT payload FROM selections")
    else:
        return set()
    return {_event_url(item["event"]["url"]) for row in rows
            if (item := json.loads(row["payload"])).get("event")}


def _dedup_error(item: dict, prior: list[dict], reported_event_urls: set[str] | None = None) -> str | None:
    event = item.get("event")
    event_url = _event_url(event["url"]) if event else _event_url(item["url"]) if item["kind"] == "news" else None
    if event_url and event_url in (reported_event_urls or set()):
        return "same event already reported in same-level history (candidate URL and kind cannot bypass its original URL)"
    if not prior:
        return None
    if item["kind"] not in ("update", "news") or not event:
        return "already selected in same-level history; an unreported important update is required"
    for previous in prior:
        earlier = previous.get("event")
        if earlier and (earlier["id"] == event["id"] or _event_url(earlier["url"]) == _event_url(event["url"])):
            return "same event already reported in same-level history (wording and event IDs cannot bypass its URL)"
        # A project introduction already covers what was verified at that time.
        boundary = _event_bounds(earlier)[1] if earlier else (
            _timestamp(previous["verified_at"], "previous verification") if previous.get("verified_at") else None)
        if boundary and _event_bounds(event)[0] <= boundary:
            return "important update must be newer than all previously reported events or baseline verifications"
        if not earlier:
            old_evidence = {_event_url(url) for url in previous.get("evidence_urls", [])}
            if _event_url(event["url"]) in old_evidence:
                return "event evidence already reported in same-level history"
    return None


def _candidate_events(records: list[dict], prior: list[dict], *, deduplicate: bool,
                      reported_event_urls: set[str] | None = None) -> list[dict]:
    """Keep verification attached to a concrete event, never to the whole project.

    Event URLs preserve release/changelog anchors. An ID rename at the same URL
    does not create a different event or evade the all-history gate.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        event = record.get("event")
        # A news discovery already identifies its article. Source review adds
        # event facts to that same card instead of leaving a second unverified
        # card that can overwrite or revive the verified/reported event.
        event_url = _event_url(event["url"]) if event else _event_url(record["url"]) if record["kind"] == "news" else ""
        key = (record["kind"], event_url)
        groups.setdefault(key, []).append(record)
    events = []
    for (kind, event_url), observations in groups.items():
        verified = [record for record in observations if record["evidence_status"] == "verified"]
        effective = copy.deepcopy(max(verified or observations, key=lambda record: (
            _timestamp(record["verified_at"] if verified else record["collected_at"], "observation time"),
            _timestamp(record["collected_at"], "collected_at"))))
        block = _dedup_error(effective, prior, reported_event_urls) if deduplicate else None
        effective.update(event_key=kind + ":" + event_url, selection_block=block,
                         selection_status="blocked" if block else "available" if verified else "needs_evidence",
                         last_verified_at=effective["verified_at"] if verified else None,
                         event_last_collected_at=max((record["collected_at"] for record in observations),
                                                     key=lambda value: _timestamp(value, "collected_at")),
                         observation_count=len(observations))
        events.append(effective)
    # This chooses a representative event, not a project's editorial score.
    events.sort(key=lambda record: (
        {"available": 2, "needs_evidence": 1, "blocked": 0}[record["selection_status"]],
        _event_bounds(record["event"])[0] if record.get("event") else _timestamp(record["discovered_at"], "event time"),
        _timestamp(record["last_verified_at"] or record["collected_at"], "observation time")), reverse=True)
    return events


def candidates(root: Path, since: str | None = None, until: str | None = None, limit: int = 80, *,
               ranking_type: str | None = None, period: str | None = None,
               offset: int = 0, include_history: bool = True) -> dict:
    if ranking_type:
        start, end, _ = period_window(ranking_type, period)
        since, until = start.isoformat(), end.isoformat()
    else:
        start, end = _date(since, "since"), _date(until, "until")
    if (start > end or type(limit) is not int or not 1 <= limit <= 1000
            or type(offset) is not int or offset < 0 or type(include_history) is not bool):
        raise DigestError("invalid date window, limit (1..1000), offset (>=0), or include_history")
    connection = _connect(root)
    result = {"status": "empty" if connection is None else "ok", "ranking_type": ranking_type, "period": period,
              "window": {"since": since, "until": until, "timezone": "Asia/Shanghai", "inclusive": True},
              **_coverage(_runs(connection, since, until), start, end), "candidates": [], "total_candidates": 0,
              "returned_count": 0, "limit": limit, "offset": offset, "next_offset": None, "truncated": False,
              "available_count": 0, "needs_evidence_count": 0, "blocked_count": 0, "eligible_count": 0,
              "blocked_candidates": [], "include_history": include_history,
              "ordering": "canonical URL for stable pagination, not practical value; continue next_offset before restarting a review pass"}
    if connection is None:
        return result
    try:
        found = []
        reported_event_urls = _reported_event_urls(connection, ranking_type) if ranking_type else set()
        identities = connection.execute("SELECT DISTINCT canonical_url FROM observations ORDER BY canonical_url").fetchall()
        for row in identities:
            history = _observations(connection, row["canonical_url"])
            first = _first_discovered(history)
            if ranking_type:
                # Once an article's event is verified, its undated discovery
                # follows that event's period instead of reopening source review
                # in every other period. Keep the observations themselves intact.
                verified_news = {_event_url(entry["event"]["url"]): entry for entry in reversed(history)
                                 if entry["kind"] == "news" and entry["evidence_status"] == "verified" and entry.get("event")}
                records = [entry for entry in history if _eligible(
                    verified_news.get(_event_url(entry["url"]), entry)
                    if entry["kind"] == "news" and not entry.get("event") else entry,
                    first, start, end)]
            else:
                if not any(since <= record["collection_date"] <= until for record in history):
                    continue
                records = [entry for entry in history if entry["collection_date"] <= until]
            if not records:
                continue
            prior = _selection_history(connection, row["canonical_url"], ranking_type)
            events = _candidate_events(records, prior, deduplicate=bool(ranking_type), reported_event_urls=reported_event_urls)
            effective = copy.deepcopy(events[0])
            effective.update(canonical_url=row["canonical_url"], first_discovered_at=first,
                             source_urls=sorted({url for record in history for url in record["source_urls"]}),
                             last_collected_at=history[0]["collected_at"],
                             latest_observation=records[0], history=[{**record, "in_window": since <= record["collection_date"] <= until} for record in history] if include_history else [],
                             issue_history=prior if include_history else [], event_candidates=events,
                             period_label="往期候选" if _timestamp(first, "first_discovered").astimezone(BEIJING).date() < start else
                             "原期事件补采" if _timestamp(first, "first_discovered").astimezone(BEIJING).date() > end else "新发现")
            found.append(effective)
        eligible = [record for record in found if record["selection_status"] != "blocked"]
        blocked = [record for record in found if record["selection_status"] == "blocked"]
        page = eligible[offset:offset + limit]
        next_offset = offset + len(page) if offset + len(page) < len(eligible) else None
        # Keep blocked cards compact and separate; they never consume the review
        # limit. Full evidence/history remains available through include_history.
        result.update(candidates=page, total_candidates=len(found), returned_count=len(page),
                      truncated=next_offset is not None, next_offset=next_offset, eligible_count=len(eligible),
                      available_count=sum(record["selection_status"] == "available" for record in found),
                      needs_evidence_count=sum(record["selection_status"] == "needs_evidence" for record in found),
                      blocked_count=len(blocked), blocked_candidates=[{
                          key: record[key] for key in ("canonical_url", "title", "kind", "event_key", "selection_status", "selection_block")
                      } for record in blocked])
        return result
    finally:
        connection.close()


def _validate_issue(issue: Any, *, final: bool = False) -> tuple[date, date, list[str]]:
    _object(issue, ("schema_version", "ranking_type", "period", "title", "prepared_at", "shortfall_reason", "verification_note", "items"), "issue")
    if issue["schema_version"] != "digest-issue.v2":
        raise DigestError("new issues require digest-issue.v2; legacy date-only archive is read-only")
    if "presentation" in issue and issue["presentation"] != "discovery.v1":
        raise DigestError("unsupported issue presentation")
    discovery = issue.get("presentation") == "discovery.v1"
    start, end, _ = period_window(issue["ranking_type"], issue["period"])
    prepared = _timestamp(issue["prepared_at"], "prepared_at")
    if prepared > _now():
        raise DigestError("prepared_at cannot be in the future")
    if final and prepared.astimezone(BEIJING).date() <= end:
        raise DigestError("a complete natural period is required before formal archive")
    for field in ("title", "verification_note"):
        _text(issue[field], field)
    _text(issue["shortfall_reason"], "shortfall_reason", empty=True)
    items = _list(issue["items"], "items")
    count = len(items)
    target = {"daily": 5, "weekly": 20, "monthly": 20}[issue["ranking_type"]]
    maximum = {"daily": 8, "weekly": 25, "monthly": 1000}[issue["ranking_type"]]
    if count > maximum:
        raise DigestError(f"{issue['ranking_type']} may contain at most {maximum} items")
    if count < target and not issue["shortfall_reason"].strip():
        raise DigestError(f"fewer than {target} items requires shortfall_reason")
    if final and not count:
        raise DigestError("zero-item results remain drafts; no empty formal article")
    if final and issue["ranking_type"] == "monthly" and count < 20:
        raise DigestError("monthly requires at least 20 items; save a draft and backfill within Q12")
    for field in ("screened_count", "verified_count"):
        if field in issue and (type(issue[field]) is not int or issue[field] < count):
            raise DigestError(f"{field} must be an integer >= selected count")
    identities, event_urls, featured, nonreading = [], set(), 0, 0
    for item in items:
        _object(item, ("url", "kind", "featured", "title", "category", "summary", "reason", "audience", "usage_conditions",
                       "verification_level", "verified_at", "evidence_urls", "change_note"), "item")
        if item["kind"] not in KINDS or item["category"] not in CATEGORIES:
            raise DigestError("invalid item kind or category")
        if type(item["featured"]) is not bool or (item["featured"] and item["kind"] == "reading"):
            raise DigestError("featured must be a boolean and reading cannot be featured")
        for field in ("title", "summary", "reason", "audience", "usage_conditions"):
            _text(item[field], "item." + field)
        if discovery:
            _object(item, ("supported_systems",), "discovery item")
            if item["supported_systems"] is not None:
                _text(item["supported_systems"], "item.supported_systems")
        _text(item["change_note"], "item.change_note", empty=True)
        _urls(item["evidence_urls"], "item.evidence_urls", nonempty=True)
        verified = _timestamp(item["verified_at"], "item.verified_at")
        if verified > prepared:
            raise DigestError("item verification cannot be after prepared_at")
        if prepared - verified > timedelta(hours=24):
            raise DigestError("stale verification: re-read originals within 24 hours before prepared_at")
        if verified.astimezone(BEIJING).date() < start:
            raise DigestError("stale verification: re-read original sources during this period or later")
        if item["verification_level"] not in ("documented", "demo", "tested"):
            raise DigestError("invalid verification_level")
        if item["kind"] == "update":
            if not item["change_note"].strip() or not item.get("event"):
                raise DigestError("an important update requires change_note and a stable event")
        if item["kind"] == "news" and not item.get("event"):
            raise DigestError("news requires a stable event")
        if item.get("event"):
            _event(item["event"], item["kind"], verified)
            if _event_url(item["event"]["url"]) not in {_event_url(url) for url in item["evidence_urls"]}:
                raise DigestError("event URL must be included in original evidence_urls")
            if item["kind"] not in ("reading", "news") and _event_url(item["event"]["url"]) == canonical_url(item["url"]):
                raise DigestError("event URL must locate an original release or update, not just project home")
            event_url = _event_url(item["event"]["url"])
            if event_url in event_urls:
                raise DigestError("the same original event cannot occur twice in an issue, including different kinds")
            event_urls.add(event_url)
        if item["kind"] == "reading":
            _text(item.get("author"), "reading.author")
            _date(item.get("original_date"), "reading.original_date")
        if issue["ranking_type"] == "weekly":
            _text(item.get("detail"), "weekly item.detail")
        if issue["ranking_type"] == "monthly":
            _text(item.get("retention_reason"), "monthly item.retention_reason")
        identity = canonical_url(item["url"], item["kind"])
        if identity in identities:
            raise DigestError("the same canonical project/article cannot occur twice in an issue")
        identities.append(identity)
        featured += item["featured"]
        nonreading += item["kind"] != "reading"
    expected = min(3, nonreading)
    if issue["ranking_type"] == "weekly" and featured != expected:
        raise DigestError(f"weekly featured count must be {expected}")
    if issue["ranking_type"] != "weekly" and not (min(1, nonreading) <= featured <= expected):
        raise DigestError("daily/monthly featured count must be 1..3 when projects are present")
    opportunities = _list(issue.get("opportunities", []), "opportunities")
    if len(opportunities) > 2:
        raise DigestError("at most 2 development opportunities; excluded from selected count")
    for opportunity in opportunities:
        _object(opportunity, ("problem", "audience", "existing_solutions", "validation", "evidence_urls", "evidence_date"), "opportunity")
        for field in ("problem", "audience", "existing_solutions", "validation"):
            _text(opportunity[field], "opportunity." + field)
        _urls(opportunity["evidence_urls"], "opportunity.evidence_urls", nonempty=True)
        if _date(opportunity["evidence_date"], "opportunity.evidence_date") > prepared.astimezone(BEIJING).date():
            raise DigestError("opportunity evidence cannot be in the future")
    return start, end, identities


def _prepare(connection: sqlite3.Connection | None, issue: dict, *, final: bool = False) -> dict:
    start, end, identities = _validate_issue(issue, final=final)
    prepared = _timestamp(issue["prepared_at"], "prepared_at")
    result = copy.deepcopy(issue)
    result.update(issue_id=issue["ranking_type"] + ":" + issue["period"], window_start=start.isoformat(), window_end=end.isoformat(),
                  canonical_urls=identities, validation_scope="Structure, recorded evidence and history checked by Python; factual source review is editorial.")
    # Do not count future days in an ongoing draft as already missed.
    coverage_end = min(end, prepared.astimezone(BEIJING).date())
    runs = [run for run in _runs(connection, start.isoformat(), coverage_end.isoformat())
            if _timestamp(run["collected_at"], "collected_at") <= prepared]
    result["coverage"] = _coverage(runs, start, coverage_end)
    result["coverage"].update(checked_through=coverage_end.isoformat(), period_complete=prepared.astimezone(BEIJING).date() > end)
    result["coverage"]["not_elapsed_dates"] = [(coverage_end + timedelta(days=offset)).isoformat()
                                                for offset in range(1, (end - coverage_end).days + 1)]
    result["supplemental_runs"] = []
    supplemental_ids = set()
    if identities and connection is None:
        raise DigestError("candidate ledger does not exist; ingest verified observations first")
    reported_event_urls = _reported_event_urls(connection, issue["ranking_type"]) if connection else set()
    for identity, item in zip(identities, result["items"]):
        records = _observations(connection, identity, prepared)
        if not records:
            raise DigestError("item has no verified observation: " + identity)
        first = _first_discovered(records)
        matching = [record for record in records if record["evidence_status"] == "verified" and record["kind"] == item["kind"]
                    and _timestamp(record["verified_at"], "verified_at") == _timestamp(item["verified_at"], "item.verified_at")
                    and record["verification_level"] == item["verification_level"]]
        if item.get("event"):
            matching = [record for record in matching if record.get("event") and
                        record["event"]["id"] == item["event"]["id"] and
                        _event_url(record["event"]["url"]) == _event_url(item["event"]["url"]) and
                        _event_time_key(record["event"]) == _event_time_key(item["event"])]
        else:
            matching = [record for record in matching if not record.get("event")]
        selected_evidence = {_event_url(url) for url in item["evidence_urls"]}
        matching = [record for record in matching if selected_evidence <= {_event_url(url) for url in record["evidence_urls"]}]
        if not matching:
            raise DigestError("item has no verified observation matching kind/event/time/level/evidence: " + identity)
        matching = [record for record in matching if _eligible(record, first, start, end)]
        if not matching:
            raise DigestError("candidate does not belong wholly to this period; Q12 forbids backdating or uncertain date boundaries: " + identity)
        prior = _selection_history(connection, identity, issue["ranking_type"])
        problem = _dedup_error(item, prior, reported_event_urls)
        if problem:
            raise DigestError(problem + ": " + identity)
        observation = matching[0]
        discovered_day = _timestamp(first, "first_discovered").astimezone(BEIJING).date()
        item.update(canonical_url=identity, first_discovered_at=first, collected_at=observation["collected_at"],
                    observation_run_id=observation["run_id"], published_at=observation.get("published_at"),
                    period_label="往期候选" if discovered_day < start else "原期事件补采" if discovered_day > end else "新发现")
        if observation["collection_date"] > end.isoformat():
            supplemental_ids.add(observation["run_id"])
    if supplemental_ids:
        for run in _runs(connection, (end + timedelta(days=1)).isoformat(), prepared.astimezone(BEIJING).date().isoformat()):
            if run["run_id"] in supplemental_ids:
                result["supplemental_runs"].append(run)
    return result


def _render_discovery(document: dict, *, draft: bool, preview: bool = False) -> str:
    """Reader-facing discovery view; audit fields remain in the manifest."""
    def day(value: str) -> str:
        return _timestamp(value, "display timestamp").astimezone(BEIJING).date().isoformat()

    items = document["items"]
    if preview:
        lines = [f"# 介绍样式预览 · 示例 {len(items)} 条", "",
                 "复用已保存资料，未重新筛选、评分或核验。以下内容不构成任何自然周期的榜单。", ""]
    else:
        lines = [f"# {document['title']}", "",
                 f"{'草稿 · ' if draft else ''}{RANKING_NAMES[document['ranking_type']]} {document['period']} · 北京时间 {document['window_start']} 至 {document['window_end']}",
                 "", f"实际整理：{day(document['prepared_at'])}；精选 {len(items)} 条。", ""]
    if not preview and document["shortfall_reason"]:
        lines += ["数量说明：" + document["shortfall_reason"], ""]
    featured = [item for item in items if item["featured"]]
    if featured and not preview:
        lines += ["重点推荐：" + "；".join(f"[{item['title']}]({item['url']})" for item in featured) + "。", ""]
    categories = [category for category in CATEGORIES if any(item["category"] == category for item in items)]
    if categories:
        lines += [("示例栏目：" if preview else "本期栏目：") + " / ".join(categories), ""]
    for category in categories:
        lines += ["## " + category, ""]
        for item in items:
            if item["category"] != category:
                continue
            lines += [f"### {'★ ' if item['featured'] and not preview else ''}{item['title']}", "", item["summary"], ""]
            if document["ranking_type"] == "weekly" and item["featured"]:
                lines += [item["detail"], ""]
            if document["ranking_type"] == "monthly":
                lines += ["值得保留：" + item["retention_reason"], ""]
            if item["kind"] == "update":
                lines += ["重要更新：" + item["change_note"], ""]
            if item["kind"] in ("project", "update") and item["supported_systems"] is not None:
                lines += ["支持系统：" + item["supported_systems"], ""]
            if item["kind"] == "reading":
                lines += [f"作者／受访者：{item['author']}；原文日期：{item['original_date']}。", ""]
            if item["kind"] == "news":
                event_date = (item["event"]["occurred_on"] + "（原文仅日期，时区未知；未确认具体时刻）"
                              if "occurred_on" in item["event"] else day(item["event"]["occurred_at"]))
                lines += [f"新闻事件日期：{event_date}。", ""]
            link_label = "原文" if item["kind"] in ("reading", "news") else "官方入口"
            links = f"[{item['title']} {link_label}]({item['url']})"
            if item.get("event") and item["event"]["url"] != item["url"]:
                links += f" · [事件原文]({item['event']['url']})"
            lines += [links, ""]
            if not preview and item["period_label"] != "新发现":
                lines += [f"{item['period_label']}；首次发现 {day(item['first_discovered_at'])}。", ""]
    if document.get("opportunities") and not preview:
        lines += ["## 开发机会观察（不计入精选条数）", ""]
        for opportunity in document["opportunities"]:
            lines += [opportunity["problem"], "", f"人群：{opportunity['audience']}；已有方案：{opportunity['existing_solutions']}。", "",
                      "验证切口：" + opportunity["validation"], "", "用户问题资料（" + opportunity["evidence_date"] + "）：" +
                      " / ".join(f"[原文 {index + 1}]({url})" for index, url in enumerate(opportunity["evidence_urls"])), ""]

    if preview:
        urls = dict.fromkeys(url for item in items for url in item["evidence_urls"])
        source_dates = document.get("preview_source_dates", {})
        lines += ["## 保存的来源资料", ""]
        for index, url in enumerate(urls, 1):
            recorded_date = source_dates.get(url)
            suffix = "；资料日期：" + recorded_date if isinstance(recorded_date, str) and recorded_date.strip() else ""
            lines += [f"- [保存的原文 {index}]({url}){suffix}"]
        lines += [""]
        return "\n".join(lines)

    # Keep real collection gaps and failures visible without copying the audit log.
    coverage = document["coverage"]
    sources = coverage["source_status"] + [source for run in document["supplemental_runs"] for source in run["sources"]]
    source_links = dict.fromkeys(f"[{source['name']}]({source['url']})（{source['status']}）" for source in sources)
    failures = dict.fromkeys(f"{source['name']}：{source['detail']}" for source in sources if source["status"] == "failed")
    lines += ["## 来源说明", "", "实际来源：" + (" / ".join(source_links) or "未记录可用来源") + "。", "",
              "本期实际采集日期：" + ("、".join(sorted({run["collection_date"] for run in coverage["runs"]})) or "无") + "。", "",
              "缺少采集的日期：" + ("、".join(coverage["missing_collection_dates"]) or "无") + f"（检查截至 {coverage['checked_through']}）。", ""]
    if document["supplemental_runs"]:
        lines += ["期后核验／补采实际日期：" + "、".join(sorted({run["collection_date"] for run in document["supplemental_runs"]})) + "。不改写原期采集覆盖。", ""]
    lines += ["来源失败：" + ("；".join(failures) or "本次记录中无失败；不代表未访问来源可用") + "。", ""]
    return "\n".join(lines)


def _render(document: dict, *, draft: bool) -> str:
    if document.get("presentation") == "discovery.v1":
        return _render_discovery(document, draft=draft)
    def day(value: str) -> str:
        return _timestamp(value, "display timestamp").astimezone(BEIJING).date().isoformat()
    name = RANKING_NAMES[document["ranking_type"]]
    items = document["items"]
    lines = [f"# {document['title']}", "", f"{'草稿 · ' if draft else ''}{name} {document['period']} · 北京时间 {document['window_start']} 至 {document['window_end']}",
             "", f"实际整理：{day(document['prepared_at'])}；精选 {len(items)} 条。", ""]
    if document["shortfall_reason"]:
        lines += [f"数量说明：{document['shortfall_reason']}", ""]
    featured = [item for item in items if item["featured"]]
    if featured:
        lines += ["重点推荐：" + "；".join(f"[{item['title']}]({item['url']})：{item['reason'].rstrip('。；; ')}" for item in featured) + "。", ""]
    categories = [category for category in CATEGORIES if any(item["category"] == category for item in items)]
    if categories:
        lines += ["本期栏目：" + " / ".join(categories), ""]
    levels = {"documented": "已读原始文档，未作本机实测结论", "demo": "已查看演示", "tested": "已本机实测"}
    for category in categories:
        lines += ["## " + category, ""]
        for item in items:
            if item["category"] != category:
                continue
            audience_label, conditions_label = ("影响人群", "适用范围与行动") if item["kind"] == "news" else ("适合", "使用条件")
            lines += [f"### {'★ ' if item['featured'] else ''}{item['title']}", "",
                       item["summary"], "", item["reason"], "", f"{audience_label}：{item['audience'].rstrip('。；; ')}。{conditions_label}：{item['usage_conditions']}", ""]
            if document["ranking_type"] == "weekly":
                lines += [item["detail"], ""]
            if document["ranking_type"] == "monthly":
                lines += ["值得保留：" + item["retention_reason"], ""]
            if item["kind"] == "update":
                lines += ["重要更新：" + item["change_note"], ""]
            if item["kind"] == "reading":
                lines += [f"作者／受访者：{item['author']}；原文日期：{item['original_date']}。", ""]
            if item["kind"] == "news":
                event_date = (item["event"]["occurred_on"] + "（原文仅日期，时区未知；未确认具体时刻）"
                              if "occurred_on" in item["event"] else day(item["event"]["occurred_at"]))
                lines += [f"新闻事件日期：{event_date}。", ""]
            published = item.get("published_at") or "未确认"
            if "T" in published:
                published = day(published)
            verification = "已核对原始新闻资料" if item["kind"] == "news" and item["verification_level"] == "documented" else levels[item["verification_level"]]
            lines += [f"[{item['title']} 官方入口]({item['url']}) · {item['period_label']}；首次发现 {day(item['first_discovered_at'])}；真实发布时间 {published}。", "",
                       f"核验：{verification}；核验日期 {day(item['verified_at'])}；实际采集 {day(item['collected_at'])}。", "",
                      "资料：" + " / ".join(f"[原始资料 {index + 1}]({url})" for index, url in enumerate(item["evidence_urls"])), ""]
    if document.get("opportunities"):
        lines += ["## 开发机会观察（不计入精选条数）", ""]
        for opportunity in document["opportunities"]:
            lines += [opportunity["problem"], "", f"人群：{opportunity['audience']}；已有方案：{opportunity['existing_solutions']}。", "",
                      "验证切口：" + opportunity["validation"], "", "用户问题资料（" + opportunity["evidence_date"] + "）：" +
                      " / ".join(f"[原文 {index + 1}]({url})" for index, url in enumerate(opportunity["evidence_urls"])), ""]
    coverage = document["coverage"]
    lines += ["## 来源与核验说明", "", document["verification_note"], "",
              f"实际初筛：{document.get('screened_count', '未单独记录')}；深入核验：{document.get('verified_count', '未单独记录')}；入选：{len(items)}。", "",
              "本期实际采集日期：" + ("、".join(sorted({run["collection_date"] for run in coverage["runs"]})) or "无") + "。", "",
              "缺少采集的日期：" + ("、".join(coverage["missing_collection_dates"]) or "无") + f"（检查截至 {coverage['checked_through']}）。", ""]
    sources = coverage["source_status"] + [{"run_id": run["run_id"], **source} for run in document["supplemental_runs"] for source in run["sources"]]
    if document["supplemental_runs"]:
        lines += ["期后核验／补采实际日期：" + "、".join(sorted({run["collection_date"] for run in document["supplemental_runs"]})) + "。不改写原期采集覆盖。", ""]
    if sources:
        lines += ["实际来源状态：", ""]
        lines += [f"- [{source['name']}]({source['url']})：{source['status']}；{source['detail']}" for source in sources]
        lines += [""]
    lines += ["来源失败：" + ("；".join(f"{source['name']}：{source['detail']}" for source in sources if source["status"] == "failed") or "本次记录中无失败；不代表未访问来源可用") + "。", ""]
    return "\n".join(lines)


def render(root: Path, issue: dict, *, draft: bool = True) -> str:
    connection = _connect(root)
    try:
        return _render(_prepare(connection, issue, final=not draft), draft=draft)
    finally:
        if connection:
            connection.close()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            handle.write(content.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()


def _write_archive_file(path: Path, content: str) -> None:
    if path.exists():
        if path.read_bytes() != content.encode("utf-8"):
            raise DigestError(f"archive file already exists with different content: {path}")
        return
    _atomic_write(path, content)


def _exports(root: Path, ranking_type: str, period: str, *, draft: bool = False) -> tuple[Path, Path]:
    folder = root / ARTICLE_DIR / ranking_type
    if draft:
        folder /= "drafts"
    return folder / (period + ".md"), folder / (period + ".json")


def _snapshot_exports(root: Path, row: dict, *, draft: bool) -> list[dict]:
    """Describe exact database text; recovery never re-renders archived material."""
    article_path, manifest_path = _exports(root, row["ranking_type"], row["period"], draft=draft)
    entries = []
    for label, path, text, expected, previous in (
        ("article", article_path, row["article"], row["article_hash"], row.get("previous_article_hash") if draft else None),
        ("manifest", manifest_path, row.get("manifest"), row.get("manifest_hash") if draft else row["content_hash"], row.get("previous_manifest_hash") if draft else None),
    ):
        if text is not None and _hash(text) != expected:
            raise DigestError(f"database {label} hash mismatch for {row['issue_id']}; exports cannot be trusted")
        current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        state = ("legacy_untracked" if text is None else "missing" if current is None else "ok" if current == expected else
                 "previous_revision" if draft and previous and current == previous else "conflict")
        entries.append({"path": path, "text": text, "expected_sha256": expected, "actual_sha256": current,
                        "state": state, "draft": draft})
    return entries


def _sync_snapshot(root: Path, row: dict, *, draft: bool) -> list[str]:
    entries = _snapshot_exports(root, row, draft=draft)
    for entry in entries:
        if entry["state"] == "conflict":
            raise DigestError(f"export conflict; inspect with recover before replacing: {entry['path']}")
        if entry["state"] == "legacy_untracked" and entry["actual_sha256"] is not None:
            raise DigestError(f"legacy draft has no saved manifest; recover --apply --quarantine preserves its old export before refreshing: {entry['path']}")
    replaced = []
    for entry in entries:
        if entry["text"] is None or entry["state"] == "ok":
            continue
        if entry["state"] == "previous_revision":
            _atomic_write(entry["path"], entry["text"])
            replaced.append(str(entry["path"].resolve()))
        else:
            _write_archive_file(entry["path"], entry["text"])
    return replaced


def recover(root: Path, *, ranking_type: str, period: str, apply: bool = False, quarantine: bool = False) -> dict:
    """Inspect exports by default; explicitly restore DB text or preserve conflicts.

    Quarantining an orphan never imports it as an archive or bypasses freshness
    checks. Its text is preserved for review, then a normal fresh archive is due.
    """
    period_window(ranking_type, period)
    if quarantine and not apply:
        raise DigestError("quarantine requires --apply; inspect first without either flag")
    issue_id = ranking_type + ":" + period
    connection = _connect(root)
    entries, snapshot_rows, retired_draft_hashes = [], [], {}
    try:
        for draft, table in ((False, "ranked_issues"), (True, "drafts")):
            raw = connection.execute(f"SELECT * FROM {table} WHERE issue_id=?", (issue_id,)).fetchone() if connection and _has_table(connection, table) else None
            if raw:
                row = dict(raw)
                if draft:
                    row.setdefault("manifest", None)
                    row.setdefault("manifest_hash", None)
                    row.setdefault("article_hash", _hash(row["article"]))
                else:
                    retired_draft_hashes = json.loads(row["manifest"]).get("superseded_draft_exports", {})
                entries.extend(_snapshot_exports(root, row, draft=draft))
                snapshot_rows.append((draft, row))
            else:
                for path in _exports(root, ranking_type, period, draft=draft):
                    if path.exists():
                        actual = hashlib.sha256(path.read_bytes()).hexdigest()
                        entries.append({"path": path, "text": None, "expected_sha256": None,
                                        "actual_sha256": actual,
                                        "state": "superseded_draft" if draft and actual in retired_draft_hashes.get(path.suffix, []) else "orphan", "draft": draft})
        report = {"status": "inspection", "issue_id": issue_id,
                  "exports": [{key: str(value.resolve()) if key == "path" else value for key, value in entry.items() if key != "text"} for entry in entries],
                  "quarantined": [], "restored": [], "removed_superseded_drafts": [],
                  "note": "Database is authoritative; orphan exports are never accepted as a formal archive."}
        if not apply:
            return report
        unsafe = [entry for entry in entries if entry["state"] in ("conflict", "orphan")
                  or (entry["state"] == "legacy_untracked" and entry["actual_sha256"] is not None)]
        if unsafe and not quarantine:
            raise DigestError("export conflict or orphan requires explicit recover --apply --quarantine; inspect first")
        backup_dir = root / ARTICLE_DIR / "recovery" / _now().strftime("%Y%m%dT%H%M%S%f")
        for entry in unsafe:
            path = entry["path"]
            relative = path.resolve().relative_to(root.resolve())
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["actual_sha256"]:
                raise DigestError("export changed during recovery inspection; retry inspection")
            backup = backup_dir / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists():
                raise DigestError("recovery backup already exists; inspect before retrying")
            os.replace(path, backup)
            report["quarantined"].append({"path": str(path.resolve()), "backup_path": str(backup.resolve()), "sha256": entry["actual_sha256"]})
        for draft, row in snapshot_rows:
            _sync_snapshot(root, row, draft=draft)
        for entry in entries:
            if entry["state"] == "superseded_draft":
                if hashlib.sha256(entry["path"].read_bytes()).hexdigest() != entry["actual_sha256"]:
                    raise DigestError("superseded draft changed during recovery; inspect again")
                entry["path"].unlink()
                report["removed_superseded_drafts"].append(str(entry["path"].resolve()))
        for entry in entries:
            if entry["text"] is not None and entry["state"] != "ok":
                report["restored"].append(str(entry["path"].resolve()))
        if connection:
            report["history_path"] = _write_indexes(root, connection)
        report["status"] = "requires_draft_refresh" if any(entry["state"] == "legacy_untracked" for entry in entries) else "recovered"
        return report
    finally:
        if connection:
            connection.close()


def _history_summary(row: dict) -> dict:
    manifest = json.loads(row["manifest"])
    legacy = row["period"].startswith("legacy-")
    article_path = (Path("outputs/weekly_digest") / (row["period"][7:] + ".md") if legacy else
                    ARTICLE_DIR / row["ranking_type"] / (row["period"] + ".md"))
    return {"issue_id": row["issue_id"], "ranking_type": row["ranking_type"], "period": row["period"], "title": row["title"],
            "item_count": len(manifest.get("items", [])), "article_path": article_path.as_posix(),
            "article_sha256": row["article_hash"], "manifest_sha256": row["content_hash"], "legacy": legacy}


def _write_indexes(root: Path, connection: sqlite3.Connection) -> str:
    rows = [_history_summary(row) for row in _issue_rows(connection)]
    drafts = [dict(row) for row in connection.execute("SELECT issue_id,ranking_type,period,payload,updated_at FROM drafts ORDER BY period DESC")] if _has_table(connection, "drafts") else []
    main = ["# 日榜、周榜与月榜历史", "", "正式归档不可覆盖；草稿会在补采核验后更新。", ""]
    for kind in RANKINGS:
        main += [f"- [{RANKING_NAMES[kind]}历史]({kind}/index.md)"]
        lines = [f"# {RANKING_NAMES[kind]}历史", "", "## 正式归档", ""]
        entries = sorted([row for row in rows if row["ranking_type"] == kind], key=lambda row: row["period"], reverse=True)
        lines += [f"- [{row['period']} · {row['title']}]({os.path.relpath(root / row['article_path'], root / ARTICLE_DIR / kind).replace(os.sep, '/')}) · {row['item_count']} 条" for row in entries] or ["暂无正式归档。"]
        lines += ["", "## 草稿", ""]
        entries = [row for row in drafts if row["ranking_type"] == kind]
        lines += [f"- [{row['period']} · {json.loads(row['payload'])['title']}](drafts/{row['period']}.md) · {len(json.loads(row['payload'])['items'])} 条" for row in entries] or ["暂无草稿。"]
        _atomic_write(root / ARTICLE_DIR / kind / "index.md", "\n".join(lines) + "\n")
    _atomic_write(root / ARTICLE_DIR / "index.md", "\n".join(main) + "\n")
    return str((root / ARTICLE_DIR / "index.md").resolve())


def save_draft(root: Path, issue: dict) -> dict:
    _validate_issue(issue)
    connection = _connect(root, create=True)
    committed = False
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            issue_id = issue["ranking_type"] + ":" + issue["period"]
            if connection.execute("SELECT 1 FROM ranked_issues WHERE issue_id=?", (issue_id,)).fetchone():
                raise DigestError("issue is already formally archived; cannot replace it with a draft")
            previous = connection.execute("SELECT * FROM drafts WHERE issue_id=?", (issue_id,)).fetchone()
            if previous:
                # Finish any earlier committed export before replacing this
                # revision; only known database bytes may be overwritten.
                _sync_snapshot(root, dict(previous), draft=True)
            document = _prepare(connection, issue)
            article = _render(document, draft=True)
            article_path, manifest_path = _exports(root, issue["ranking_type"], issue["period"], draft=True)
            text = json.dumps({**document, "status": "draft"}, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            if not previous and any(path.exists() for path in (article_path, manifest_path)):
                raise DigestError("orphan draft exports exist; inspect recover before saving a new draft")
            connection.execute("""INSERT INTO drafts
                (issue_id,ranking_type,period,payload,article,updated_at,manifest,manifest_hash,article_hash,previous_article_hash,previous_manifest_hash)
                VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(issue_id) DO UPDATE SET
                payload=excluded.payload,article=excluded.article,updated_at=excluded.updated_at,
                manifest=excluded.manifest,manifest_hash=excluded.manifest_hash,article_hash=excluded.article_hash,
                previous_article_hash=excluded.previous_article_hash,previous_manifest_hash=excluded.previous_manifest_hash""",
                (issue_id, issue["ranking_type"], issue["period"], _json(issue), article, issue["prepared_at"], text, _hash(text), _hash(article),
                 previous["article_hash"] if previous else None, previous["manifest_hash"] if previous else None))
        committed = True
        row = dict(connection.execute("SELECT * FROM drafts WHERE issue_id=?", (issue_id,)).fetchone())
        replaced = _sync_snapshot(root, row, draft=True)
        index = _write_indexes(root, connection)
        return {"status": "draft", "issue_id": issue_id, "item_count": len(issue["items"]),
                "shortfall": max(0, {"daily": 5, "weekly": 20, "monthly": 20}[issue["ranking_type"]] - len(issue["items"])),
                "article_path": str(article_path.resolve()), "manifest_path": str(manifest_path.resolve()), "history_path": index,
                "article_sha256": _hash(article), "manifest_sha256": _hash(text), "updated_previous_exports": replaced}
    except (OSError, DigestError) as exc:
        if committed:
            raise DigestError(f"draft committed; export pending for {issue_id}; use recover --apply: {exc}") from exc
        raise
    finally:
        connection.close()


def archive(root: Path, issue: dict, article: str | None = None) -> dict:
    _validate_issue(issue, final=True)
    if not (root / DB_PATH).exists():
        raise DigestError("candidate ledger does not exist; ingest verified observations first")
    connection = _connect(root, create=True)
    issue_id = issue["ranking_type"] + ":" + issue["period"]
    article_path, manifest_path = _exports(root, issue["ranking_type"], issue["period"])
    committed = False
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT * FROM ranked_issues WHERE issue_id=?", (issue_id,)).fetchone()
            old_draft = connection.execute("SELECT * FROM drafts WHERE issue_id=?", (issue_id,)).fetchone()
            if previous:
                if previous["input_hash"] != _hash(_json(issue)) or (article is not None and article != previous["article"]):
                    raise DigestError("issue already exists with different content; existing issues cannot be overwritten")
                rendered, manifest_text = previous["article"], previous["manifest"]
            else:
                if any(path.exists() for path in (article_path, manifest_path)):
                    raise DigestError("orphan archive exports exist without a committed issue; inspect recover and quarantine before retrying")
                if _now() - _timestamp(issue["prepared_at"], "prepared_at") > timedelta(hours=24):
                    raise DigestError("stale prepared_at: re-check sources and same-level history before finalizing a delayed draft")
                if any(_now() - _timestamp(item["verified_at"], "item.verified_at") > timedelta(hours=24) for item in issue["items"]):
                    raise DigestError("stale verification: originals must be checked within 24 hours of actual formal archive")
                document = _prepare(connection, issue, final=True)
                rendered = _render(document, draft=False)
                if article is not None and article != rendered:
                    raise DigestError("article must exactly match the structured render; body and selection cannot diverge")
                document.update(status="archived", article_path=article_path.relative_to(root).as_posix(), article_sha256=_hash(rendered), issue_sha256=_hash(_json(issue)))
                if old_draft:
                    document["superseded_draft_exports"] = {
                        ".md": [value for value in (old_draft["article_hash"], old_draft["previous_article_hash"]) if value],
                        ".json": [value for value in (old_draft["manifest_hash"], old_draft["previous_manifest_hash"]) if value],
                    }
                manifest_text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                connection.execute("INSERT INTO ranked_issues VALUES (?,?,?,?,?,?,?,?,?,?)", (
                    issue_id, issue["ranking_type"], issue["period"], issue["title"], _hash(_json(issue)), manifest_text,
                    _hash(manifest_text), rendered, _hash(rendered), _now().isoformat()))
                connection.executemany("INSERT INTO ranked_selections VALUES (?,?,?)", [
                    (issue_id, item["canonical_url"], _json(item)) for item in document["items"]])
            connection.execute("DELETE FROM drafts WHERE issue_id=?", (issue_id,))
        committed = True
        row = dict(connection.execute("SELECT * FROM ranked_issues WHERE issue_id=?", (issue_id,)).fetchone())
        _sync_snapshot(root, row, draft=False)
        index = _write_indexes(root, connection)
        if old_draft:
            # Unrecognized edits are left for explicit recovery, not deleted.
            for entry in _snapshot_exports(root, dict(old_draft), draft=True):
                if entry["state"] in ("ok", "previous_revision"):
                    entry["path"].unlink()
        return {"status": "unchanged" if previous else "archived", "issue_id": issue_id, "item_count": len(issue["items"]),
                "article_path": str(article_path.resolve()), "manifest_path": str(manifest_path.resolve()), "history_path": index,
                "article_sha256": _hash(rendered), "manifest_sha256": _hash(manifest_text)}
    except (OSError, DigestError) as exc:
        if committed:
            raise DigestError(f"archive committed; export pending for {issue_id}; use recover --apply: {exc}") from exc
        raise
    finally:
        connection.close()


def status(root: Path) -> dict:
    connection = _connect(root)
    empty = {"status": "empty", "schema_version": None, "candidate_count": 0, "observation_count": 0, "batch_count": 0,
             "issue_count": 0, "recent_issues": [], "drafts": [], "last_collected_at": None, "collection_dates": [],
             "source_gaps": [], "empty_sources": [], "coverage_window": None, "missing_collection_dates": []}
    if connection is None:
        return empty
    try:
        latest = connection.execute("SELECT collected_at,local_date FROM batches ORDER BY collected_epoch DESC LIMIT 1").fetchone()
        end = _now().date()
        start = end - timedelta(days=6)
        rows = _issue_rows(connection)
        drafts = [{"issue_id": row["issue_id"], "ranking_type": row["ranking_type"], "period": row["period"],
                   "item_count": len(json.loads(row["payload"])["items"]), "updated_at": row["updated_at"]}
                  for row in connection.execute("SELECT * FROM drafts ORDER BY period")] if _has_table(connection, "drafts") else []
        return {"status": "ok", "schema_version": connection.execute("PRAGMA user_version").fetchone()[0],
                "candidate_count": connection.execute("SELECT COUNT(DISTINCT canonical_url) FROM observations").fetchone()[0],
                "observation_count": connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
                "batch_count": connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0], "issue_count": len(rows),
                "recent_issues": [_history_summary(row) for row in rows[:12]], "drafts": drafts,
                "last_collected_at": latest["collected_at"] if latest else None,
                "collection_dates": [row[0] for row in connection.execute("SELECT DISTINCT local_date FROM batches ORDER BY local_date")],
                "coverage_window": {"since": start.isoformat(), "until": end.isoformat(), "timezone": "Asia/Shanghai"},
                **_coverage(_runs(connection, start.isoformat(), end.isoformat()), start, end)}
    finally:
        connection.close()


def due(root: Path, now: str | None = None) -> dict:
    moment = _timestamp(now, "now").astimezone(BEIJING) if now else _now()
    today = moment.date()
    periods = {"daily": (today - timedelta(days=1)).isoformat(),
               "weekly": (today - timedelta(days=today.weekday() + 7)).isoformat(),
               "monthly": (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")}
    state = status(root)
    connection = _connect(root)
    try:
        delivered = {row["issue_id"] for row in _issue_rows(connection)} if connection else set()
    finally:
        if connection:
            connection.close()
    result = []
    for kind, period in periods.items():
        start, end, due_at = period_window(kind, period)
        issue_id = kind + ":" + period
        result.append({"issue_id": issue_id, "ranking_type": kind, "period": period, "window_start": start.isoformat(),
                       "window_end": end.isoformat(), "due_at": due_at.isoformat(), "due": moment >= due_at and issue_id not in delivered,
                       "archived": issue_id in delivered, "has_draft": any(draft["issue_id"] == issue_id for draft in state["drafts"])})
    pending = [{**draft, "shortfall": max(0, 20 - draft["item_count"])} for draft in state["drafts"]
               if draft["ranking_type"] == "monthly" and period_window("monthly", draft["period"])[2] <= moment]
    return {"status": "ok", "now": moment.isoformat(), "periods": result, "pending_monthly": pending,
            "note": "Latest complete cycles only; missing older daily/weekly periods require an explicit period. Drafts do not reserve selection history."}


def dispatch(root: Path, now: str | None = None) -> dict:
    """Choose work once at wakeup; one thread heartbeat serves all four slots."""
    moment = _timestamp(now, "now").astimezone(BEIJING) if now else _now()
    today = moment.date()
    slot = f"{moment.hour:02d}:00"
    result = {"status": "ok", "evaluated_at": moment.isoformat(), "timezone": "Asia/Shanghai",
              "slot": slot, "actions": [],
              "note": "Read dispatch once at run start and keep these actions if execution crosses an hour. Empty actions require no collection or network access."}
    kind = ("daily" if moment.hour == 9 else "weekly" if moment.hour == 10 and today.weekday() == 0
            else "monthly" if moment.hour == 11 and today.day == 1 else None)
    if kind is None and moment.hour != 19:
        return result
    state = due(root, moment.isoformat())
    if kind:
        cycle = next(cycle for cycle in state["periods"] if cycle["ranking_type"] == kind)
        if cycle["due"]:
            result["actions"].append({"action": "generate", "ranking_type": kind,
                                      "period": cycle["period"], "issue_id": cycle["issue_id"]})
    else:
        result["actions"].append({"action": "collect", "period": today.isoformat()})
        pending = sorted(state["pending_monthly"], key=lambda entry: entry["period"])
        if pending:
            oldest = pending[0]
            result["actions"].append({"action": "backfill_monthly", "ranking_type": "monthly",
                                      "period": oldest["period"], "issue_id": oldest["issue_id"],
                                      "shortfall": oldest["shortfall"]})
    return result


def _read_json(path: Path) -> dict:
    def invalid_constant(value: str) -> None:
        raise DigestError(f"non-finite JSON number is not supported: {value}")
    return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=invalid_constant)


def main(argv: list[str] | None = None) -> int:
    class Parser(argparse.ArgumentParser):
        def error(self, message: str) -> None:
            raise DigestError(message)
    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("ingest", "candidates", "render", "draft", "archive", "status", "migrate", "due", "dispatch", "history", "recover"):
        command = commands.add_parser(name)
        command.add_argument("--root", type=Path, required=True)
        if name in ("ingest", "render", "draft", "archive"):
            command.add_argument("--file", type=Path, required=True)
        if name == "archive":
            command.add_argument("--article", type=Path)
        if name == "render":
            command.add_argument("--output", type=Path)
            command.add_argument("--final", action="store_true")
        if name == "candidates":
            command.add_argument("--since")
            command.add_argument("--until")
            command.add_argument("--type", choices=RANKINGS, dest="ranking_type")
            command.add_argument("--period")
            command.add_argument("--limit", type=int, default=80)
            command.add_argument("--offset", type=int, default=0)
            command.add_argument("--compact", action="store_true", help="omit full observation and issue history")
        if name == "recover":
            command.add_argument("--type", choices=RANKINGS, dest="ranking_type", required=True)
            command.add_argument("--period", required=True)
            command.add_argument("--apply", action="store_true", help="restore missing exports from committed database text")
            command.add_argument("--quarantine", action="store_true", help="preserve conflicts/orphans in recovery backups before restoring; requires --apply")
        if name in ("due", "dispatch"):
            command.add_argument("--now")
    try:
        args = parser.parse_args(argv)
        root = args.root.resolve()
        if args.command == "ingest":
            result = ingest(root, _read_json(args.file))
        elif args.command == "candidates":
            if (args.ranking_type or args.period) and (not args.ranking_type or not args.period or args.since or args.until):
                raise DigestError("use --type with --period, or --since with --until")
            result = candidates(root, args.since, args.until, args.limit, ranking_type=args.ranking_type, period=args.period,
                                offset=args.offset, include_history=not args.compact)
        elif args.command == "render":
            content = render(root, _read_json(args.file), draft=not args.final)
            if args.output:
                output_path = args.output.resolve()
                output_base = (root / ARTICLE_DIR).resolve()
                # Render is a preview. Only archive/draft may write managed history.
                if output_path == output_base or output_base in output_path.parents:
                    raise DigestError("render --output must be outside managed outputs/digest; use draft/archive to save there")
                _atomic_write(output_path, content)
            result = {"status": "rendered", "article_path": str(args.output.resolve()) if args.output else None, "article": content if not args.output else None}
        elif args.command == "draft":
            result = save_draft(root, _read_json(args.file))
        elif args.command == "archive":
            result = archive(root, _read_json(args.file), args.article.read_text(encoding="utf-8-sig") if args.article else None)
        elif args.command == "migrate":
            result = migrate(root)
        elif args.command == "due":
            result = due(root, args.now)
        elif args.command == "dispatch":
            result = dispatch(root, args.now)
        elif args.command == "recover":
            result = recover(root, ranking_type=args.ranking_type, period=args.period, apply=args.apply, quarantine=args.quarantine)
        elif args.command == "history":
            connection = _connect(root)
            try:
                result = {"status": "ok", "issues": [_history_summary(row) for row in _issue_rows(connection)] if connection else [],
                          "history_path": _write_indexes(root, connection) if connection else None}
            finally:
                if connection:
                    connection.close()
        else:
            result = status(root)
    except (DigestError, OSError, sqlite3.Error, UnicodeError, ValueError) as exc:
        print(_json({"status": "error", "error": str(exc)}))
        return 2
    print(_json(result))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
