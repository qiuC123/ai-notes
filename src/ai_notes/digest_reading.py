"""Question-driven, bounded original repository reading for digest reviews.

This module retrieves documents, not candidate code. All documents share the
README's resolved commit, and optional Gitingest only formats already fetched
original files in an isolated local directory. Its output is never evidence.
"""
from __future__ import annotations

import argparse
import codecs
from contextlib import contextmanager
from email.message import Message
import hashlib
import importlib.util
from importlib import metadata
from html.parser import HTMLParser
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
DISCOVERY_READING_CONTRACT = 'discovery-reading.v1'
HTML_CHARSET_SCAN_BYTES = 8192
_HTML_MEDIA_TYPES = {'text/html', 'application/xhtml+xml'}
_HTML_RAW_MEDIA_TYPES = _HTML_MEDIA_TYPES | {'', 'text/plain', 'application/octet-stream'}
_HTML_ENCODINGS = {'utf-8', 'ascii', 'utf-16-le', 'utf-16-be', 'utf-32-le', 'utf-32-be',
                   'cp1250', 'cp1251', 'cp1252', 'cp1253', 'cp1254', 'cp1255', 'cp1256',
                   'cp1257', 'cp1258', 'iso8859-1', 'iso8859-2', 'iso8859-5', 'iso8859-7',
                   'iso8859-9', 'iso8859-15', 'gb18030', 'gbk', 'gb2312', 'big5',
                   'shift_jis', 'cp932', 'euc_jp', 'euc_kr', 'cp949', 'koi8-r', 'koi8-u'}
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


def _discovery_route(text: str) -> str:
    """Small routing signals, never a project eligibility or quality verdict."""
    if re.search(r'AppWizard has created|MICROSOFT FOUNDATION CLASS LIBRARY', text, re.I):
        return 'generated_application_template'
    if len(text) <= 2000 and re.search(r'source[_ ]code|\bdevelopment\b|\bbuilding\b', text, re.I):
        return 'short_source_development_entry'
    return 'repository_overview'


def _discovery_kind(path: str) -> str | None:
    existing = _kind(path)
    if existing:
        return existing
    file = PurePosixPath(path)
    if file.suffix.lower() not in ('.md', '.mdx', '.rst', '.txt', '.adoc', '.html', '.htm'):
        return None
    # Static, repository-owned user material only. No source execution or
    # guessed filenames, and no arbitrary README links become fetch targets.
    if not any(part.lower() in ('docs', 'doc', 'documentation', 'help', 'website') for part in file.parts[:-1]):
        return None
    name = re.sub(r'[_ -]+', '', file.stem.lower())
    if any(name.endswith(ending) for ending in ('gettingstarted', 'quickstart', 'userguide', 'usage', 'faq')):
        return 'usage'
    if name in ('overview', 'features', 'index', '_index'):
        return 'overview'
    return None


def _select_discovery(tree: dict, readme_path: str, maximum: int) -> tuple[list[dict], int]:
    # Reuse the inventory validation of the legacy path, without its policy.
    _select(tree, readme_path, 0)
    choices = []
    for row in tree['tree']:
        if not isinstance(row, dict) or row.get('type') != 'blob' or row.get('mode') not in ('100644', '100755'):
            continue
        try:
            path = _safe_path(row.get('path'))
        except sources.SourceError:
            continue
        if path == readme_path or len(PurePosixPath(path).parts) > 5:
            continue
        purpose = _discovery_kind(path)
        if purpose:
            choices.append({'file_path': path, 'question': purpose})
    def preference(row):
        file = PurePosixPath(row['file_path'])
        # A root licence answers project terms before auxiliary submodule
        # licences. User material outranks build instructions and extra terms.
        priority = (0 if row['question'] == 'license' and len(file.parts) == 1 else
                    1 if row['question'] in ('usage', 'overview') else
                    2 if row['question'] == 'installation' else 3)
        generic = file.stem.lower() in ('index', '_index', 'readme')
        return (priority, generic, len(file.parts), row['file_path'].lower(), row['file_path'])
    choices.sort(key=preference)
    return choices[:maximum], len(choices)


