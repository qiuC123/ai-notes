"""Bounded public-source discovery and immutable response evidence for digests.

Discovery never becomes verification automatically. Cached bytes retain their
original fetch time, including on HTTP 304 and saved-run replay.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import closing, contextmanager
import hashlib
import html
import ipaddress
import json
import os
import re
import socket
import sqlite3
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from itertools import zip_longest
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import getproxies, proxy_bypass

import httpx

from . import digest

SOURCE_DIR = Path('data/weekly_digest/source_runs')
DEFAULT_CONFIG = Path('config/digest_sources.json')
MAX_BYTES = 3 * 1024 * 1024
TYPES = {'show_hn', 'rss', 'hf_models', 'hf_spaces'}


class SourceError(ValueError):
    """Source or persisted evidence could not safely be consumed."""


def _now() -> datetime:
    return datetime.now(digest.BEIJING)


def _stamp() -> str:
    return _now().isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '-')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _write(path: Path, value: Any) -> None:
    _atomic(path, _json(value).encode('utf-8'))


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _public_url(url: str, *, resolve: bool) -> str:
    digest._url(url)
    host = urlsplit(url).hostname or ''
    if host.lower() == 'localhost' or host.lower().endswith(('.localhost', '.local', '.internal')):
        raise SourceError('only public HTTP(S) hosts are allowed')
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = []
        if resolve:
            addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]
    if any(not address.is_global for address in addresses):
        raise SourceError('non-public network address is not allowed')
    return url


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(15.0, connect=8.0), follow_redirects=False,
                        headers={'User-Agent': 'ai-notes-digest/0.3 (+https://github.com/qiuC123/ai-notes)'})


def _proxy_resolves(url: str) -> bool:
    parsed = urlsplit(url)
    proxies = getproxies()
    return bool((proxies.get(parsed.scheme) or proxies.get('all')) and not proxy_bypass(parsed.hostname or ''))


def fetch(root: Path, url: str, *, client: httpx.Client | None = None,
          max_bytes: int = MAX_BYTES) -> dict:
    """Fetch a public original or discovery feed, retaining bounded raw bytes.

    ``fetched_at`` is the time bytes were fetched, not the current validation
    time. ``checked_at`` is the actual request time. Neither is ``verified_at``.
    A supplied httpx client is a trusted injection point for offline tests.
    """
    root = Path(root).resolve()
    if not 1 <= max_bytes <= MAX_BYTES:
        raise SourceError(f'max_bytes must be between 1 and {MAX_BYTES}')
    requested_at = _stamp()
    cache_dir = root / SOURCE_DIR / 'cache' / _sha(url.encode('utf-8'))
    meta_path = cache_dir / 'latest.json'
    previous = _read(meta_path) if meta_path.exists() else None
    own_client = client is None
    session = client or _client()
    headers = {}
    if previous:
        if previous.get('etag'):
            headers['If-None-Match'] = previous['etag']
        if previous.get('last_modified'):
            headers['If-Modified-Since'] = previous['last_modified']
    attempt_path = root / SOURCE_DIR / 'fetches' / (_now().strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:12] + '.json')
    attempt = {'schema_version': 'digest-fetch.v1', 'url': url, 'checked_at': requested_at}
    deadline = time.monotonic() + 30
    try:
        current = url
        for hop in range(6):
            if time.monotonic() > deadline:
                raise SourceError('fetch exceeded 30 second request budget')
            # HTTP forward proxies resolve remote domains themselves. Local DNS
            # can be unrelated (e.g. a poisoned AAAA beside a usable proxy).
            # Literal and localhost addresses are rejected in either route.
            _public_url(current, resolve=own_client and not _proxy_resolves(current))
            with session.stream('GET', current, headers=headers, follow_redirects=False, timeout=15.0) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    if hop == 5 or 'location' not in response.headers:
                        raise SourceError('invalid or excessive redirects')
                    current = urljoin(current, response.headers['location'])
                    # Conditional headers belong to the original cached resource.
                    if not previous or current != previous.get('final_url'):
                        headers = {}
                    continue
                if response.status_code == 304:
                    if not previous:
                        raise SourceError('304 response without saved evidence')
                    body_path = root / previous['body_path']
                    if not body_path.is_file() or _sha(body_path.read_bytes()) != previous['sha256']:
                        raise SourceError('cached evidence missing or hash mismatch')
                    if previous['byte_count'] > max_bytes:
                        raise SourceError('cached response exceeds byte limit')
                    result = {**previous, 'checked_at': requested_at, 'cache_status': 'revalidated', 'http_status': 304}
                else:
                    response.raise_for_status()
                    if int(response.headers.get('content-length', '0')) > max_bytes:
                        raise SourceError('response exceeds byte limit')
                    chunks, count = [], 0
                    for chunk in response.iter_bytes():
                        if time.monotonic() > deadline:
                            raise SourceError('fetch exceeded 30 second request budget')
                        count += len(chunk)
                        if count > max_bytes:
                            raise SourceError('response exceeds byte limit')
                        chunks.append(chunk)
                    raw = b''.join(chunks)
                    digest_hash = _sha(raw)
                    body_path = cache_dir / (digest_hash + '.body')
                    if not body_path.exists():
                        _atomic(body_path, raw)
                    result = {**attempt, 'fetched_at': _stamp(), 'final_url': str(response.url),
                              'body_path': _relative(root, body_path), 'sha256': digest_hash,
                              'byte_count': len(raw), 'content_type': response.headers.get('content-type', ''),
                              'etag': response.headers.get('etag'), 'last_modified': response.headers.get('last-modified'),
                              'cache_status': 'fetched', 'http_status': response.status_code}
                result['receipt_path'] = _relative(root, attempt_path)
                _write(attempt_path, {**result, 'status': 'ok'})
                _write(meta_path, result)
                return result
        raise SourceError('excessive redirects')
    except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
        _write(attempt_path, {**attempt, 'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'})
        raise SourceError(f'{type(exc).__name__}: {exc}; receipt={_relative(root, attempt_path)}') from exc
    finally:
        if own_client:
            session.close()


def _date(value: Any) -> datetime | None:
    if not value or not isinstance(value, (str, int, float)):
        return None
    try:
        if isinstance(value, (int, float)):
            result = datetime.fromtimestamp(value, digest.BEIJING)
        else:
            try:
                result = datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError:
                result = parsedate_to_datetime(value)
        return result if result.utcoffset() is not None else None
    except (ValueError, OverflowError, TypeError):
        return None


def read_github(root: Path, url: str, *, ref: str | None = None,
                client: httpx.Client | None = None) -> dict:
    """Read the original README pinned to the repository's resolved commit.

    The API response and decoded README are both retained. No license or
    release claim is inferred from the README and this is not verification.
    """
    root = Path(root).resolve()
    identity = digest.canonical_url(url)
    if urlsplit(identity).hostname != 'github.com':
        raise SourceError('github reader requires a GitHub repository URL')
    owner_repo = urlsplit(identity).path.strip('/')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', owner_repo):
        raise SourceError('invalid GitHub repository identity')
    evidence = []
    def get(endpoint: str) -> tuple[dict, dict]:
        record = fetch(root, endpoint, client=client)
        evidence.append(record)
        return json.loads(_payload(root, record)), record
    if ref is None:
        repository, _ = get('https://api.github.com/repos/' + owner_repo)
        ref = repository['default_branch']
    commit, _ = get('https://api.github.com/repos/' + owner_repo + '/commits/' + quote(ref, safe=''))
    commit_sha = commit['sha']
    if not isinstance(commit_sha, str) or not re.fullmatch(r'[0-9a-f]{40}', commit_sha):
        raise SourceError('GitHub commit response has no valid SHA')
    readme, response = get('https://api.github.com/repos/' + owner_repo + '/readme?ref=' + commit_sha)
    if readme.get('encoding') != 'base64' or not isinstance(readme.get('content'), str):
        raise SourceError('GitHub README content is unavailable')
    raw = base64.b64decode(''.join(readme['content'].split()), validate=True)
    if len(raw) > MAX_BYTES:
        raise SourceError('README exceeds byte limit')
    path = root / SOURCE_DIR / 'documents' / (_sha(raw) + '.md')
    if not path.exists():
        _atomic(path, raw)
    return {**response, 'reader': 'github-readme', 'repository_url': identity,
            'original_url': identity + '/blob/' + commit_sha + '/' + quote(readme['path'], safe='/'),
            'commit_sha': commit_sha, 'document_path': _relative(root, path),
            'document_sha256': _sha(raw), 'evidence_status': 'discovered', 'fetches': evidence}


def _clean(value: Any, limit: int = 1000) -> str:
    return ' '.join(html.unescape(re.sub('<[^>]+>', ' ', str(value or ''))).split())[:limit]


def load_config(root: Path, path: Path | None = None) -> dict:
    config = _read(path or (root / DEFAULT_CONFIG))
    if config.get('schema_version') != 'digest-sources.v1':
        raise SourceError('unsupported source configuration')
    items = config.get('sources')
    if not isinstance(items, list) or not 1 <= len(items) <= 16:
        raise SourceError('configuration requires 1 to 16 sources')
    seen = set()
    for item in items:
        source_id = item.get('id', '')
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', source_id) or source_id in seen:
            raise SourceError('source IDs must be unique lowercase kebab names')
        seen.add(source_id)
        if item.get('type') not in TYPES:
            raise SourceError('unsupported source type')
        _public_url(item.get('url', ''), resolve=False)
        if 'hellogithub' in item['url'].lower() or 'hellogithub' in source_id:
            raise SourceError('HelloGitHub is editorial reference only, not a source')
        weekdays = item.get('weekdays', list(range(7)))
        if not isinstance(weekdays, list) or not weekdays or any(type(x) is not int or not 0 <= x <= 6 for x in weekdays):
            raise SourceError('weekdays must contain Monday=0 through Sunday=6')
        if type(item.get('max_items', 30)) is not int or not 1 <= item.get('max_items', 30) <= 80:
            raise SourceError('source max_items must be 1 to 80')
        if item.get('category', '博客、帖子与访谈') not in digest.CATEGORIES:
            raise SourceError('source category must be one of the eight digest columns')
        if item.get('kind', 'reading') not in ('project', 'reading'):
            raise SourceError('discovery may not label an update important automatically')
    return config


def _payload(root: Path, result: dict) -> bytes:
    raw = (root / result['body_path']).read_bytes()
    if _sha(raw) != result['sha256']:
        raise SourceError('saved evidence hash mismatch')
    return raw


def _source_items(root: Path, source: dict, client: httpx.Client | None,
                  fetches: list[dict], errors: list[str]) -> tuple[list[dict], int]:
    def get(url: str) -> tuple[bytes, dict]:
        result = fetch(root, url, client=client)
        fetches.append(result)
        return _payload(root, result), result
    raw, response = get(source['url'])
    maximum = source.get('max_items', 30)
    items = []
    if source['type'] == 'show_hn':
        ids = json.loads(raw)
        if not isinstance(ids, list):
            raise SourceError('Show HN index is not an array')
        for item_id in ids[:maximum]:
            try:
                if type(item_id) is not int:
                    raise SourceError('invalid HN item ID')
                original = f'https://hacker-news.firebaseio.com/v0/item/{item_id}.json'
                content, item_response = get(original)
                entry = json.loads(content)
                if not entry or entry.get('deleted') or entry.get('dead') or not entry.get('url'):
                    continue
                url = entry['url']
                items.append({'url': url, 'title': _clean(entry.get('title')), 'summary': _clean(entry.get('text')),
                              'source_url': f'https://news.ycombinator.com/item?id={item_id}',
                              'observed_publication': _date(entry.get('time')), 'published_at': None,
                              'kind': 'project', 'category': '开源项目' if urlsplit(url).hostname == 'github.com' else 'AI 应用',
                              'response': item_response})
            except (ValueError, KeyError, TypeError) as exc:
                errors.append(f'item {item_id}: {exc}')
        return items, len(ids)
    if source['type'] in ('hf_models', 'hf_spaces'):
        entries = json.loads(raw)
        if not isinstance(entries, list):
            raise SourceError('Hugging Face index is not an array')
        for entry in entries[:maximum]:
            identifier = entry.get('id') or entry.get('modelId')
            if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', identifier):
                continue
            model = source['type'] == 'hf_models'
            items.append({'url': 'https://huggingface.co/' + ('' if model else 'spaces/') + identifier,
                          'title': identifier, 'summary': _clean(entry.get('description')) or f'Hugging Face {"模型" if model else "Space"}发现线索；具体用途、运行条件和许可待原文核验。',
                          'source_url': source['url'], 'observed_publication': _date(entry.get('lastModified')),
                          'published_at': None, 'kind': 'project', 'category': '模型与运行工具' if model else 'AI 应用',
                          'response': response})
        return items, len(entries)
    tree = ET.fromstring(raw)
    entries = [element for element in tree.iter() if element.tag.rsplit('}', 1)[-1] in ('entry', 'item')]
    for entry in entries[:maximum]:
        fields = {}
        link = ''
        for child in entry:
            tag = child.tag.rsplit('}', 1)[-1]
            fields.setdefault(tag, ''.join(child.itertext()).strip())
            if tag == 'link' and child.attrib.get('rel', 'alternate') == 'alternate':
                link = child.attrib.get('href') or fields[tag]
        if not link:
            guid = fields.get('guid', '')
            # Opaque RSS GUIDs are identity strings, not relative article URLs.
            link = guid if urlsplit(guid).scheme in ('http', 'https') else ''
        if not link:
            continue
        published = _date(fields.get('published') or fields.get('pubDate') or fields.get('date'))
        observed = published or _date(fields.get('updated'))
        kind = source.get('kind', 'reading')
        items.append({'url': urljoin(response['final_url'], link), 'title': _clean(fields.get('title')),
                      'summary': _clean(fields.get('summary') or fields.get('description') or fields.get('content') or fields.get('encoded')),
                      'source_url': source['url'], 'observed_publication': observed,
                      'published_at': published.isoformat() if published and kind == 'reading' else None,
                      'kind': kind, 'category': source.get('category', '博客、帖子与访谈'), 'response': response})
    return items, len(entries)


def _known(root: Path) -> dict[str, str]:
    path = root / digest.DB_PATH
    if not path.exists():
        return {}
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
        connection.execute('PRAGMA query_only=ON')
        result = {}
        rows = connection.execute('SELECT canonical_url,o.payload,b.collected_at FROM observations o JOIN batches b ON b.run_id=o.run_id')
        for identity, payload, collected_at in rows:
            discovered = json.loads(payload).get('discovered_at') or collected_at
            if identity not in result or digest._timestamp(discovered, 'discovered_at') < digest._timestamp(result[identity], 'discovered_at'):
                result[identity] = discovered
        return result


def _confirm_ingest(root: Path, batch: dict) -> dict:
    result = digest.ingest(root, batch)
    with closing(sqlite3.connect((root / digest.DB_PATH).as_uri() + '?mode=ro', uri=True)) as connection:
        row = connection.execute('SELECT payload FROM batches WHERE run_id=?', (batch['run_id'],)).fetchone()
        count = connection.execute('SELECT COUNT(*) FROM observations WHERE run_id=?', (batch['run_id'],)).fetchone()[0]
    result['read_back_confirmed'] = bool(row and json.loads(row[0]) == batch and count == len(batch['candidates']))
    if not result['read_back_confirmed']:
        raise SourceError('ingest read-back mismatch')
    return result


@contextmanager
def _run_lock(folder: Path):
    """Nonblocking OS lock; process exit releases it without deleting files.

    Keeping the file avoids the unlink/open-inode race between two workers.
    A stale file is harmless; only a live OS lock means the run is active.
    """
    folder.mkdir(parents=True, exist_ok=True)
    handle = (folder / '.lock').open('a+b')
    locked = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise SourceError('run is already active (OS process lock held)') from exc
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def collect(root: Path, *, source_ids: list[str] | None = None, limit: int = 20,
            run_id: str | None = None, config_path: Path | None = None,
            client: httpx.Client | None = None) -> dict:
    """Persist a bounded batch and receipts, ingest it, then read it back.

    The same run_id replays saved evidence without new requests. Already known
    identities remain source observations and do not consume the new-item cap.
    """
    root = Path(root).resolve()
    if type(limit) is not int or not 1 <= limit <= 20:
        raise SourceError('limit must be 1 to 20')
    run_id = run_id or ('collect-' + _now().strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}', run_id):
        raise SourceError('invalid run_id')
    folder = root / SOURCE_DIR / 'runs' / run_id
    folder.mkdir(parents=True, exist_ok=True)
    batch_path, receipt_path = folder / 'batch.json', folder / 'receipt.json'
    with _run_lock(folder):
        if batch_path.exists():
            batch = _read(batch_path)
            receipt = _read(receipt_path) if receipt_path.exists() else {
                'schema_version': 'digest-source-run.v1', 'run_id': run_id, 'status': 'recovered',
                'collected_at': batch['collected_at'], 'batch_path': _relative(root, batch_path),
                'receipt_path': _relative(root, receipt_path), 'source_results': batch['sources']}
            receipt['ingest'] = _confirm_ingest(root, batch)
            _write(receipt_path, receipt)
            return receipt
        config = load_config(root, config_path)
        requested = set(source_ids or [])
        available = {s['id'] for s in config['sources']}
        if requested - available:
            raise SourceError('unknown requested source: ' + ', '.join(sorted(requested - available)))
        start = _now()
        selected = [s for s in config['sources'] if s.get('enabled', True) and
                    (s['id'] in requested if requested else start.weekday() in s.get('weekdays', list(range(7))))]
        if not selected:
            raise SourceError('no configured source is due or enabled')
        known = _known(root)
        candidates: dict[str, dict] = {}
        new_count, known_count = 0, 0
        results, queues = [], []
        for source in selected:
            fetched, errors, eligible = [], [], []
            state_path = root / SOURCE_DIR / 'source-state' / (source['id'] + '.json')
            previous_state = _read(state_path) if state_path.exists() else {}
            previous_attempt = _date(previous_state.get('completed_at'))
            fallback = (previous_state.get('status') == 'failed' and previous_attempt is not None
                        and start - timedelta(days=7) <= previous_attempt <= start)
            lookback = 168 if fallback else 48
            result = {'name': source['id'], 'url': source['url'], 'status': 'empty', 'detail': '',
                      'planned': True, 'started_at': _stamp(), 'lookback_hours': lookback,
                      'fallback_after_failure': fallback,
                      'window_start': (start - timedelta(hours=lookback)).isoformat(), 'window_end': start.isoformat(),
                      'read_items': 0, 'available_items': 0, 'accepted_observations': 0,
                      'future_items': 0, 'outside_window': 0, 'unknown_date_items': 0,
                      'budget_excluded': 0, 'fetches': fetched, 'errors': errors}
            try:
                entries, total = _source_items(root, source, client, fetched, errors)
                result['available_items'], result['read_items'] = total, len(entries)
                observed_dates = [x['observed_publication'] for x in entries if x['observed_publication']]
                result['observed_date_min'] = min(observed_dates).isoformat() if observed_dates else None
                result['observed_date_max'] = max(observed_dates).isoformat() if observed_dates else None
                result['index_truncated'] = total > source.get('max_items', 30)
                for entry in entries:
                    try:
                        observed = entry['observed_publication']
                        if observed and observed > _now():
                            result['future_items'] += 1
                            continue
                        if observed and observed < start - timedelta(hours=lookback):
                            result['outside_window'] += 1
                            continue
                        if observed is None:
                            result['unknown_date_items'] += 1
                        identity = digest.canonical_url(entry['url'], entry['kind'])
                        _public_url(identity, resolve=False)
                        eligible.append((identity, entry, result))
                    except (ValueError, KeyError, TypeError) as exc:
                        errors.append(str(exc))
                result['status'] = 'failed' if errors else ('ok' if entries else 'empty')
                result['detail'] = f'读取 {len(entries)}/{total} 条，保存 {result["accepted_observations"]} 个来源观察；仅发现，不代表核验或窗口全覆盖。'
            except (ValueError, KeyError, TypeError, ET.ParseError) as exc:
                errors.append(f'{type(exc).__name__}: {exc}')
                result['status'], result['detail'] = 'failed', str(exc)
            result['completed_at'] = _stamp()
            _write(state_path, {'source_id': source['id'], 'run_id': run_id,
                                'status': result['status'], 'completed_at': result['completed_at']})
            results.append(result)
            queues.append(eligible)
        # Round-robin discovery sources so an always-on HN feed cannot consume
        # every new-item slot before the day's rotating source is considered.
        for row in zip_longest(*queues):
            for packed in row:
                if packed is None:
                    continue
                identity, entry, result = packed
                if identity in candidates:
                    candidates[identity]['source_urls'] = sorted(set(candidates[identity]['source_urls'] + [entry['source_url']]))
                    continue
                is_known = identity in known
                if not is_known and new_count >= limit:
                    result['budget_excluded'] += 1
                    continue
                title = entry['title'] or identity
                fetched_at = entry['response']['fetched_at']
                observed = entry['observed_publication']
                candidates[identity] = {
                    'url': identity, 'title': title, 'category': entry['category'],
                    'summary': entry['summary'] or title,
                    'reason': f'来源发现「{title}」这一具体作品或文章；保存供复核用途、许可、分类与实用价值，尚未通过价值筛选。',
                    'source_urls': [entry['source_url']], 'published_at': entry['published_at'],
                    'kind': entry['kind'], 'evidence_status': 'discovered', 'evidence_urls': [], 'change_note': '',
                    'discovered_at': known.get(identity, _stamp()), 'verified_at': None, 'verification_level': 'documented',
                    'source_observation': {'fetched_at': fetched_at, 'checked_at': entry['response']['checked_at'],
                                           'sha256': entry['response']['sha256'], 'body_path': entry['response']['body_path'],
                                           'source_item_published_at': observed.isoformat() if observed else None,
                                           'category_is_provisional': True}}
                result['accepted_observations'] += 1
                if is_known:
                    known_count += 1
                else:
                    new_count += 1
        for result in results:
            if result['status'] != 'failed':
                result['detail'] = f'读取 {result["read_items"]}/{result["available_items"]} 条，保存 {result["accepted_observations"]} 个来源观察；仅发现，不代表核验或窗口全覆盖。'
        collected_at = _stamp()
        batch = {'schema_version': 'digest-batch.v2', 'run_id': run_id, 'collected_at': collected_at,
                 'sources': results, 'candidates': list(candidates.values())}
        digest._validate_batch(batch)
        failed = sum(s['status'] == 'failed' for s in results)
        status = 'failed' if failed == len(results) else 'partial' if failed else 'ok' if candidates else 'empty'
        receipt = {'schema_version': 'digest-source-run.v1', 'run_id': run_id, 'status': status,
                   'started_at': start.isoformat(), 'collected_at': collected_at,
                   'batch_path': _relative(root, batch_path), 'receipt_path': _relative(root, receipt_path),
                   'new_candidates': new_count, 'known_observations': known_count, 'new_limit': limit,
                   'source_results': results, 'source_config_sha256': _sha(_json(config).encode('utf-8'))}
        _write(batch_path, batch)
        _write(receipt_path, receipt)
        try:
            receipt['ingest'] = _confirm_ingest(root, batch)
        except (ValueError, OSError, sqlite3.Error) as exc:
            receipt['ingest'] = {'status': 'failed', 'error': str(exc), 'read_back_confirmed': False}
            _write(receipt_path, receipt)
            raise
        _write(receipt_path, receipt)
        return receipt


def _fixture_client(path: Path) -> httpx.Client:
    fixtures = _read(path)
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) not in fixtures:
            raise httpx.ConnectError('URL missing from offline fixture', request=request)
        value = fixtures[str(request.url)]
        if value.get('error'):
            raise httpx.ReadTimeout(value['error'], request=request)
        options = {'json': value['json']} if 'json' in value else {'text': value.get('body', '')}
        return httpx.Response(value.get('status_code', 200), headers=value.get('headers', {}), **options)
    return httpx.Client(transport=httpx.MockTransport(handler))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    collect_cli = commands.add_parser('collect')
    collect_cli.add_argument('--source', action='append')
    collect_cli.add_argument('--limit', type=int, default=20)
    collect_cli.add_argument('--run-id')
    collect_cli.add_argument('--config', type=Path)
    fetch_cli = commands.add_parser('fetch')
    fetch_cli.add_argument('--url', required=True)
    github_cli = commands.add_parser('github')
    github_cli.add_argument('--url', required=True)
    github_cli.add_argument('--ref')
    for command in (collect_cli, fetch_cli, github_cli):
        command.add_argument('--root', type=Path, default=Path('.'))
        command.add_argument('--fixture', type=Path)
    args = parser.parse_args(argv)
    session = None
    try:
        session = _fixture_client(args.fixture) if args.fixture else None
        if args.command == 'collect':
            result = collect(args.root, source_ids=args.source, limit=args.limit, run_id=args.run_id,
                             config_path=args.config, client=session)
        elif args.command == 'github':
            result = read_github(args.root, args.url, ref=args.ref, client=session)
        else:
            result = fetch(args.root, args.url, client=session)
        print(_json(result), end='')
        return 1 if result.get('status') == 'failed' else 0
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        print(_json({'status': 'failed', 'error': str(exc)}), file=sys.stderr, end='')
        return 1
    finally:
        if session:
            session.close()


if __name__ == '__main__':
    raise SystemExit(main())
