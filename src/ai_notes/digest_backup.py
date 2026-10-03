"""Private, allowlisted digest backups with SQLite snapshots and hash checks.

Stop all digest writers first. Three database write locks hold a shared quiet
interval while SQLite's backup API exports committed data. Restore never
overwrites an existing nonempty destination.
"""
from __future__ import annotations

import argparse
from contextlib import closing, ExitStack
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import sys
import tempfile
import time

from . import digest
from . import digest_sources

DATABASES = tuple(Path(f'data/weekly_digest/{name}.sqlite3') for name in ('digest', 'selection', 'runtime'))
TREES = (Path('data/weekly_digest/source_runs'), Path('data/weekly_digest/incoming'), Path('outputs/digest'))
EXACT = {
    'data/weekly_digest/notice-state.json',
    'config/digest_sources.json', 'config/digest_selection.json',
    'docs/DIGEST_RANKING_DESIGN.md', 'docs/WEEKLY_PROJECT_DIGEST_WORKFLOW.md',
    'docs/WEEKLY_PROJECT_DIGEST.md', 'docs/WEEKLY_PROJECT_DIGEST_OUTPUT_DRAFT.md',
    'docs/WEEKLY_PROJECT_DIGEST_SOURCES.md', 'docs/DIGEST_SELECTION_POLICY.md',
}


class BackupError(ValueError):
    pass


def _hash(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as handle:
        while chunk := handle.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def _safe_name(name: str) -> bool:
    if not isinstance(name, str) or '\\' in name or ':' in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ('', '.', '..') for part in name.split('/')):
        return False
    if any(part.startswith('.') or part.lower().endswith(('.pem', '.key', '.log'))
           or part.lower() in ('credentials.json', 'secrets.json') for part in path.parts):
        return False
    return (name in EXACT or name in {p.as_posix() for p in DATABASES}
            or any(name.startswith(p.as_posix() + '/') for p in TREES)
            or (path.parent.as_posix() == 'docs/prompts' and path.name.startswith('digest-') and path.suffix == '.md'))


def _not_link(path: Path, root: Path) -> None:
    current = path
    while current != root:
        if current.is_symlink() or getattr(current, 'is_junction', lambda: False)():
            raise BackupError(f'linked path is not supported: {path.relative_to(root)}')
        current = current.parent
    if not path.resolve().is_relative_to(root.resolve()):
        raise BackupError('path leaves selected digest root')


def _files(root: Path) -> dict[str, Path]:
    result = {}
    for tree in TREES + (Path('docs/prompts'),):
        parent = root / tree
        if parent.exists():
            _not_link(parent, root)
            for path in parent.rglob('*'):
                _not_link(path, root)
                relative = path.relative_to(root).as_posix()
                if path.is_file() and _safe_name(relative):
                    result[relative] = path
    for name in EXACT:
        path = root / name
        if path.is_file():
            _not_link(path, root)
            result[name] = path
    return dict(sorted(result.items()))


def _empty_destination(destination: Path) -> None:
    if destination.is_symlink() or getattr(destination, 'is_junction', lambda: False)():
        raise BackupError('destination cannot be a link')
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise BackupError('destination must be absent or an empty directory; overwrite is refused')


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
        outgoing.flush()
        os.fsync(outgoing.fileno())


def _quiet(root: Path, connections: dict[Path, sqlite3.Connection]) -> None:
    source_runs = root / 'data/weekly_digest/source_runs'
    if source_runs.exists():
        for path in source_runs.rglob('.lock'):
            try:
                with digest_sources._run_lock(path.parent):
                    pass  # A file left by an exited process is not an active lock.
            except digest_sources.SourceError as exc:
                raise BackupError('collector is active; stop the writer before backup') from exc
    runtime = connections.get(Path('data/weekly_digest/runtime.sqlite3'))
    if runtime and runtime.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
        if runtime.execute("SELECT 1 FROM jobs WHERE status='running' LIMIT 1").fetchone():
            raise BackupError('runtime has running jobs; stop and finish/recover them before backup')


def _publish(stage: Path, destination: Path) -> None:
    _empty_destination(destination)
    if destination.exists():
        destination.rmdir()  # only the already-checked empty directory
    stage.rename(destination)


