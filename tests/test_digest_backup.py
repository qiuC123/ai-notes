from __future__ import annotations

import json
import io
import contextlib
import runpy
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ai_notes import digest_backup as backup


class DigestBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'project'
        self.root.mkdir()
        for name in ('digest', 'selection', 'runtime'):
            path = self.root / f'data/weekly_digest/{name}.sqlite3'
            path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE evidence (id INTEGER PRIMARY KEY, text TEXT)')
                db.execute('INSERT INTO evidence(text) VALUES (?)', ('retained ' + name,))
                db.commit()
        self.put('config/digest_sources.json', '{"sources":[]}')
        self.put('docs/prompts/digest-selection.md', 'Prompt')
        self.put('outputs/digest/daily/2026-10-02.md', 'Article')
        self.put('data/weekly_digest/source_runs/cache/abc/value.body', 'Raw original')
        self.put('data/weekly_digest/incoming/observed-batch.json', '{"original":"retained"}')
        self.put('data/weekly_digest/notice-state.json', '{"last_notice":"kept"}')

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def test_sqlite_backup_and_empty_target_restore(self):
        target = self.base / 'private-backup'
        result = backup.create_backup(self.root, target)
        self.assertEqual(3, result['database_count'])
        self.assertTrue(backup.verify_backup(target)['verified'])
        restored = self.base / 'restored'
        restored.mkdir()
        self.assertTrue(backup.restore_backup(target, restored)['verified'])
        for name in ('digest', 'selection', 'runtime'):
            with closing(sqlite3.connect(restored / f'data/weekly_digest/{name}.sqlite3')) as db:
                self.assertEqual('retained ' + name, db.execute('SELECT text FROM evidence').fetchone()[0])
        self.assertEqual('Article', (restored / 'outputs/digest/daily/2026-10-02.md').read_text())
        self.assertEqual({'original': 'retained'}, json.loads((restored / 'data/weekly_digest/incoming/observed-batch.json').read_text()))
        self.assertEqual({'last_notice': 'kept'}, json.loads((restored / 'data/weekly_digest/notice-state.json').read_text()))

    def test_credentials_and_other_project_data_are_excluded(self):
        self.put('.env', 'SECRET=do-not-export')
        self.put('config/private.json', 'secret')
        self.put('data/other.sqlite3', 'not digest')
        self.put('outputs/other/private.txt', 'other project')
        self.put('data/weekly_digest/source_runs/.env', 'token')
        result = backup.create_backup(self.root, self.base / 'backup')
        names = [f['path'] for f in json.loads((self.base / 'backup/manifest.json').read_text())['files']]
        self.assertFalse(any('.env' in name or 'private' in name or 'other' in name for name in names))
        self.assertTrue(result['verified'])

    def test_hash_tampering_blocks_restore_and_preserves_target(self):
        target = self.base / 'backup'
        backup.create_backup(self.root, target)
        (target / 'outputs/digest/daily/2026-10-02.md').write_text('tamper')
        restored = self.base / 'restored'
        with self.assertRaises(backup.BackupError):
            backup.restore_backup(target, restored)
        self.assertFalse(restored.exists())

    def test_existing_restore_target_and_inside_repo_backup_are_rejected(self):
        target = self.base / 'backup'
        backup.create_backup(self.root, target)
        restored = self.base / 'restored'
        restored.mkdir()
        (restored / 'keep.txt').write_text('keep')
        with self.assertRaises(backup.BackupError):
            backup.restore_backup(target, restored)
        self.assertEqual('keep', (restored / 'keep.txt').read_text())
        with self.assertRaises(backup.BackupError):
            backup.create_backup(self.root, self.root / 'backup')

    def test_manifest_path_traversal_and_unexpected_files_are_rejected(self):
        target = self.base / 'backup'
        backup.create_backup(self.root, target)
        manifest = json.loads((target / 'manifest.json').read_text())
        manifest['files'][0]['path'] = '../outside.txt'
        (target / 'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(backup.BackupError):
            backup.verify_backup(target)

    def test_active_collector_or_worker_prevents_backup(self):
        folder = self.root / 'data/weekly_digest/source_runs/runs/active'
        with backup.digest_sources._run_lock(folder):
            with self.assertRaises(backup.BackupError):
                backup.create_backup(self.root, self.base / 'backup')
        with closing(sqlite3.connect(self.root / 'data/weekly_digest/runtime.sqlite3')) as db:
            db.execute('CREATE TABLE jobs (status TEXT)')
            db.execute("INSERT INTO jobs VALUES ('running')")
            db.commit()
        with self.assertRaises(backup.BackupError):
            backup.create_backup(self.root, self.base / 'backup')

    def test_wal_commits_are_included_without_copying_sidecars(self):
        path = self.root / 'data/weekly_digest/digest.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('INSERT INTO evidence(text) VALUES (?)', ('uncheckpointed',))
            db.commit()
            self.assertTrue(Path(str(path) + '-wal').exists())
            target = self.base / 'wal-backup'
            backup.create_backup(self.root, target)
            self.assertTrue(backup.verify_backup(target)['verified'])
            with closing(sqlite3.connect(target / 'data/weekly_digest/digest.sqlite3')) as restored:
                self.assertEqual(2, restored.execute('SELECT COUNT(*) FROM evidence').fetchone()[0])
                self.assertEqual('delete', restored.execute('PRAGMA journal_mode').fetchone()[0])

    def test_extra_unmanifested_file_is_rejected(self):
        target = self.base / 'backup'
        backup.create_backup(self.root, target)
        (target / '.env').write_text('must-not-travel')
        with self.assertRaises(backup.BackupError):
            backup.verify_backup(target)

    def test_tick_with_empty_dispatch_never_runs_pending_work(self):
        program = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'deploy/digest/one_tick.py'))
        response = program['subprocess'].CompletedProcess([], 0, json.dumps({'dispatch': {'actions': []}, 'jobs': []}), '')
        with patch.object(program['subprocess'], 'run', return_value=response) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                code = program['main'](['--root', str(self.root), '--execute'])
        self.assertEqual(0, code)
        self.assertEqual(1, run.call_count)
        self.assertEqual('schedule', run.call_args.args[0][5])

    def test_tick_with_actions_can_drain_bounded_work(self):
        program = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'deploy/digest/one_tick.py'))
        values = [{'dispatch': {'actions': [{'action': 'collect'}]}, 'jobs': [{'job_id':'current-a'},{'job_id':'current-b'}]}, {'status': 'completed'}, {'status': 'idle'}]
        responses = [program['subprocess'].CompletedProcess([], 0, json.dumps(value), '') for value in values]
        with patch.object(program['subprocess'], 'run', side_effect=responses) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                code = program['main'](['--root', str(self.root), '--execute', '--max-jobs', '3'])
        self.assertEqual(0, code)
        self.assertEqual(['schedule', 'work', 'work'], [call.args[0][5] for call in run.call_args_list])
        self.assertEqual(['--id','current-a'],run.call_args_list[1].args[0][-2:])
        self.assertEqual(['--id','current-b'],run.call_args_list[2].args[0][-2:])

    def test_tick_forwards_private_config_path_without_reading_its_contents(self):
        program = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'deploy/digest/one_tick.py'))
        private_file = self.base / 'private model.env'
        private_file.write_text('DIGEST_MODEL_API_KEY=private-test-only', encoding='utf-8')
        values = [{'dispatch': {'actions': [{'action': 'collect'}]}, 'jobs': [{'job_id': 'current'}]}, {'status': 'completed'}]
        responses = [program['subprocess'].CompletedProcess([], 0, json.dumps(value), '') for value in values]
        stdout = io.StringIO()
        with patch.object(program['subprocess'], 'run', side_effect=responses) as run:
            with contextlib.redirect_stdout(stdout):
                code = program['main'](['--root', str(self.root), '--model-env-file', str(private_file), '--execute'])
        self.assertEqual(0, code)
        for call in run.call_args_list:
            self.assertEqual(['--model-env-file', str(private_file.resolve())], call.args[0][-2:])
            self.assertNotIn('private-test-only', str(call.args))
        self.assertNotIn('private-test-only', stdout.getvalue())


if __name__ == '__main__':
    unittest.main()
