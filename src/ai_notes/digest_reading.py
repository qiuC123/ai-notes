"""Question-driven, bounded original repository reading for digest reviews.

This module retrieves documents, not candidate code. All documents share the
README's resolved commit, and optional Gitingest only formats already fetched
original files in an isolated local directory. Its output is never evidence.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
from importlib import metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import sys
from typing import Mapping
from urllib.parse import quote, unquote, urlsplit

import httpx

from . import digest, digest_sources as sources

MAX_DOCUMENTS = 6
MAX_SUPPLEMENTAL_REQUESTS = 6
MAX_DOCUMENT_BYTES = 512 * 1024
MAX_CONTEXT_CHARS = 100_000
_QUESTIONS = {
    'overview': 'What does the project do and who can use it?',
    'license': 'What does the original license text permit and require?',
    'installation': 'Which installation paths and requirements are documented?',
    'usage': 'How does a reader start using the project?',
}


def _safe_path(value: str) -> str:
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or '\x00' in value or value.startswith('/')):
        raise sources.SourceError('invalid repository document path')
    parts = value.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise sources.SourceError('invalid repository document path')
    return PurePosixPath(value).as_posix()


def _explicit_ref(url: str) -> str | None:
    """Keep explicit candidate versions; never replace them with latest."""
    parts = urlsplit(url).path.strip('/').split('/')
    if len(parts) > 4 and parts[2:4] == ['releases', 'tag']:
        return unquote('/'.join(parts[4:]))
    if len(parts) > 3 and parts[2] == 'commit':
        return unquote(parts[3])
    if len(parts) > 3 and parts[2] in ('blob', 'tree'):
        # A full commit is unambiguous even when the file path follows it.
        if re.fullmatch(r'[0-9a-fA-F]{40}', parts[3]):
            return parts[3].lower()
        # Branch names with slashes cannot be distinguished from document paths
        # without another API lookup. Do not silently choose a different ref.
        if len(parts) == 4:
            return unquote(parts[3])
        raise sources.SourceError('repository document URL requires an unambiguous commit SHA')
    if len(parts) > 2:
        raise sources.SourceError('repository URL does not identify a supported pinned ref')
    return None


def _failed_receipt(root: Path, url: str, exc: Exception) -> dict:
    message = str(exc)
    result = {'url': url, 'status': 'failed', 'error': message}
    if '; receipt=' in message:
        path = message.rsplit('; receipt=', 1)[1]
        result['receipt_path'] = path
        try:
            result.update(json.loads((root / path).read_text(encoding='utf-8')))
        except (OSError, ValueError):
            pass
    return result


def _pinned_fetch(root: Path, url: str, *, client: httpx.Client | None,
                  max_bytes: int = sources.MAX_BYTES) -> dict:
    """One supplemental HTTP request; redirects are a recorded failure.

    The shared fetcher normally follows public redirects. A pinned original
    must not acquire another URL, and each supplemental request has one slot.
    """
    own = client is None
    session = client or sources._client()

    class PinnedClient:
        @contextmanager
        def stream(self, *args, **kwargs):
            if own:
                sources._public_url(url, resolve=not sources._proxy_resolves(url))
            with session.stream(*args, **kwargs) as response:
                if 300 <= response.status_code < 400 and response.status_code != 304:
                    response.raise_for_status()
                yield response

    try:
        return sources.fetch(root, url, client=PinnedClient(), max_bytes=max_bytes)
    finally:
        if own:
            session.close()


def _kind(path: str) -> str | None:
    name = PurePosixPath(path).name.lower()
    if re.fullmatch(r'(?:licen[sc]e|copying)(?:[._-][a-z0-9_-]+)*', name):
        if PurePosixPath(name).suffix in ('', '.txt', '.md', '.rst', '.adoc') or '.' not in name:
            return 'license'
    suffix = PurePosixPath(name).suffix
    if suffix not in ('.md', '.mdx', '.rst', '.txt', '.adoc'):
        return None
    stem = name[:-len(suffix)]
    key = re.sub(r'[_ ]+', '-', stem)
    if key in ('install', 'installation', 'setup', 'getting-started', 'get-started'):
        return 'installation'
    if key in ('usage', 'guide', 'user-guide', 'tutorial', 'tutorials', 'examples', 'quickstart', 'quick-start'):
        return 'usage'
    if key in ('readme', 'index') and path.lower().split('/')[0] in ('docs', 'doc', 'documentation', 'examples'):
        return 'usage'
    return None


def _select(tree: dict, readme_path: str, maximum: int) -> tuple[list[dict], int]:
    if not isinstance(tree, dict):
        raise sources.SourceError('GitHub tree response is not an object')
    rows = tree.get('tree')
    if not isinstance(rows, list) or type(tree.get('truncated')) is not bool:
        raise sources.SourceError('GitHub tree response has no valid document inventory')
    choices = []
    for row in rows:
        if not isinstance(row, dict) or row.get('type') != 'blob' or row.get('mode') not in ('100644', '100755'):
            continue
        try:
            path = _safe_path(row.get('path'))
        except sources.SourceError:
            continue
        if path == readme_path or len(PurePosixPath(path).parts) > 5:
            continue
        purpose = _kind(path)
        if purpose:
            choices.append({'file_path': path, 'question': purpose})
    def preference(row):
        file = PurePosixPath(row['file_path'])
        generic = file.stem.lower() in ('index', 'readme')
        # Prefer a concrete use guide over a shallower documentation index.
        primary_license = file.name.lower() in ('license', 'license.md', 'license.txt')
        return (generic, len(file.parts), not primary_license, row['file_path'].lower(), row['file_path'])
    choices.sort(key=preference)
    # Answer separate questions before reading another guide about one topic.
    selected = []
    for purpose in ('license', 'installation', 'usage'):
        first = next((row for row in choices if row['question'] == purpose), None)
        if first and len(selected) < maximum:
            selected.append(first)
    for row in choices:
        if row not in selected and len(selected) < maximum:
            selected.append(row)
    return selected, len(choices)


def package_selected_documents(documents: Mapping[str, bytes]) -> dict:
    """Run optional Gitingest against only these immutable original bytes.

    The helper accepts a document map, never a URL. The temporary folder
    contains these files only, without Git metadata.
    Raw contexts and their receipts remain the evidence authority.
    """
    checked = {_safe_path(path): raw for path, raw in documents.items()}
    if any('.git' in PurePosixPath(path).parts for path in checked):
        raise sources.SourceError('Git metadata is not a selected original document')
    if any(not isinstance(raw, bytes) or len(raw) > MAX_DOCUMENT_BYTES for raw in checked.values()):
        raise sources.SourceError('local packaging requires bounded original byte documents')
    if len(checked) > MAX_DOCUMENTS:
        raise sources.SourceError('too many selected documents for local packaging')
    manifest = [{'file_path': path, 'document_sha256': hashlib.sha256(raw).hexdigest(),
                 'byte_count': len(raw)} for path, raw in checked.items()]
    result = {'tool': 'gitingest', 'local_only': True, 'evidence_role': 'packaging_only',
              'documents': manifest, 'version': None}
    if importlib.util.find_spec('gitingest') is None:
        return {**result, 'status': 'unavailable', 'available': False,
                'error': 'optional digest-reading extra is not installed'}
    try:
        result['version'] = metadata.version('gitingest')
    except metadata.PackageNotFoundError:
        pass
    try:
        with tempfile.TemporaryDirectory(prefix='ai-notes-selected-') as folder:
            local = Path(folder)
            for path, raw in checked.items():
                target = local / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            # Gitingest 0.3.1 configures global logging at import. A child keeps
            # that side effect and its asyncio loop outside the caller process.
            script = (
                'import json,sys\nfrom gitingest import ingest\n'
                'summary,tree,content=ingest(sys.argv[1],max_file_size=int(sys.argv[2]),'
                'include_patterns=set(json.loads(sys.argv[3])),include_gitignored=True,'
                'include_submodules=False,output=None)\n'
                'print(json.dumps([summary,tree,content],ensure_ascii=False))\n')
            environment = {**os.environ, 'LOG_LEVEL': 'ERROR', 'LOG_FORMAT': 'human'}
            completed = subprocess.run(
                [sys.executable, '-X', 'utf8', '-c', script, str(local), str(MAX_DOCUMENT_BYTES),
                 json.dumps(list(checked))], env=environment, capture_output=True,
                encoding='utf-8', timeout=30, check=True)
            summary, tree, content = json.loads(completed.stdout)
            if any(not isinstance(value, str) for value in (summary, tree, content)):
                raise ValueError('Gitingest did not return string summary, tree, and content')
            return {**result, 'status': 'ok', 'available': True, 'summary': summary,
                    'tree': tree, 'content': content, 'content_chars': len(content),
                    'content_sha256': hashlib.sha256(content.encode('utf-8')).hexdigest()}
    except Exception as exc:  # Optional formatting must not discard valid originals.
        return {**result, 'status': 'failed', 'available': True,
                'error': f'{type(exc).__name__}: {exc}'}


def _contexts(documents: list[dict], maximum: int, commit: str) -> list[dict]:
    # Reserve a share for every fetched document, then spend unused shares on
    # the earlier questions. A long README cannot consume the license's share.
    share = maximum // max(1, len(documents))
    counts = [min(len(row['decoded']), share) for row in documents]
    remaining = maximum - sum(counts)
    for index, row in enumerate(documents):
        addition = min(remaining, len(row['decoded']) - counts[index])
        counts[index] += addition
        remaining -= addition
    result = []
    for row, supplied in zip(documents, counts):
        full = row['decoded']
        row['supplied_chars'] = supplied
        row['coverage'] = 'complete_text' if supplied == len(full) else 'excerpt'
        if not supplied:
            continue
        result.append({'url': row['original_url'], 'text': full[:supplied],
                       'fetched_at': row['fetched_at'], 'source_scope': {
            'schema_version': 'digest-source-scope.v1', 'coverage': row['coverage'],
            'document_chars': len(full), 'supplied_chars': supplied,
            'ranges': [{'start': 0, 'end': supplied}],
            'document_sha256': row['document_sha256'], 'reader': row['reader'],
            'commit_sha': commit, 'file_path': row['file_path']}})
    return result


def read_project(root: Path, url: str, *, client: httpx.Client | None = None,
                 max_documents: int = 4, max_chars: int = 14_000) -> dict:
    """Read README plus bounded original license/install/use documents.

    ``max_documents`` includes the README. Tree plus supplemental document
    requests never exceeds six; no release listing, clones, code execution,
    recursive link following, or repository eligibility gate is involved.
    Returned source ranges index decoded originals before any excerpting.
    """
    root = Path(root).resolve()
    if type(max_documents) is not int or not 1 <= max_documents <= MAX_DOCUMENTS:
        raise sources.SourceError(f'max_documents must be 1 to {MAX_DOCUMENTS}')
    if type(max_chars) is not int or not 1 <= max_chars <= MAX_CONTEXT_CHARS:
        raise sources.SourceError(f'max_chars must be 1 to {MAX_CONTEXT_CHARS}')
    identity = digest.canonical_url(url)
    if urlsplit(identity).hostname != 'github.com':
        raise sources.SourceError('project reader requires a GitHub repository URL')
    ref = _explicit_ref(url)
    owner_repo = urlsplit(identity).path.strip('/')
    result = {'schema_version': 'digest-project-reading.v1', 'repository_url': identity,
              'requested_url': url, 'commit_sha': None, 'contexts': [], 'receipts': [],
              'documents': [], 'failures': [], 'reading_plan': {
                  'questions': _QUESTIONS.copy(), 'selected_documents': [], 'tree_status': 'not_read',
                  'budget': {'max_documents': max_documents, 'max_chars': max_chars,
                             'max_supplemental_requests': MAX_SUPPLEMENTAL_REQUESTS}},
              'coverage': {'max_documents': max_documents, 'max_chars': max_chars,
                           'max_supplemental_requests': MAX_SUPPLEMENTAL_REQUESTS,
                           'supplemental_requests': 0, 'tree_truncated': None}}
    result['reading_plan']['failures'] = result['failures']
    rows, document_bytes = [], {}
    try:
        readme = sources.read_github(root, identity, ref=ref, client=client)
        result['receipts'].extend({**record, 'status': 'ok'} for record in readme['fetches'])
        commit = result['commit_sha'] = readme['commit_sha']
        path = _safe_path(unquote(urlsplit(readme['original_url']).path.split('/blob/' + commit + '/', 1)[1]))
        raw = (root / readme['document_path']).read_bytes()
        if len(raw) > MAX_DOCUMENT_BYTES or hashlib.sha256(raw).hexdigest() != readme['document_sha256']:
            raise sources.SourceError('README is oversized or its saved original hash differs')
        decoded = raw.decode('utf-8')
        row = {'file_path': path, 'question': 'overview', 'status': 'read',
               'original_url': readme['original_url'], 'reader': readme['reader'],
               'fetched_at': readme['fetched_at'],
               'document_path': readme['document_path'], 'document_sha256': readme['document_sha256'],
               'document_chars': len(decoded), 'decoded': decoded}
        rows.append(row)
        result['documents'].append(row)
        document_bytes[path] = raw
        result['reading_plan']['selected_documents'].append({'file_path': path, 'question': 'overview'})
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        result['failures'].append({'url': url, 'question': 'overview', 'error': str(exc)})
        result['receipts'].append(_failed_receipt(root, url, exc))
        result['status'] = 'failed'
        result['coverage'].update({'read_documents': 0, 'supplied_chars': 0,
                                   'questions': {key: 'unknown' for key in _QUESTIONS}})
        result['gitingest'] = {'tool': 'gitingest', 'status': 'not_run', 'reason': 'no original documents'}
        return result
    tree_url = f'https://api.github.com/repos/{owner_repo}/git/trees/{commit}?recursive=1'
    selected = []
    result['coverage']['supplemental_requests'] += 1
    try:
        receipt = _pinned_fetch(root, tree_url, client=client)
        result['receipts'].append({**receipt, 'status': 'ok'})
        if receipt['final_url'] != tree_url:
            raise sources.SourceError('pinned inventory redirected to a different URL')
        tree = json.loads(sources._payload(root, receipt))
        selected, available = _select(tree, path, max_documents - 1)
        result['reading_plan']['tree_status'] = 'read'
        result['coverage'].update({'tree_truncated': tree['truncated'], 'matching_documents': available,
                                   'document_budget_excluded': available - len(selected)})
    except (ValueError, OSError, KeyError, TypeError) as exc:
        result['reading_plan']['tree_status'] = 'failed'
        result['failures'].append({'url': tree_url, 'question': 'document_inventory', 'error': str(exc)})
        result['receipts'].append(_failed_receipt(root, tree_url, exc))
    result['reading_plan']['selected_documents'].extend(selected)
    for selection in selected:
        path = selection['file_path']
        original = identity + '/blob/' + commit + '/' + quote(path, safe='/')
        raw_url = 'https://raw.githubusercontent.com/' + owner_repo + '/' + commit + '/' + quote(path, safe='/')
        row = {**selection, 'original_url': original, 'reader': 'github-original-file'}
        result['documents'].append(row)
        result['coverage']['supplemental_requests'] += 1
        try:
            receipt = _pinned_fetch(root, raw_url, client=client, max_bytes=MAX_DOCUMENT_BYTES)
            result['receipts'].append({**receipt, 'status': 'ok'})
            if receipt['final_url'] != raw_url:
                raise sources.SourceError('pinned original redirected to a different document URL')
            raw = sources._payload(root, receipt)
            decoded = raw.decode('utf-8')
            document_path = root / sources.SOURCE_DIR / 'documents' / (sources._sha(raw) + '.original')
            if not document_path.exists():
                sources._atomic(document_path, raw)
            row.update({'status': 'read', 'document_path': sources._relative(root, document_path),
                        'fetched_at': receipt['fetched_at'],
                        'document_sha256': sources._sha(raw), 'document_chars': len(decoded),
                        'decoded': decoded})
            rows.append(row)
            document_bytes[path] = raw
        except (ValueError, OSError, KeyError, TypeError) as exc:
            row.update({'status': 'failed', 'coverage': 'unknown', 'error': str(exc)})
            result['failures'].append({'url': raw_url, 'file_path': path,
                                       'question': selection['question'], 'error': str(exc)})
            result['receipts'].append(_failed_receipt(root, raw_url, exc))
    result['contexts'] = _contexts(rows, max_chars, commit)
    questions = {}
    for purpose in _QUESTIONS:
        relevant = [row for row in result['documents'] if row['question'] == purpose]
        supplied = [row for row in relevant if row.get('supplied_chars', 0)]
        questions[purpose] = ('read' if supplied else 'unknown')
    result['coverage'].update({'read_documents': len(rows), 'context_documents': len(result['contexts']),
                               'failed_documents': sum(row['status'] == 'failed' for row in result['documents']),
                               'supplied_chars': sum(len(context['text']) for context in result['contexts']),
                               'questions': questions})
    result['reading_plan']['budget'].update({
        'selected_documents': len(result['reading_plan']['selected_documents']),
        'supplied_chars': result['coverage']['supplied_chars'],
        'supplemental_requests': result['coverage']['supplemental_requests']})
    result['status'] = 'partial' if result['failures'] else 'ok'
    packaged = package_selected_documents(document_bytes)
    if packaged['status'] == 'ok':
        artifact = root / sources.SOURCE_DIR / 'documents' / (packaged['content_sha256'] + '.gitingest.txt')
        if not artifact.exists():
            sources._atomic(artifact, packaged['content'].encode('utf-8'))
        packaged['artifact_path'] = sources._relative(root, artifact)
    result['gitingest'] = {key: value for key, value in packaged.items()
                           if key not in ('content', 'summary', 'tree')}
    for row in result['documents']:
        row.pop('decoded', None)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--url', required=True)
    parser.add_argument('--max-documents', type=int, default=4)
    parser.add_argument('--max-chars', type=int, default=14_000)
    args = parser.parse_args(argv)
    try:
        packet = read_project(args.root, args.url, max_documents=args.max_documents, max_chars=args.max_chars)
        summary = {**packet, 'contexts': [{'url': row['url'], 'fetched_at': row['fetched_at'],
                                          'source_scope': row['source_scope']}
                                          for row in packet['contexts']]}
        print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if packet['status'] == 'failed' else 0
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