def create_backup(root: Path, destination: Path) -> dict:
    root = Path(root).resolve()
    destination = Path(destination).absolute()
    _empty_destination(destination)
    destination = destination.resolve()
    if destination.is_relative_to(root) or root.is_relative_to(destination):
        raise BackupError('private backup destination must be separate from the source project tree')
    if not root.is_dir():
        raise BackupError('source root does not exist')
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.digest-backup-', dir=destination.parent))
    started = time.monotonic()
    try:
        with ExitStack() as stack:
            locks = {}
            existing = [relative for relative in DATABASES if (root / relative).is_file()]
            for relative in existing:
                source = root / relative
                _not_link(source, root)
                connection = stack.enter_context(closing(sqlite3.connect(source, timeout=5)))
                connection.execute('BEGIN IMMEDIATE')
                locks[relative] = connection
            _quiet(root, locks)
            paths = _files(root)
            if not existing and not paths:
                raise BackupError('no allowlisted digest material exists')
            rows = []
            for relative in existing:
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect((root / relative).as_uri() + '?mode=ro', uri=True)) as incoming:
                    with closing(sqlite3.connect(target)) as outgoing:
                        def progress(status, remaining, total):
                            if time.monotonic() - started > 120:
                                raise BackupError('database backup exceeded 120 second budget')
                        incoming.backup(outgoing, pages=256, progress=progress)
                        # Export a standalone file even when the source uses
                        # WAL; the backup must not depend on absent sidecars.
                        outgoing.execute('PRAGMA journal_mode=DELETE')
                        if outgoing.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                            raise BackupError('backed up database failed integrity check')
                rows.append({'path': relative.as_posix(), 'sha256': _hash(target), 'bytes': target.stat().st_size, 'kind': 'sqlite'})
            source_hashes = {}
            for name, path in paths.items():
                target = stage / name
                _copy(path, target)
                source_hashes[name] = _hash(target)
                rows.append({'path': name, 'sha256': source_hashes[name], 'bytes': target.stat().st_size, 'kind': 'file'})
            _quiet(root, locks)
            current_paths = _files(root)
            if existing != [relative for relative in DATABASES if (root / relative).is_file()]:
                raise BackupError('database set changed during backup; stop all writers and retry')
            if paths.keys() != current_paths.keys() or any(_hash(path) != source_hashes[name] for name, path in current_paths.items()):
                raise BackupError('source files changed during backup; stop all writers and retry')
            manifest = {'schema_version': 'digest-backup.v1', 'project': 'ai-notes-digest',
                        'created_at': datetime.now(digest.BEIJING).isoformat(),
                        'database_count': len(existing), 'missing_databases': [x.as_posix() for x in DATABASES if x not in existing],
                        'consistency': 'SQLite backup API while all existing digest database write locks are held; non-database hashes rechecked',
                        'files': sorted(rows, key=lambda x: x['path'])}
            (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        verified = verify_backup(stage)
        _publish(stage, destination)
        return {**verified, 'backup_path': str(destination), 'database_count': len(existing)}
    except (OSError, sqlite3.Error) as exc:
        raise BackupError(str(exc)) from exc
    finally:
        if stage.exists():
            # stage was created here under the explicit backup parent; no
            # source, user destination or computed arbitrary tree is removed.
            if stage.parent.resolve() != destination.parent.resolve() or not stage.name.startswith('.digest-backup-'):
                raise BackupError('unexpected temporary backup location')
            shutil.rmtree(stage)


def verify_backup(backup: Path) -> dict:
    backup = Path(backup).resolve()
    manifest_path = backup / 'manifest.json'
    _not_link(manifest_path, backup)
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise BackupError('backup manifest cannot be read') from exc
    if manifest.get('schema_version') != 'digest-backup.v1' or manifest.get('project') != 'ai-notes-digest':
        raise BackupError('unsupported backup manifest')
    entries = manifest.get('files')
    if not isinstance(entries, list) or not entries:
        raise BackupError('backup manifest has no files')
    listed = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise BackupError('manifest file entry must be an object')
        name = entry.get('path')
        if not _safe_name(name) or name in listed:
            raise BackupError('manifest contains disallowed or duplicate paths')
        listed.add(name)
        path = backup / name
        _not_link(path, backup)
        if not path.is_file() or path.stat().st_size != entry.get('bytes') or _hash(path) != entry.get('sha256'):
            raise BackupError(f'backup hash/size mismatch: {name}')
        if name in {x.as_posix() for x in DATABASES}:
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise BackupError(f'database integrity check failed: {name}')
    actual = {p.relative_to(backup).as_posix() for p in backup.rglob('*') if p.is_file()}
    if actual != listed | {'manifest.json'}:
        raise BackupError('backup contains unlisted files')
    present = {x.as_posix() for x in DATABASES} & listed
    if manifest.get('database_count') != len(present) or set(manifest.get('missing_databases', [])) != {x.as_posix() for x in DATABASES} - present:
        raise BackupError('manifest database inventory does not match files')
    digest._timestamp(manifest.get('created_at'), 'backup.created_at')
    return {'verified': True, 'file_count': len(entries), 'manifest_sha256': _hash(manifest_path), 'created_at': manifest['created_at']}


def restore_backup(backup: Path, destination: Path) -> dict:
    backup = Path(backup).resolve()
    destination = Path(destination).absolute()
    _empty_destination(destination)
    destination = destination.resolve()
    if destination.is_relative_to(backup) or backup.is_relative_to(destination):
        raise BackupError('restore target must be separate from the backup tree')
    verified = verify_backup(backup)
    manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.digest-restore-', dir=destination.parent))
    try:
        for entry in manifest['files']:
            source, target = backup / entry['path'], stage / entry['path']
            _copy(source, target)
            if _hash(target) != entry['sha256']:
                raise BackupError('backup changed during restore')
        _publish(stage, destination)
        return {**verified, 'restored_to': str(destination), 'database_count': manifest['database_count']}
    finally:
        if stage.exists():
            if stage.parent.resolve() != destination.parent.resolve() or not stage.name.startswith('.digest-restore-'):
                raise BackupError('unexpected temporary restore location')
            shutil.rmtree(stage)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create')
    create.add_argument('--root', type=Path, default=Path('.'))
    create.add_argument('--destination', type=Path, required=True)
    verify = sub.add_parser('verify')
    verify.add_argument('--backup', type=Path, required=True)
    restore = sub.add_parser('restore')
    restore.add_argument('--backup', type=Path, required=True)
    restore.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = create_backup(args.root, args.destination) if args.command == 'create' else verify_backup(args.backup) if args.command == 'verify' else restore_backup(args.backup, args.destination)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