def _metadata_ranges(text: str) -> list[dict]:
    """Exact top-level JSON field slices; no generated prose is evidence."""
    decoder = json.JSONDecoder()
    position = 0
    def skip_space():
        nonlocal position
        while position < len(text) and text[position].isspace():
            position += 1
    skip_space()
    if text[position:position + 1] != '{':
        raise sources.SourceError('GitHub repository metadata is not an object')
    position += 1
    ranges = []
    while True:
        skip_space()
        if text[position:position + 1] == '}':
            break
        start = position
        key, position = decoder.raw_decode(text, position)
        skip_space()
        if text[position:position + 1] != ':':
            raise sources.SourceError('invalid GitHub metadata field')
        position += 1
        skip_space()
        value, position = decoder.raw_decode(text, position)
        if key in ('name', 'full_name', 'description', 'homepage') and isinstance(value, str) and value.strip():
            ranges.append({'start': start, 'end': position})
        skip_space()
        if text[position:position + 1] == '}':
            break
        if text[position:position + 1] != ',':
            raise sources.SourceError('invalid GitHub metadata separator')
        position += 1
    return ranges


class _ProductText(HTMLParser):
    """Capture static page text; scripts and styles are never executed."""
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'template'):
            self.hidden += 1
        if self.hidden:
            return
        if re.fullmatch('h[1-6]', tag):
            self.parts.append('\n' + '#' * int(tag[1]) + ' ')
        elif tag in ('p', 'div', 'section', 'article', 'li', 'br', 'hr', 'tr'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'template'):
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'div', 'li', 'section', 'article', 'tr'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    def text(self):
        text = re.sub(r'[^\S\n]+', ' ', ''.join(self.parts))
        return re.sub(r'\n(?: *\n){2,}', '\n\n', text).strip()


