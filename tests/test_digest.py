from __future__ import annotations
import contextlib
import copy
import io
import json
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ai_notes import digest


def candidate(url='https://github.com/example/tool', **changes):
    value = dict(url=url, title='Practical tool', category='开源项目', summary='Organizes local documents.',
                 reason='Makes a repeated task easier.', source_urls=['https://news.ycombinator.com/item?id=123'],
                 published_at='2021-01-01', kind='project', evidence_status='verified',
                 evidence_urls=[url + '/blob/main/README.md'], change_note='')
    value.update(changes)
    return value


def batch(run_id='run-1', collected_at='2026-09-07T20:00:00+08:00', items=None):
    return dict(schema_version='digest-batch.v1', run_id=run_id, collected_at=collected_at,
                sources=[dict(name='Show HN', url='https://news.ycombinator.com/show', status='ok', detail='Read source.')],
                candidates=[candidate()] if items is None else items)


def item(record=None, verified_at='2026-09-07T20:00:00+08:00', **changes):
    record = record or candidate()
    value = {key: record[key] for key in ('url', 'kind', 'title', 'category', 'summary', 'reason', 'evidence_urls', 'change_note')}
    value.update(featured=True, audience='Readers managing documents', usage_conditions='Install locally; check license.',
                 verification_level='documented', verified_at=verified_at, detail='Weekly workflow context and comparison.',
                 retention_reason='A reusable workflow worth keeping after the news.')
    if record.get('event'):
        value['event'] = copy.deepcopy(record['event'])
    value.update(changes)
    return value


def issue(kind='daily', period='2026-09-07', prepared_at='2026-09-08T09:00:00+08:00', items=None):
    return dict(schema_version='digest-issue.v2', ranking_type=kind, period=period, title='Useful projects',
                prepared_at=prepared_at, shortfall_reason='Only useful verified candidates are included.',
                verification_note='Read originals; not locally installed.', items=[item()] if items is None else items)


def archive(root, document, article=None):
    with patch.object(digest, '_now', return_value=datetime.fromisoformat(document['prepared_at']) + timedelta(minutes=1)):
        return digest.archive(root, document, article)


def update_record(version='v2', occurred='2026-09-09T10:00:00+08:00'):
    url = 'https://github.com/example/tool/releases/tag/' + version
    return candidate(kind='update', change_note='Adds an export workflow.', evidence_urls=[url],
                     event=dict(id=version, url=url, occurred_at=occurred, type='update'))


class DigestTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        clock = patch.object(digest, '_now', return_value=datetime.fromisoformat('2026-11-10T12:00:00+08:00'))
        clock.start()
        self.addCleanup(clock.stop)

    def test_url_identity(self):
        self.assertEqual('https://github.com/example/tool', digest.canonical_url('http://www.github.com/EXAMPLE/Tool.git/releases/tag/v2?utm_campaign=x#feature', 'update'))
        self.assertEqual('https://github.com/example/tool/blob/main/Guide.md', digest.canonical_url('https://github.com/EXAMPLE/Tool/blob/main/Guide.md?utm_source=hn', 'reading'))
        self.assertEqual('https://example.org/post?id=3&lang=zh', digest.canonical_url('https://EXAMPLE.org/post?utm_source=x&lang=zh&id=3#part', 'reading'))
        for url in ('javascript:alert(1)', 'https://user:password@example.com', 'https://github.com/owner'):
            with self.subTest(url=url), self.assertRaises(digest.DigestError):
                digest.canonical_url(url)

    def test_v1_compatible_and_unverified_text_cannot_replace_verified(self):
        digest.ingest(self.root, batch())
        later = candidate(evidence_status='discovered', evidence_urls=[], summary='Unverified feature', source_urls=['https://hellogithub.com/project/a'])
        digest.ingest(self.root, batch('next', '2026-09-08T08:00:00+08:00', [later]))
        found = digest.candidates(self.root, '2026-09-08', '2026-09-08')['candidates'][0]
        self.assertEqual('Organizes local documents.', found['summary'])
        self.assertEqual('2026-09-07T20:00:00+08:00', found['first_discovered_at'])
        self.assertEqual('discovered', found['latest_observation']['evidence_status'])
        self.assertEqual(2, len(found['source_urls']))

    def test_beijing_gaps_failures_empty_and_truncation(self):
        digest.ingest(self.root, batch('before', '2026-09-06T15:59:59Z', [candidate('https://github.com/example/outside')]))
        digest.ingest(self.root, batch('first', '2026-09-06T16:00:00Z'))
        value = batch('last', '2026-09-07T15:59:59Z', [candidate('https://github.com/example/second')])
        value['sources'] += [dict(name='Unavailable', url='https://example.com/fail', status='failed', detail='HTTP 503'), dict(name='Quiet', url='https://example.com/empty', status='empty', detail='No items')]
        digest.ingest(self.root, value)
        result = digest.candidates(self.root, '2026-09-07', '2026-09-08', 1)
        self.assertEqual(2, result['total_candidates'])
        self.assertTrue(result['truncated'])
        self.assertEqual(['2026-09-08'], result['missing_collection_dates'])
        self.assertEqual('HTTP 503', result['source_gaps'][0]['detail'])
        self.assertEqual(1, len(result['empty_sources']))

    def test_batch_idempotency_and_validation_before_writes(self):
        value = batch()
        digest.ingest(self.root, value)
        self.assertEqual('unchanged', digest.ingest(self.root, dict(reversed(list(value.items()))))['status'])
        value['candidates'][0]['title'] = 'Other'
        with self.assertRaisesRegex(digest.DigestError, 'different content'):
            digest.ingest(self.root, value)
        unused = self.root / 'untouched'
        with self.assertRaises(digest.DigestError):
            digest.ingest(unused, batch(items=[candidate(evidence_urls=[])]))
        self.assertFalse(unused.exists())

    def test_ingest_sql_failure_rolls_back_whole_batch(self):
        digest.ingest(self.root, batch())
        with contextlib.closing(sqlite3.connect(self.root / digest.DB_PATH)) as connection:
            connection.execute("CREATE TRIGGER fail_bad BEFORE INSERT ON observations WHEN NEW.canonical_url='https://github.com/example/bad' BEGIN SELECT RAISE(ABORT, 'failure'); END")
            connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            digest.ingest(self.root, batch('bad', items=[candidate('https://github.com/example/good'), candidate('https://github.com/example/bad')]))
        self.assertEqual((1, 1), (digest.status(self.root)['observation_count'], digest.status(self.root)['batch_count']))

    def test_v2_time_and_level_validation_and_future_collection(self):
        value = batch()
        value['schema_version'] = 'digest-batch.v2'
        value['candidates'][0].update(discovered_at='2026-09-07T19:00:00+08:00', verified_at='2026-09-07T20:00:00+08:00', verification_level='documented')
        digest.ingest(self.root, value)
        value['run_id'] = 'bad'
        value['candidates'][0]['discovered_at'] = '2026-09-08T19:00:00+08:00'
        with self.assertRaisesRegex(digest.DigestError, 'discovered_at'):
            digest.ingest(self.root, value)
        with self.assertRaisesRegex(digest.DigestError, 'future'):
            digest.ingest(self.root, batch('future', '2027-01-01T12:00:00+08:00'))

    def test_missing_ledger_read_only(self):
        unused = self.root / 'unused'
        self.assertEqual('empty', digest.status(unused)['status'])
        self.assertEqual(['2026-09-01', '2026-09-02'], digest.candidates(unused, '2026-09-01', '2026-09-02')['missing_collection_dates'])
        self.assertFalse(unused.exists())

    def test_natural_periods_leap_month_and_due_boundaries(self):
        start, end, due_at = digest.period_window('monthly', '2024-02')
        self.assertEqual(('2024-02-01', '2024-02-29', 11), (str(start), str(end), due_at.hour))
        with self.assertRaisesRegex(digest.DigestError, 'Monday'):
            digest.period_window('weekly', '2026-09-08')
        for time, expected in [('08:59:59', [False, False, False]), ('09:00:00', [True, False, False]), ('10:00:00', [True, True, False]), ('11:00:00', [True, True, True])]:
            result = digest.due(self.root, '2026-06-01T' + time + '+08:00')['periods']
            self.assertEqual(expected, [entry['due'] for entry in result])
            self.assertEqual(['2026-05-31', '2026-05-25', '2026-05'], [entry['period'] for entry in result])

    def test_dispatch_monday_first_day_four_slots_and_beijing_timezone(self):
        expected = [('09', 'generate', 'daily', '2026-05-31'), ('10', 'generate', 'weekly', '2026-05-25'),
                    ('11', 'generate', 'monthly', '2026-05'), ('19', 'collect', None, '2026-06-01')]
        for hour, action, kind, period in expected:
            result = digest.dispatch(self.root, f'2026-06-01T{hour}:00:00+08:00')
            self.assertEqual(1, len(result['actions']))
            found = result['actions'][0]
            self.assertEqual((action, kind, period), (found['action'], found.get('ranking_type'), found['period']))
        utc = digest.dispatch(self.root, '2026-06-01T01:15:00Z')
        self.assertEqual('09:00', utc['slot'])
        self.assertEqual('2026-06-01T09:15:00+08:00', utc['evaluated_at'])
        self.assertEqual('daily', utc['actions'][0]['ranking_type'])

    def test_dispatch_non_target_slots_return_empty_without_reading_ledger(self):
        with patch.object(digest, 'due', side_effect=AssertionError('inactive slot must not inspect work')):
            for moment in ('2026-06-01T08:59:59+08:00', '2026-06-01T12:00:00+08:00',
                           '2026-06-02T10:00:00+08:00', '2026-06-02T11:00:00+08:00',
                           '2026-06-08T11:00:00+08:00', '2026-06-01T20:00:00+08:00'):
                with self.subTest(moment=moment):
                    self.assertEqual([], digest.dispatch(self.root, moment)['actions'])

    def test_dispatch_collection_retries_only_oldest_due_monthly_draft(self):
        for period in ('2026-04', '2026-05', '2026-06'):
            digest.save_draft(self.root, issue('monthly', period, '2026-06-01T11:00:00+08:00', []))
        result = digest.dispatch(self.root, '2026-06-01T19:00:00+08:00')
        self.assertEqual(['collect', 'backfill_monthly'], [entry['action'] for entry in result['actions']])
        self.assertEqual(('2026-04', 20), (result['actions'][1]['period'], result['actions'][1]['shortfall']))

    def test_ongoing_draft_zero_items_and_monthly_shortfall(self):
        digest.ingest(self.root, batch())
        document = issue('monthly', '2026-09')
        saved = digest.save_draft(self.root, document)
        manifest = json.loads(Path(saved['manifest_path']).read_text(encoding='utf-8'))
        self.assertEqual(19, saved['shortfall'])
        self.assertNotIn('2026-09-09', manifest['coverage']['missing_collection_dates'])
        self.assertIn('2026-09-09', manifest['coverage']['not_elapsed_dates'])
        with self.assertRaisesRegex(digest.DigestError, 'complete natural period'):
            archive(self.root, document)
        empty = issue('monthly', '2026-09', '2026-10-01T11:00:00+08:00', [])
        self.assertEqual(0, digest.save_draft(self.root, empty)['item_count'])
        with self.assertRaisesRegex(digest.DigestError, 'zero-item'):
            archive(self.root, empty)
        self.assertEqual((0, 1), (digest.status(self.root)['issue_count'], len(digest.status(self.root)['drafts'])))

    def test_monthly_nineteen_twenty_and_three_archives_same_day(self):
        records = [candidate(f'https://github.com/example/tool{i}') for i in range(20)]
        observed = '2026-05-31T20:00:00+08:00'
        digest.ingest(self.root, batch('month', observed, records))
        selected = [item(record, observed, featured=index < 3) for index, record in enumerate(records)]
        document = issue('monthly', '2026-05', '2026-06-01T11:00:00+08:00', selected[:19])
        with self.assertRaisesRegex(digest.DigestError, 'at least 20'):
            archive(self.root, document)
        document['items'] = selected
        results = [archive(self.root, document), archive(self.root, issue('weekly', '2026-05-25', '2026-06-01T11:00:00+08:00', selected)), archive(self.root, issue('daily', '2026-05-31', '2026-06-01T11:00:00+08:00', selected[:5]))]
        self.assertEqual(3, len({result['article_path'] for result in results}))
        self.assertEqual(3, digest.status(self.root)['issue_count'])
        for hour in ('09', '10', '11'):
            self.assertEqual([], digest.dispatch(self.root, f'2026-06-01T{hour}:00:00+08:00')['actions'])
        for kind in digest.RANKINGS:
            self.assertIn('Useful projects', (self.root / digest.ARTICLE_DIR / kind / 'index.md').read_text(encoding='utf-8'))

    def test_all_same_level_history_never_expires_cross_level_allowed(self):
        digest.ingest(self.root, batch('discovery', '2026-09-06T20:00:00+08:00', [candidate(evidence_status='discovered', evidence_urls=[])]))
        digest.ingest(self.root, batch())
        archive(self.root, issue())
        archive(self.root, issue('weekly', '2026-08-31'))
        for number in range(8, 13):
            record = candidate(f'https://github.com/example/other{number}')
            observed = f'2026-09-{number:02d}T20:00:00+08:00'
            digest.ingest(self.root, batch(f'run{number}', observed, [record]))
            archive(self.root, issue('daily', f'2026-09-{number:02d}', f'2026-09-{number+1:02d}T09:00:00+08:00', [item(record, observed)]))
        observed = '2026-09-13T20:00:00+08:00'
        digest.ingest(self.root, batch('again', observed))
        with self.assertRaisesRegex(digest.DigestError, 'same-level history'):
            archive(self.root, issue('daily', '2026-09-13', '2026-09-14T09:00:00+08:00', [item(verified_at=observed)]))

    def test_update_rewording_id_swap_old_event_and_real_new_event(self):
        digest.ingest(self.root, batch())
        archive(self.root, issue())
        record = update_record()
        observed = '2026-09-09T20:00:00+08:00'
        digest.ingest(self.root, batch('update', observed, [record]))
        archive(self.root, issue('daily', '2026-09-09', '2026-09-10T09:00:00+08:00', [item(record, observed)]))
        again = copy.deepcopy(record)
        again['change_note'] = 'Same exporter in new wording.'
        again['event']['id'] = 'renamed'
        later = '2026-09-10T20:00:00+08:00'
        digest.ingest(self.root, batch('reword', later, [again]))
        with self.assertRaisesRegex(digest.DigestError, 'same event'):
            archive(self.root, issue('daily', '2026-09-10', '2026-09-11T09:00:00+08:00', [item(again, later)]))
        old = update_record('unreported-old', '2026-09-01T10:00:00+08:00')
        digest.ingest(self.root, batch('old', later, [old]))
        with self.assertRaisesRegex(digest.DigestError, 'newer'):
            archive(self.root, issue('daily', '2026-09-10', '2026-09-11T09:00:00+08:00', [item(old, later)]))
        new = update_record('v3', '2026-09-10T10:00:00+08:00')
        digest.ingest(self.root, batch('new', later, [new]))
        self.assertEqual('archived', archive(self.root, issue('daily', '2026-09-10', '2026-09-11T09:00:00+08:00', [item(new, later)]))['status'])

    def test_carry_forward_retains_discovery_publication_and_label(self):
        digest.ingest(self.root, batch())
        observed = '2026-09-15T20:00:00+08:00'
        digest.ingest(self.root, batch('recheck', observed))
        result = archive(self.root, issue('daily', '2026-09-15', '2026-09-16T09:00:00+08:00', [item(verified_at=observed)]))
        selected = json.loads(Path(result['manifest_path']).read_text(encoding='utf-8'))['items'][0]
        self.assertEqual(('往期候选', '2021-01-01', '2026-09-07T20:00:00+08:00'), (selected['period_label'], selected['published_at'], selected['first_discovered_at']))

    def test_q12_boundaries_backfill_late_verification_and_future_event(self):
        before = candidate('https://github.com/example/before', evidence_status='discovered', evidence_urls=[])
        digest.ingest(self.root, batch('before', '2026-09-30T23:59:00+08:00', [before]))
        after = candidate('https://github.com/example/after')
        digest.ingest(self.root, batch('after', '2026-09-30T16:01:00Z', [after]))
        digest.ingest(self.root, batch('old-tool', '2026-09-20T20:00:00+08:00'))
        future = update_record('october', '2026-10-02T10:00:00+08:00')
        release_url = 'https://github.com/example/released/releases/tag/v1'
        release = candidate('https://github.com/example/released', evidence_urls=[release_url], event=dict(id='v1', url=release_url, occurred_at='2026-09-20T10:00:00+08:00', type='release'))
        old_verified = candidate(before['url'])
        observed = '2026-10-03T10:00:00+08:00'
        digest.ingest(self.root, batch('late', observed, [old_verified, after, release, future]))
        document = issue('monthly', '2026-09', '2026-10-03T11:00:00+08:00', [item(old_verified, observed), item(release, observed, featured=False)])
        self.assertEqual('draft', digest.save_draft(self.root, document)['status'])
        for bad in (after, future):
            with self.subTest(url=bad['url']), self.assertRaisesRegex(digest.DigestError, 'Q12'):
                digest.save_draft(self.root, issue('monthly', '2026-09', '2026-10-03T11:00:00+08:00', [item(bad, observed)]))
        found = digest.candidates(self.root, ranking_type='monthly', period='2026-09')['candidates']
        self.assertNotIn(after['url'], [entry['canonical_url'] for entry in found])
        self.assertFalse(any(entry.get('event', {}).get('id') == 'october' for entry in found))

    def test_newer_month_delivered_first_blocks_old_draft(self):
        records = [candidate(f'https://github.com/example/tool{i}') for i in range(20)]
        observed = '2026-09-30T20:00:00+08:00'
        digest.ingest(self.root, batch('sept', observed, records))
        selected = [item(record, observed, featured=index < 3) for index, record in enumerate(records)]
        september = issue('monthly', '2026-09', '2026-10-01T11:00:00+08:00', selected)
        digest.save_draft(self.root, september)
        november = '2026-11-01T10:00:00+08:00'
        digest.ingest(self.root, batch('nov-check', november, records))
        selected = [item(record, november, featured=index < 3) for index, record in enumerate(records)]
        archive(self.root, issue('monthly', '2026-10', '2026-11-01T11:00:00+08:00', selected))
        september.update(prepared_at='2026-11-01T11:00:00+08:00', items=selected)
        with self.assertRaisesRegex(digest.DigestError, 'same-level history'):
            archive(self.root, september)
        self.assertEqual(1, len(digest.status(self.root)['drafts']))

    def test_render_exact_structure_immutable_replay_and_repair(self):
        digest.ingest(self.root, batch())
        document = issue()
        rendered = digest.render(self.root, document, draft=False)
        for invalid in ('# Other article', rendered + '\n### Unselected project\n', rendered.replace('Practical tool', 'Invented')):
            with self.assertRaisesRegex(digest.DigestError, 'exactly match'):
                archive(self.root, document, invalid)
        result = archive(self.root, document, rendered)
        Path(result['article_path']).unlink()
        (self.root / digest.ARTICLE_DIR / 'daily/index.md').unlink()
        self.assertEqual('unchanged', digest.archive(self.root, document)['status'])
        self.assertEqual(rendered, Path(result['article_path']).read_text(encoding='utf-8'))
        changed = copy.deepcopy(document)
        changed['title'] = 'Other'
        with self.assertRaisesRegex(digest.DigestError, 'different content'):
            archive(self.root, changed)
        self.assertEqual(1, digest.status(self.root)['issue_count'])

    def test_interrupted_export_removes_orphan_and_replay_is_single_archive(self):
        digest.ingest(self.root, batch())
        original = digest._write_archive_file
        def fail(path, content):
            if path.suffix == '.json':
                raise OSError('interrupted manifest export')
            return original(path, content)
        with patch.object(digest, '_write_archive_file', side_effect=fail):
            with self.assertRaises(OSError):
                archive(self.root, issue())
        self.assertEqual(0, digest.status(self.root)['issue_count'])
        self.assertFalse((self.root / digest.ARTICLE_DIR / 'daily/2026-09-07.md').exists())
        self.assertEqual('archived', archive(self.root, issue())['status'])
        self.assertEqual(1, digest.status(self.root)['issue_count'])

    def test_stale_prepared_cannot_finalize_delayed_draft_but_replay_is_timeless(self):
        digest.ingest(self.root, batch())
        digest.save_draft(self.root, issue())
        with self.assertRaisesRegex(digest.DigestError, 'stale prepared_at'):
            digest.archive(self.root, issue())
        archive(self.root, issue())
        self.assertEqual('unchanged', digest.archive(self.root, issue())['status'])

    def test_two_twenty_three_hour_gaps_do_not_allow_old_verification(self):
        digest.ingest(self.root, batch())
        document = issue(prepared_at='2026-09-08T19:00:00+08:00')
        with patch.object(digest, '_now', return_value=datetime.fromisoformat('2026-09-09T18:00:00+08:00')):
            with self.assertRaisesRegex(digest.DigestError, '24 hours of actual formal archive'):
                digest.archive(self.root, document)
        self.assertEqual(0, digest.status(self.root)['issue_count'])

    def test_unverified_unrecorded_stale_and_upgraded_claims_rejected(self):
        digest.ingest(self.root, batch())
        for change in [dict(verification_level='tested'), dict(evidence_urls=['https://example.com/unread']), dict(verified_at='2026-09-08T10:00:00+08:00'), dict(verified_at='2026-09-01T10:00:00+08:00')]:
            document = issue()
            document['items'][0].update(change)
            with self.subTest(change=change), self.assertRaises(digest.DigestError):
                archive(self.root, document)
        discovered = candidate('https://github.com/example/unverified', evidence_status='discovered', evidence_urls=[])
        digest.ingest(self.root, batch('discovered', items=[discovered]))
        with self.assertRaisesRegex(digest.DigestError, 'no verified observation'):
            archive(self.root, issue(items=[item(candidate(discovered['url']))]))
        self.assertEqual(0, digest.status(self.root)['issue_count'])

    def test_counts_duplicates_reading_featured_opportunity_contract(self):
        digest.ingest(self.root, batch())
        document = issue()
        document['shortfall_reason'] = ''
        with self.assertRaisesRegex(digest.DigestError, 'shortfall_reason'):
            archive(self.root, document)
        with self.assertRaisesRegex(digest.DigestError, 'cannot occur twice'):
            archive(self.root, issue(items=[item(), item()]))
        with self.assertRaisesRegex(digest.DigestError, 'featured count'):
            archive(self.root, issue('weekly', '2026-08-31', items=[item(featured=False)]))
        reading = candidate('https://author.example/post', kind='reading', category='博客、帖子与访谈', evidence_urls=['https://author.example/post'])
        digest.ingest(self.root, batch('reading', items=[reading]))
        document = issue(items=[item(reading, featured=False, author='Author', original_date='2021-01-01')])
        opportunity = dict(problem='A user problem', audience='Users', existing_solutions='Compared tools', validation='Small interview', evidence_urls=['https://example.com/problem'], evidence_date='2026-09-07')
        document['opportunities'] = [opportunity, opportunity]
        self.assertEqual(1, archive(self.root, document)['item_count'])
        document['opportunities'].append(opportunity)
        with self.assertRaisesRegex(digest.DigestError, 'at most 2'):
            digest._validate_issue(document)

    def test_render_actual_source_failures_and_coverage(self):
        digest.ingest(self.root, batch('discovery', '2026-08-30T20:00:00+08:00', [candidate(evidence_status='discovered', evidence_urls=[])]))
        value = batch()
        value['sources'].append(dict(name='Blocked', url='https://example.org/blocked', status='failed', detail='HTTP 403'))
        digest.ingest(self.root, value)
        text = digest.render(self.root, issue('weekly', '2026-08-31'), draft=False)
        self.assertIn('HTTP 403', text)
        self.assertIn('本期实际采集日期：无', text)
        self.assertIn('期后核验／补采实际日期：2026-09-07', text)
        self.assertNotIn('2026-09-07T20:00:00', text)

    def test_legacy_migration_recoverable_backup_counts_and_delivery(self):
        path = self.root / digest.DB_PATH
        path.parent.mkdir(parents=True)
        connection = sqlite3.connect(path)
        connection.executescript('''CREATE TABLE batches (run_id TEXT PRIMARY KEY,collected_at TEXT,collected_epoch REAL,local_date TEXT,content_hash TEXT,payload TEXT);
CREATE TABLE observations (id INTEGER PRIMARY KEY,run_id TEXT,canonical_url TEXT,payload TEXT);
CREATE TABLE issues (date TEXT PRIMARY KEY,title TEXT,manifest TEXT,content_hash TEXT,article TEXT,article_hash TEXT);
CREATE TABLE selections (issue_date TEXT,canonical_url TEXT,payload TEXT,PRIMARY KEY(issue_date,canonical_url));''')
        value = batch()
        connection.execute('INSERT INTO batches VALUES (?,?,?,?,?,?)', ('run-1', value['collected_at'], datetime.fromisoformat(value['collected_at']).timestamp(), '2026-09-07', digest._hash(digest._json(value)), digest._json(value)))
        connection.execute('INSERT INTO observations VALUES (1,?,?,?)', ('run-1', candidate()['url'], digest._json(candidate())))
        manifest = digest._json(dict(items=[item()]))
        connection.execute('INSERT INTO issues VALUES (?,?,?,?,?,?)', ('2026-09-06', 'Old weekly', manifest, digest._hash(manifest), 'Original article', digest._hash('Original article')))
        connection.execute('INSERT INTO selections VALUES (?,?,?)', ('2026-09-06', candidate()['url'], digest._json(item())))
        connection.commit()
        connection.close()
        old_bytes = path.read_bytes()
        self.assertEqual(0, digest.status(self.root)['schema_version'])
        self.assertEqual(old_bytes, path.read_bytes())
        result = digest.migrate(self.root)
        self.assertEqual('migrated', result['status'])
        with contextlib.closing(sqlite3.connect(result['backup_path'])) as backup:
            self.assertEqual('ok', backup.execute('PRAGMA integrity_check').fetchone()[0])
            self.assertEqual(1, backup.execute('SELECT COUNT(*) FROM observations').fetchone()[0])
        self.assertEqual('unchanged', digest.migrate(self.root)['status'])
        state = digest.status(self.root)
        self.assertEqual((1, 1, 1, 1), (state['batch_count'], state['candidate_count'], state['observation_count'], state['issue_count']))
        observed = '2026-09-13T20:00:00+08:00'
        digest.ingest(self.root, batch('recheck', observed))
        with self.assertRaisesRegex(digest.DigestError, 'same-level history'):
            archive(self.root, issue('weekly', '2026-09-07', '2026-09-14T10:00:00+08:00', [item(verified_at=observed)]))

    def test_cli_json_errors_v1_archive_rejected_managed_paths_protected(self):
        digest.ingest(self.root, batch())
        path = self.root / 'issue.json'
        path.write_text(json.dumps(issue()), encoding='utf-8')
        for args in [['status'], ['candidates', '--root', str(self.root), '--since', 'bad', '--until', '2026-09-07'], ['render', '--root', str(self.root), '--file', str(path), '--output', str(self.root / digest.ARTICLE_DIR / 'daily/x.md')]]:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(2, digest.main(args))
            self.assertEqual('error', json.loads(output.getvalue())['status'])
        with self.assertRaises(digest.DigestError):
            digest.archive(self.root, dict(schema_version='digest-issue.v1', date='2026-09-07'), 'old bypass')


if __name__ == '__main__':
    unittest.main()
