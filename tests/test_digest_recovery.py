"""Candidate recall and interrupted export regressions use isolated ledgers only."""
from __future__ import annotations

import copy
import contextlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from test_digest import archive, batch, candidate, issue, item, update_record
from ai_notes import digest


class DigestRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        clock = patch.object(digest, '_now', return_value=datetime.fromisoformat('2026-11-10T12:00:00+08:00'))
        clock.start()
        self.addCleanup(clock.stop)

    def test_blocked_candidates_do_not_consume_limit_and_pages_reach_tail(self):
        first = candidate('https://github.com/example/a')
        remaining = [candidate('https://github.com/example/' + name) for name in ('y', 'z')]
        digest.ingest(self.root, batch(items=[first, *remaining]))
        archive(self.root, issue(items=[item(first)]))
        result = digest.candidates(self.root, ranking_type='daily', period='2026-09-07', limit=1)
        self.assertEqual(remaining[0]['url'], result['candidates'][0]['canonical_url'])
        self.assertEqual((3, 2, 1), (result['total_candidates'], result['available_count'], result['blocked_count']))
        tail = digest.candidates(self.root, ranking_type='daily', period='2026-09-07', limit=1,
                                 offset=result['next_offset'], include_history=False)
        self.assertEqual(remaining[1]['url'], tail['candidates'][0]['canonical_url'])
        self.assertEqual([], tail['candidates'][0]['history'])
        self.assertIsNone(tail['next_offset'])

    def test_reportable_v3_survives_later_reread_of_reported_v2(self):
        v2 = update_record()
        digest.ingest(self.root, batch('v2', '2026-09-09T20:00:00+08:00', [v2]))
        archive(self.root, issue(period='2026-09-09', prepared_at='2026-09-10T09:00:00+08:00', items=[item(v2, '2026-09-09T20:00:00+08:00')]))
        v3 = update_record('v3', '2026-09-10T10:00:00+08:00')
        digest.ingest(self.root, batch('v3', '2026-09-10T20:00:00+08:00', [v3]))
        digest.ingest(self.root, batch('reread-v2', '2026-09-11T20:00:00+08:00', [v2]))
        found = digest.candidates(self.root, ranking_type='daily', period='2026-09-11')['candidates'][0]
        self.assertEqual('v3', found['event']['id'])
        self.assertIsNone(found['selection_block'])
        self.assertEqual('2026-09-10T20:00:00+08:00', found['last_verified_at'])
        self.assertEqual('2026-09-11T20:00:00+08:00', found['last_collected_at'])

    def test_verification_max_is_event_specific_not_ingestion_order(self):
        v3 = update_record('v3', '2026-09-10T10:00:00+08:00')
        for run, collected, verified in [('new-check', '2026-09-11T20:00:00+08:00', '2026-09-11T19:00:00+08:00'),
                                         ('late-import', '2026-09-12T20:00:00+08:00', '2026-09-10T20:00:00+08:00')]:
            record = {**v3, 'discovered_at': '2026-09-10T11:00:00+08:00', 'verified_at': verified, 'verification_level': 'documented'}
            digest.ingest(self.root, batch(run, collected, [record]))
        found = digest.candidates(self.root, ranking_type='daily', period='2026-09-12')['candidates'][0]
        self.assertEqual('2026-09-11T19:00:00+08:00', found['last_verified_at'])
        self.assertEqual(found['verified_at'], found['last_verified_at'])

    def test_pending_update_does_not_borrow_old_verification(self):
        digest.ingest(self.root, batch())
        archive(self.root, issue())
        pending = update_record()
        pending.update(evidence_status='discovered', evidence_urls=[])
        digest.ingest(self.root, batch('pending', '2026-09-09T20:00:00+08:00', [pending]))
        found = digest.candidates(self.root, ranking_type='daily', period='2026-09-09')['candidates'][0]
        self.assertEqual('needs_evidence', found['selection_status'])
        self.assertIsNone(found['last_verified_at'])
        self.assertEqual('discovered', found['evidence_status'])

    def test_hard_exit_after_first_archive_export_is_recoverable_after_24_hours(self):
        digest.ingest(self.root, batch())
        original = digest._write_archive_file
        def interrupt(path, text):
            if path.suffix == '.json':
                raise SystemExit('simulated process exit')
            return original(path, text)
        with patch.object(digest, '_write_archive_file', side_effect=interrupt):
            with self.assertRaises(SystemExit):
                archive(self.root, issue())
        self.assertEqual(1, digest.status(self.root)['issue_count'])
        result = digest.recover(self.root, ranking_type='daily', period='2026-09-07', apply=True)
        self.assertEqual('recovered', result['status'])
        self.assertEqual('unchanged', digest.archive(self.root, issue())['status'])

    def test_interrupted_archive_also_retires_known_draft_exports(self):
        digest.ingest(self.root, batch())
        digest.save_draft(self.root, issue())
        with patch.object(digest, '_write_archive_file', side_effect=SystemExit('after database commit')):
            with self.assertRaises(SystemExit):
                archive(self.root, issue())
        report = digest.recover(self.root, ranking_type='daily', period='2026-09-07', apply=True)
        self.assertEqual(2, len(report['removed_superseded_drafts']))
        self.assertFalse(any(path.exists() for path in digest._exports(self.root, 'daily', '2026-09-07', draft=True)))
        self.assertEqual([], digest.status(self.root)['drafts'])

    def test_draft_export_failure_keeps_new_version_recoverable(self):
        digest.ingest(self.root, batch())
        first = issue()
        digest.save_draft(self.root, first)
        changed = copy.deepcopy(first)
        changed['title'] = 'Updated draft'
        original = digest._atomic_write
        def interrupt(path, text):
            if path.suffix == '.json':
                raise OSError('simulated manifest failure')
            return original(path, text)
        with patch.object(digest, '_atomic_write', side_effect=interrupt):
            with self.assertRaisesRegex(digest.DigestError, 'committed'):
                digest.save_draft(self.root, changed)
        digest.recover(self.root, ranking_type='daily', period='2026-09-07', apply=True)
        article, manifest = digest._exports(self.root, 'daily', '2026-09-07', draft=True)
        self.assertIn('Updated draft', article.read_text(encoding='utf-8'))
        self.assertEqual('Updated draft', json.loads(manifest.read_text(encoding='utf-8'))['title'])

    def test_conflicting_archive_is_reported_not_silently_overwritten(self):
        digest.ingest(self.root, batch())
        saved = archive(self.root, issue())
        article = Path(saved['article_path'])
        article.write_text('external edit', encoding='utf-8')
        result = digest.recover(self.root, ranking_type='daily', period='2026-09-07')
        self.assertEqual('conflict', result['exports'][0]['state'])
        with self.assertRaisesRegex(digest.DigestError, 'conflict'):
            digest.recover(self.root, ranking_type='daily', period='2026-09-07', apply=True)
        self.assertEqual('external edit', article.read_text(encoding='utf-8'))
        recovered = digest.recover(self.root, ranking_type='daily', period='2026-09-07', apply=True, quarantine=True)
        self.assertEqual('external edit', Path(recovered['quarantined'][0]['backup_path']).read_text(encoding='utf-8'))
        self.assertNotEqual('external edit', article.read_text(encoding='utf-8'))

    def test_v2_migration_preserves_archives_and_marks_legacy_draft_manifest_unknown(self):
        digest.ingest(self.root, batch())
        saved = archive(self.root, issue())
        draft = issue(period='2026-09-08', prepared_at='2026-09-09T09:00:00+08:00', items=[])
        digest.save_draft(self.root, draft)
        article_before = Path(saved['article_path']).read_bytes()
        with contextlib.closing(sqlite3.connect(self.root / digest.DB_PATH)) as connection:
            for name in ('manifest', 'manifest_hash', 'article_hash', 'previous_article_hash', 'previous_manifest_hash'):
                connection.execute('ALTER TABLE drafts DROP COLUMN ' + name)
            connection.execute('PRAGMA user_version=2')
            connection.commit()
        migrated = digest.migrate(self.root)
        self.assertEqual(3, migrated['schema_version'])
        self.assertTrue(Path(migrated['backup_path']).exists())
        with contextlib.closing(sqlite3.connect(migrated['backup_path'])) as backup:
            self.assertEqual(2, backup.execute('PRAGMA user_version').fetchone()[0])
            self.assertEqual(1, backup.execute('SELECT COUNT(*) FROM ranked_issues').fetchone()[0])
        self.assertEqual(article_before, Path(saved['article_path']).read_bytes())
        report = digest.recover(self.root, ranking_type='daily', period='2026-09-08')
        self.assertEqual('legacy_untracked', report['exports'][1]['state'])
        with self.assertRaisesRegex(digest.DigestError, 'legacy draft'):
            digest.save_draft(self.root, draft)
        result = digest.recover(self.root, ranking_type='daily', period='2026-09-08', apply=True, quarantine=True)
        self.assertEqual('requires_draft_refresh', result['status'])
        self.assertEqual('draft', digest.save_draft(self.root, draft)['status'])

    def test_orphan_requires_explicit_quarantine_and_fresh_archive_evidence(self):
        digest.ingest(self.root, batch())
        article, manifest = digest._exports(self.root, 'daily', '2026-09-07')
        digest._atomic_write(article, 'old interrupted export')
        report = digest.recover(self.root, ranking_type='daily', period='2026-09-07')
        self.assertEqual('orphan', report['exports'][0]['state'])
        with self.assertRaisesRegex(digest.DigestError, 'orphan'):
            archive(self.root, issue())
        repaired = digest.recover(self.root, ranking_type='daily', period='2026-09-07', apply=True, quarantine=True)
        self.assertFalse(article.exists())
        self.assertEqual('old interrupted export', Path(repaired['quarantined'][0]['backup_path']).read_text(encoding='utf-8'))
        with self.assertRaisesRegex(digest.DigestError, 'stale'):
            digest.archive(self.root, issue())


if __name__ == '__main__':
    unittest.main()