class _HTMLCharsets(HTMLParser):
    """Read ASCII charset declarations only in a bounded HTML header sample."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.labels = []
        self.finished = False

    def handle_starttag(self, tag, attrs):
        if tag == 'body':
            self.finished = True
        if tag != 'meta' or self.finished:
            return
        attrs = dict(attrs)
        if attrs.get('charset'):
            self.labels.append(attrs['charset'])
        elif str(attrs.get('http-equiv', '')).lower() == 'content-type':
            label = _header_charset(attrs.get('content', ''))
            if label:
                self.labels.append(label)

    def handle_endtag(self, tag):
        if tag == 'head':
            self.finished = True


def _header_charset(content_type: str) -> str | None:
    message = Message()
    message['content-type'] = content_type
    return message.get_content_charset()


def _html_encoding(label: str) -> str:
    if len(label) > 64 or not re.fullmatch(r'[A-Za-z0-9._-]+', label):
        raise sources.SourceError('invalid declared HTML charset')
    try:
        encoding = codecs.lookup(label).name
    except LookupError as exc:
        raise sources.SourceError('unsupported declared HTML charset: ' + label) from exc
    if encoding not in _HTML_ENCODINGS:
        raise sources.SourceError('unsupported declared HTML charset: ' + label)
    return encoding


def _html_prefix(text: str) -> bool:
    return bool(re.match(r'\s*(?:<\?xml\b[^>]*>\s*)?(?:<!doctype\s+html\b|<!--|'
                         r'<(?:html|head|body|meta|title|h[1-6]|p|div|section|article|'
                         r'li|ul|ol|a|table|script|style|template)\b)', text, re.I))


def _strict_text(raw: bytes, encoding: str) -> str:
    if raw.startswith((b'%PDF-', b'\x89PNG', b'GIF87a', b'GIF89a', b'PK\x03\x04', b'\xff\xd8\xff', b'\x7fELF', b'MZ')):
        raise sources.SourceError('binary document is not supported source text')
    try:
        text = raw.decode(encoding, errors='strict')
    except UnicodeError as exc:
        raise sources.SourceError('strict ' + encoding + ' decoding failed: ' + str(exc)) from exc
    if re.search(r'[\x00-\x08\x0b\x0e-\x1f\x7f-\x9f]', text):
        raise sources.SourceError('decoded document contains non-text control characters')
    return text


def _page_text(raw: bytes, content_type: str = '', *, decoding: dict | None = None) -> str:
    """Extract bounded HTML with explicit charset provenance and strict decoding.

    BOM takes priority over HTTP charset and then a header meta declaration.
    Without a declaration only strict UTF-8 is accepted; no legacy guess or
    replacement decoding can turn binary/unknown bytes into source evidence.
    """
    if not isinstance(raw, bytes) or len(raw) > MAX_DOCUMENT_BYTES:
        raise sources.SourceError('HTML exceeds original document byte budget')
    media_type = content_type.partition(';')[0].strip().lower()
    if media_type not in _HTML_RAW_MEDIA_TYPES:
        raise sources.SourceError('unsupported HTML media type: ' + media_type)
    encoding, origin, label, payload = 'utf-8', 'utf-8-default', None, raw
    for bom, codec in ((codecs.BOM_UTF32_LE, 'utf-32-le'), (codecs.BOM_UTF32_BE, 'utf-32-be'),
                       (codecs.BOM_UTF8, 'utf-8'), (codecs.BOM_UTF16_LE, 'utf-16-le'),
                       (codecs.BOM_UTF16_BE, 'utf-16-be')):
        if raw.startswith(bom):
            encoding, origin, payload = codec, 'bom', raw[len(bom):]
            break
    if origin != 'bom':
        label = _header_charset(content_type)
        if label:
            encoding, origin = _html_encoding(label), 'http-header'
        else:
            declarations = _HTMLCharsets()
            # Latin-1 preserves every sampled byte while exposing only ASCII
            # markup; it is never used to decode the supplied document text.
            declarations.feed(raw[:HTML_CHARSET_SCAN_BYTES].decode('latin-1'))
            if declarations.labels:
                encodings = {_html_encoding(value) for value in declarations.labels}
                if len(encodings) != 1:
                    raise sources.SourceError('conflicting HTML meta charset declarations')
                label = declarations.labels[0]
                encoding, origin = encodings.pop(), 'html-meta'
    if decoding is not None:
        decoding.update(encoding=encoding, encoding_source=origin, declared_charset=label,
                        decoder='python-codecs-strict', text_extractor='html.parser.HTMLParser')
    text = _strict_text(payload, encoding)
    if not _html_prefix(text):
        raise sources.SourceError('document does not start with recognizable HTML markup')
    parser = _ProductText()
    parser.feed(text)
    return parser.text()


def _discovery_contexts(documents: list[dict], maximum: int, commit: str) -> list[dict]:
    def full_text(row):
        spans = row.get('selected_ranges')
        return ('\n\n'.join(row['decoded'][span['start']:span['end']] for span in spans)
                if spans is not None else row['decoded'])
    contents = [full_text(row) for row in documents]
    share = maximum // max(1, len(documents))
    counts = [min(len(text), share) for text in contents]
    remaining = maximum - sum(counts)
    for index, text in enumerate(contents):
        addition = min(remaining, len(text) - counts[index])
        counts[index] += addition
        remaining -= addition
    contexts = []
    for row, content, count in zip(documents, contents, counts):
        spans = row.get('selected_ranges')
        ranges = []
        if spans is None and count:
            ranges = [{'start': 0, 'end': count}]
        elif spans:
            left = count
            for index, span in enumerate(spans):
                if index:
                    left -= 2  # Exact separator used in full_text above.
                taken = min(max(0, left), span['end'] - span['start'])
                if taken:
                    ranges.append({'start': span['start'], 'end': span['start'] + taken})
                left -= taken
                if left <= 0:
                    break
        row['supplied_chars'] = count
        row['coverage'] = ('complete_text' if spans is None and count == len(row['decoded']) else 'excerpt')
        if count:
            contexts.append(dict(url=row['original_url'], text=content[:count], fetched_at=row['fetched_at'],
                source_scope=dict(schema_version='digest-source-scope.v1', coverage=row['coverage'],
                    document_chars=len(row['decoded']), supplied_chars=count, ranges=ranges,
                    document_sha256=row['document_sha256'], reader=row['reader'],
                    commit_sha=row.get('commit_sha', commit), file_path=row['file_path'])))
    return contexts


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
                 max_documents: int = 4, max_chars: int = 14_000,
                 reading_contract: str | None = None) -> dict:
    """Read README plus bounded original license/install/use documents.

    ``max_documents`` includes the README. Tree plus supplemental document
    requests never exceeds six; no release listing, clones, code execution,
    recursive link following, or repository eligibility gate is involved.
    Returned source ranges index decoded originals before any excerpting.
    Explicit ``discovery-reading.v1`` additionally supplies repository metadata
    and routes thin development/template entries toward user material. Current
    metadata and homepage text never enter a pinned event's source packet.
    """
    root = Path(root).resolve()
    if type(max_documents) is not int or not 1 <= max_documents <= MAX_DOCUMENTS:
        raise sources.SourceError(f'max_documents must be 1 to {MAX_DOCUMENTS}')
    if type(max_chars) is not int or not 1 <= max_chars <= MAX_CONTEXT_CHARS:
        raise sources.SourceError(f'max_chars must be 1 to {MAX_CONTEXT_CHARS}')
    if reading_contract not in (None, DISCOVERY_READING_CONTRACT):
        raise sources.SourceError('unsupported project reading contract')
    discovery = reading_contract == DISCOVERY_READING_CONTRACT
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
    if discovery:
        result['reading_contract'] = reading_contract
        result['reading_plan']['routing'] = {'overview_reason': None,
            'metadata': {'status': 'not_read', 'reason': 'pending_original_readme'},
            'homepage': {'status': 'not_read', 'reason': 'pending_metadata',
                         'association': 'repository_declared_homepage', 'max_pages': 1}}
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
    homepage = None
    reserve_homepage = 0
    if discovery:
        routing = result['reading_plan']['routing']
        routing['overview_reason'] = _discovery_route(decoded)
        if ref is not None:
            routing['metadata'] = {'status': 'skipped', 'reason': 'pinned_ref_excludes_current_metadata'}
            routing['homepage'].update(status='skipped', reason='pinned_ref_excludes_current_homepage')
        elif max_documents == 1:
            routing['metadata'] = {'status': 'skipped', 'reason': 'document_budget_exhausted'}
            routing['homepage'].update(status='skipped', reason='document_budget_exhausted')
        else:
            metadata_url = f'https://api.github.com/repos/{owner_repo}'
            try:
                receipt = next(record for record in readme['fetches'] if record['url'] == metadata_url)
                if receipt['final_url'] != metadata_url:
                    raise sources.SourceError('repository metadata redirected to a different URL')
                raw_metadata = sources._payload(root, receipt)
                if len(raw_metadata) > MAX_DOCUMENT_BYTES:
                    raise sources.SourceError('repository metadata exceeds original document byte budget')
                metadata_text = raw_metadata.decode('utf-8')
                repository = json.loads(metadata_text)
                full_name = repository.get('full_name', owner_repo) if isinstance(repository, dict) else None
                if not isinstance(full_name, str) or full_name.lower() != owner_repo.lower():
                    raise sources.SourceError('repository metadata identity differs')
                ranges = _metadata_ranges(metadata_text)
                if ranges:
                    metadata_row = dict(file_path=None, question='metadata', status='read',
                        original_url=metadata_url, reader='github-repository-metadata', commit_sha=None,
                        fetched_at=receipt['fetched_at'], document_path=receipt['body_path'],
                        document_sha256=receipt['sha256'], document_chars=len(metadata_text),
                        decoded=metadata_text, selected_ranges=ranges)
                    rows.append(metadata_row)
                    result['documents'].append(metadata_row)
                    result['reading_plan']['selected_documents'].append(
                        {'file_path': None, 'question': 'metadata', 'url': metadata_url})
                    routing['metadata'] = {'status': 'read', 'reason': 'exact_repository_json_fields',
                        'url': metadata_url, 'field_ranges': ranges, 'commit_sha': None}
                else:
                    routing['metadata'] = {'status': 'empty', 'reason': 'no_nonempty_discovery_fields',
                        'url': metadata_url}
                declared_homepage = repository.get('homepage')
                if not isinstance(declared_homepage, str) or not declared_homepage.strip():
                    routing['homepage'].update(status='skipped', reason='no_repository_declared_homepage')
                elif routing['overview_reason'] == 'repository_overview':
                    routing['homepage'].update(status='skipped', reason='repository_overview_retained',
                                               url=declared_homepage)
                else:
                    sources._public_url(declared_homepage, resolve=False)
                    if len(rows) >= max_documents:
                        routing['homepage'].update(status='skipped', reason='document_budget_exhausted',
                                                   url=declared_homepage)
                    else:
                        homepage = declared_homepage
                        reserve_homepage = 1
                        routing['homepage'].update(status='planned', reason=routing['overview_reason'],
                                                   url=homepage, metadata_url=metadata_url)
            except (ValueError, OSError, KeyError, TypeError, IndexError, StopIteration) as exc:
                metadata_read = routing['metadata']['status'] == 'read'
                if not metadata_read:
                    routing['metadata'].update(status='failed', reason=str(exc))
                routing['homepage'].update(status='invalid' if metadata_read else 'skipped',
                    reason=str(exc) if metadata_read else 'metadata_or_declared_url_invalid')
                result['failures'].append({'url': declared_homepage if metadata_read else metadata_url,
                    'question': 'homepage' if metadata_read else 'metadata', 'error': str(exc)})
    tree_url = f'https://api.github.com/repos/{owner_repo}/git/trees/{commit}?recursive=1'
    selected = []
    result['coverage']['supplemental_requests'] += 1
    try:
        receipt = _pinned_fetch(root, tree_url, client=client)
        result['receipts'].append({**receipt, 'status': 'ok'})
        if receipt['final_url'] != tree_url:
            raise sources.SourceError('pinned inventory redirected to a different URL')
        tree = json.loads(sources._payload(root, receipt))
        if discovery:
            # A root licence remains ahead of the reserved product page. The
            # reservation only reduces extra user/build/submodule documents.
            selected, available = _select_discovery(tree, path, max(0, max_documents - len(rows) - reserve_homepage))
            if homepage and not any(row['question'] == 'license' for row in selected):
                root_license, _ = _select_discovery(tree, path, 1)
                if root_license and root_license[0]['question'] == 'license' and not selected:
                    selected, reserve_homepage = root_license, 0
                    homepage = None
                    routing['homepage'].update(status='skipped', reason='root_license_uses_remaining_document_slot')
        else:
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
            original_raw = raw
            if discovery and PurePosixPath(path).suffix.lower() in ('.html', '.htm'):
                row.update(raw_document_sha256=receipt['sha256'], raw_document_path=receipt['body_path'],
                           decoding={})
                decoded = _page_text(raw, receipt.get('content_type', ''), decoding=row['decoding'])
                if not decoded.strip():
                    raise sources.SourceError('static user document contains no readable text')
                raw = decoded.encode('utf-8')
                row.update(reader='github-static-user-text')
            else:
                decoded = raw.decode('utf-8')
            document_path = root / sources.SOURCE_DIR / 'documents' / (sources._sha(raw) + '.original')
            if not document_path.exists():
                sources._atomic(document_path, raw)
            row.update({'status': 'read', 'document_path': sources._relative(root, document_path),
                        'fetched_at': receipt['fetched_at'],
                        'document_sha256': sources._sha(raw), 'document_chars': len(decoded),
                        'decoded': decoded})
            rows.append(row)
            document_bytes[path] = original_raw
        except (ValueError, OSError, KeyError, TypeError) as exc:
            row.update({'status': 'failed', 'coverage': 'unknown', 'error': str(exc)})
            result['failures'].append({'url': raw_url, 'file_path': path,
                                       'question': selection['question'], 'error': str(exc)})
            result['receipts'].append(_failed_receipt(root, raw_url, exc))
    if discovery and homepage:
        route = result['reading_plan']['routing']['homepage']
        if result['coverage']['supplemental_requests'] >= MAX_SUPPLEMENTAL_REQUESTS:
            route.update(status='skipped', reason='supplemental_request_budget_exhausted')
        else:
            homepage_row = dict(file_path=None, question='overview', original_url=homepage,
                                reader='repository-homepage-text', commit_sha=None)
            result['documents'].append(homepage_row)
            result['reading_plan']['selected_documents'].append(
                {'file_path': None, 'question': 'overview', 'url': homepage})
            result['coverage']['supplemental_requests'] += 1
            try:
                receipt = _pinned_fetch(root, homepage, client=client, max_bytes=MAX_DOCUMENT_BYTES)
                result['receipts'].append({**receipt, 'status': 'ok'})
                if receipt['final_url'] != homepage:
                    raise sources.SourceError('declared homepage redirected to a different URL')
                raw = sources._payload(root, receipt)
                content_type = receipt.get('content_type', '')
                media_type = content_type.partition(';')[0].strip().lower()
                homepage_row.update(raw_document_path=receipt['body_path'],
                                    raw_document_sha256=receipt['sha256'], decoding={})
                is_html = (media_type in _HTML_MEDIA_TYPES or
                           _html_prefix(raw.removeprefix(codecs.BOM_UTF8)[:HTML_CHARSET_SCAN_BYTES].decode('latin-1')))
                if is_html:
                    decoded = _page_text(raw, content_type, decoding=homepage_row['decoding'])
                else:
                    if media_type not in ('', 'text/plain'):
                        raise sources.SourceError('declared homepage is not supported HTML or plain text')
                    decoded = _strict_text(raw, 'utf-8')
                    homepage_row['decoding'].update(encoding='utf-8', encoding_source='utf-8-default',
                                                   declared_charset=None, decoder='python-codecs-strict')
                if not decoded.strip():
                    route.update(status='empty', reason='declared_homepage_contains_no_readable_text')
                    raise sources.SourceError('declared homepage contains no readable text')
                captured = decoded.encode('utf-8')
                document_path = root / sources.SOURCE_DIR / 'documents' / (sources._sha(captured) + '.original')
                if not document_path.exists():
                    sources._atomic(document_path, captured)
                homepage_row.update(status='read', fetched_at=receipt['fetched_at'], decoded=decoded,
                    document_path=sources._relative(root, document_path), document_sha256=sources._sha(captured),
                    document_chars=len(decoded), raw_document_path=receipt['body_path'],
                    raw_document_sha256=receipt['sha256'],
                    reader='repository-homepage-html-text' if is_html else 'repository-homepage-decoded-text')
                rows.append(homepage_row)
                route.update(status='read', reason='one_repository_declared_product_entry',
                             captured_text_chars=len(decoded), commit_sha=None)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                status = 'empty' if route['status'] == 'empty' else 'failed'
                homepage_row.update(status=status, coverage='unknown', error=str(exc))
                route.update(status=status, reason=str(exc), coverage='unknown')
                result['failures'].append({'url': homepage, 'question': 'overview', 'error': str(exc)})
                result['receipts'].append(_failed_receipt(root, homepage, exc))
    result['contexts'] = (_discovery_contexts(rows, max_chars, commit) if discovery else
                          _contexts(rows, max_chars, commit))
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
        if discovery:
            row.pop('selected_ranges', None)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--url', required=True)
    parser.add_argument('--max-documents', type=int, default=4)
    parser.add_argument('--max-chars', type=int, default=14_000)
    parser.add_argument('--reading-contract', choices=[DISCOVERY_READING_CONTRACT])
    args = parser.parse_args(argv)
    try:
        options = {'max_documents': args.max_documents, 'max_chars': args.max_chars}
        if args.reading_contract is not None:
            options['reading_contract'] = args.reading_contract
        packet = read_project(args.root, args.url, **options)
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
