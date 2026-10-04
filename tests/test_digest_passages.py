from __future__ import annotations

import copy
import unittest

from ai_notes import digest_pipeline as pipeline
from ai_notes.digest_passages import build_passages, bind_review, MAX_CHARS


class PassageBuildTests(unittest.TestCase):
    def test_complete_text_is_retained_in_contiguous_bounded_slices(self):
        text = '# Install\n\n' + ('Introductory paragraph with a condition.\n\n' * 85)
        text += '## macOS\n\n' + ('Apple Silicon host using an Intel build must match plugin architecture.\n' * 80)
        text += '\n```text\n# Not a heading inside code\n```\nFinal exception remains here.\n'
        rows = build_passages([dict(url='https://docs.example/install', text=text)])
        self.assertGreater(len(rows), 3)
        self.assertEqual(text, ''.join(row['quote'] for row in rows))
        self.assertTrue(all(0 < len(row['quote']) <= MAX_CHARS and row['quote'] in text for row in rows))
        mac = [row for row in rows if 'Apple Silicon' in row['quote']]
        self.assertTrue(mac)
        self.assertTrue(all(row['heading_path'] == ['Install', 'macOS'] for row in mac))
        self.assertFalse(any('Not a heading inside code' in row['heading_path'] for row in rows))
        self.assertIn('Final exception remains here.', rows[-1]['quote'])

    def test_long_unicode_line_and_crlf_are_preserved_without_silent_truncation(self):
        text = '适用条件🙂' * 2500 + '\r\n' + 'Only the Windows source-install path requires this dependency.\r\n'
        rows = build_passages([dict(url='https://docs.example/unbroken', text=text)])
        self.assertEqual(text, ''.join(row['quote'] for row in rows))
        self.assertTrue(all(len(row['quote']) <= MAX_CHARS for row in rows))
        self.assertGreater(len(rows), 5)

    def test_ids_bind_source_text_and_url_not_context_order(self):
        first = dict(url='https://docs.example/a', text='Identical excerpt.')
        second = dict(url='https://docs.example/b', text='Identical excerpt.')
        forward = build_passages([first, second])
        backward = build_passages([second, first])
        self.assertEqual({row['id'] for row in forward}, {row['id'] for row in backward})
        self.assertNotEqual(forward[0]['id'], forward[1]['id'])
        edited = build_passages([dict(first, text='Identical excerpt. New caveat.')])
        self.assertNotEqual(forward[0]['id'], edited[0]['id'])

    def test_duplicate_url_conflict_is_rejected_and_identical_copy_is_deduplicated(self):
        source = dict(url='https://docs.example/same', text='One immutable snapshot.')
        self.assertEqual(build_passages([source]), build_passages([source, copy.deepcopy(source)]))
        with self.assertRaisesRegex(ValueError, 'different text'):
            build_passages([source, dict(source, text='A different snapshot at the same URL.')])

    def test_heading_hierarchy_and_setext_context_are_kept_across_chunks(self):
        text = 'Usage\n=====\nGeneral guide.\n\n## Windows\n' + ('Path-specific instruction.\n\n' * 160)
        text += '## Linux\nOptional acceleration only.\n'
        rows = build_passages([dict(url='https://docs.example/guide', text=text)])
        windows = [row for row in rows if 'Path-specific instruction.' in row['quote']]
        self.assertGreater(len(windows), 1)
        self.assertTrue(all(row['heading_path'] == ['Usage', 'Windows'] for row in windows))
        self.assertEqual(['Usage', 'Linux'], rows[-1]['heading_path'])
        self.assertEqual(text, ''.join(row['quote'] for row in rows))

    def test_only_whitespace_sources_can_be_omitted(self):
        self.assertEqual([], build_passages([dict(url='https://docs.example/blank', text=' \r\n\t')]))
        self.assertEqual([], build_passages([]))


class PassageBindingTests(unittest.TestCase):
    def setUp(self):
        self.main = 'https://project.example/'
        self.licence = 'https://project.example/LICENSE'
        self.contexts = [dict(url=self.main, text='The tool organizes files. A desktop installer is available. Optional GPU acceleration is separate.'),
                         dict(url=self.licence, text='MIT License. Copyright 2026 Example Authors.')]
        self.passages = build_passages(self.contexts)
        self.main_id, self.licence_id = (row['id'] for row in self.passages)
        self.facts = dict(title='文件整理工具', category='开源项目', summary='整理文件。', reason='减少重复整理工作。',
                          audience='需要整理文件的读者', usage_conditions='可下载桌面安装包；GPU 加速可选。',
                          detail='用于整理本地文件。', retention_reason='日常整理需求可重复使用。', open_source_status='confirmed')
        self.output = dict(qualified=True, reason='提供用途和入口。', facts=self.facts,
                           evidence=dict(category=[self.main_id], summary=[self.main_id],
                                         usage_conditions=[self.main_id], license=[self.licence_id]))
        self.record = dict(kind='project', url=self.main)

    def test_program_binds_actual_quote_and_url_without_mutating_raw_output(self):
        original = copy.deepcopy(self.output)
        source_copy = copy.deepcopy(self.passages)
        bound = bind_review(self.output, self.passages, self.record)
        self.assertEqual(self.output, original)
        self.assertEqual(self.passages, source_copy)
        self.assertEqual([self.main, self.licence], bound['facts']['evidence_urls'])
        for claim in bound['facts']['claims']:
            passage = next(row for row in self.passages if row['evidence_url'] == claim['evidence_url'])
            self.assertEqual(passage['quote'], claim['quote'])
        self.assertEqual(self.contexts[1]['text'], bound['facts']['claims'][-1]['text'])
        self.assertEqual(bound['facts'], pipeline._facts(bound, self.contexts, self.record))
        bound['facts']['summary'] = 'Changed return value only.'
        self.assertEqual(original, self.output)

    def test_multiple_ids_are_combined_evidence_and_keep_individual_sources(self):
        self.output['evidence']['summary'] = [self.main_id, self.licence_id]
        bound = bind_review(self.output, self.passages, self.record)
        claims = [claim for claim in bound['facts']['claims'] if claim['field'] == 'summary']
        self.assertEqual([self.main, self.licence], [claim['evidence_url'] for claim in claims])
        self.assertEqual([self.facts['summary']] * 2, [claim['text'] for claim in claims])
        self.assertEqual([row['quote'] for row in self.passages], [claim['quote'] for claim in claims])

    def test_fabricated_or_other_source_snapshot_ids_are_not_repaired(self):
        foreign_id = build_passages([dict(url=self.main, text='Different snapshot with the same URL.')])[0]['id']
        for invalid_id in ('p_invented', foreign_id):
            with self.subTest(invalid_id=invalid_id):
                output = copy.deepcopy(self.output)
                output['evidence']['summary'] = [invalid_id]
                with self.assertRaisesRegex(ValueError, 'unknown'):
                    bind_review(output, self.passages, self.record)

    def test_passage_id_cannot_be_ambiguous_across_sources(self):
        passages = copy.deepcopy(self.passages)
        passages[1]['id'] = passages[0]['id']
        with self.assertRaisesRegex(ValueError, 'duplicate passage ID'):
            bind_review(self.output, passages, self.record)

    def test_extra_null_fields_and_model_supplied_quotes_are_rejected(self):
        changes = [lambda value: value.update(notes=None),
                   lambda value: value['facts'].update(event_date=None),
                   lambda value: value['evidence'].update(evidence_url=None),
                   lambda value: value['evidence'].update(summary=[dict(id=self.main_id, quote='Invented text')])]
        for change in changes:
            with self.subTest(change=change):
                value = copy.deepcopy(self.output)
                change(value)
                frozen = copy.deepcopy(value)
                with self.assertRaises(ValueError):
                    bind_review(value, self.passages, self.record)
                self.assertEqual(frozen, value)

    def test_previous_failed_quote_contract_is_not_silently_converted(self):
        old = dict(qualified=True, reason='Old malformed result.', facts=copy.deepcopy(self.facts))
        old['facts'].update(evidence_urls=[self.main], claims=[dict(field='summary', text='Tool introduction.',
                                                                 evidence_url=self.main, quote='The tool ... desktop installer ... GPU')])
        frozen = copy.deepcopy(old)
        with self.assertRaisesRegex(pipeline.runtime.RuntimeError, 'quote'):
            pipeline._facts(old, self.contexts, self.record)
        with self.assertRaisesRegex(ValueError, 'exactly'):
            bind_review(old, self.passages, self.record)
        self.assertEqual(frozen, old)

    def test_false_review_requires_null_facts_and_evidence(self):
        output = dict(qualified=False, reason='No usable original source.', facts=None, evidence=None)
        self.assertEqual(dict(qualified=False, reason=output['reason'], facts=None),
                         bind_review(output, self.passages, self.record))
        for key in ('facts', 'evidence'):
            with self.subTest(key=key):
                malformed = dict(output, **{key: {}})
                with self.assertRaisesRegex(ValueError, 'null'):
                    bind_review(malformed, self.passages, self.record)

    def test_empty_license_does_not_bypass_existing_open_source_gate(self):
        self.output['evidence']['license'] = []
        bound = bind_review(self.output, self.passages, self.record)
        with self.assertRaisesRegex(pipeline.runtime.RuntimeError, 'licence'):
            pipeline._facts(bound, self.contexts, self.record)

    def test_core_evidence_cannot_be_empty_or_repeat_the_same_id(self):
        for ids in ([], [self.main_id, self.main_id], None):
            with self.subTest(ids=ids):
                output = copy.deepcopy(self.output)
                output['evidence']['usage_conditions'] = ids
                with self.assertRaises(ValueError):
                    bind_review(output, self.passages, self.record)

    def test_reading_requires_author_and_original_date_evidence(self):
        text = 'By Example Author. Published 2020-01-15. Practical file organization.'
        contexts = [dict(url=self.main, text=text)]
        passages = build_passages(contexts)
        pid = passages[0]['id']
        facts = dict(self.facts, category='博客、帖子与访谈', author='Example Author', original_date='2020-01-15')
        output = dict(qualified=True, reason='Original author article.', facts=facts,
                      evidence={field:[pid] for field in ('category', 'summary', 'usage_conditions', 'author', 'original_date')})
        output['evidence']['license'] = []
        bound = bind_review(output, passages, dict(kind='reading', url=self.main))
        self.assertEqual('2020-01-15', bound['facts']['original_date'])
        self.assertEqual(bound['facts'], pipeline._facts(bound, contexts, dict(kind='reading', url=self.main)))
        output['evidence']['author'] = []
        with self.assertRaises(ValueError):
            bind_review(output, passages, dict(kind='reading', url=self.main))

    def test_update_change_note_retains_summary_and_keeps_event_gate(self):
        event_url = self.main + 'release/v2'
        contexts = self.contexts + [dict(url=event_url, text='Adds an installer with optional acceleration.')]
        passages = build_passages(contexts)
        output = copy.deepcopy(self.output)
        output['evidence']['change_note'] = [passages[-1]['id']]
        record = dict(kind='update', url=self.main, event={'url':event_url})
        bound = bind_review(output, passages, record)
        change = next(claim for claim in bound['facts']['claims'] if claim['field']=='change_note')
        self.assertEqual(output['facts']['summary'], change['text'])
        self.assertEqual(contexts[-1]['text'], change['quote'])
        self.assertEqual(bound['facts'], pipeline._facts(bound, contexts, record))
        output['evidence']['change_note'] = [self.main_id]
        with self.assertRaisesRegex(pipeline.runtime.RuntimeError, 'event original|increment'):
            pipeline._facts(bind_review(output, passages, record), contexts, record)

    def test_update_multiple_passages_preserve_complete_chinese_change_summary(self):
        event_url = self.main + 'release/v3'
        release = '# Installer\nAdds a desktop installer.\n\n# Acceleration\nAdds optional GPU acceleration.\n'
        contexts = self.contexts + [dict(url=event_url, text=release)]
        passages = build_passages(contexts)
        change_passages = [passage for passage in passages if passage['evidence_url'] == event_url]
        self.assertEqual(2, len(change_passages))
        output = copy.deepcopy(self.output)
        output['facts']['summary'] = '本次新增桌面安装包和可选 GPU 加速。'
        output['evidence']['change_note'] = [passage['id'] for passage in change_passages]
        raw = copy.deepcopy(output)
        bound = bind_review(output, passages, dict(kind='update', url=self.main, event={'url':event_url}))
        changes = [claim for claim in bound['facts']['claims'] if claim['field'] == 'change_note']
        self.assertEqual([output['facts']['summary']] * 2, [claim['text'] for claim in changes])
        self.assertEqual([passage['quote'] for passage in change_passages], [claim['quote'] for claim in changes])
        self.assertEqual(release, ''.join(claim['quote'] for claim in changes))
        self.assertEqual(raw, output)

    def test_news_original_url_and_full_timestamp_gates_remain_authoritative(self):
        stamp = '2020-01-15T08:30:00+00:00'
        contexts = [dict(url=self.main, text='Published '+stamp+'. The product adds file export.'),
                    dict(url='https://secondary.example/story', text='Published '+stamp+'. A secondary account.')]
        passages = build_passages(contexts)
        primary, secondary = (row['id'] for row in passages)
        facts = dict(self.facts, category='AI 应用', open_source_status='unknown', event_date=stamp)
        output = dict(qualified=True, reason='Original announcement.', facts=facts,
                      evidence=dict(category=[primary], summary=[primary], usage_conditions=[primary],
                                    license=[], event_date=[primary]))
        record = dict(kind='news', url=self.main)
        bound = bind_review(output, passages, record)
        self.assertEqual(stamp, bound['facts']['event_date'])
        self.assertEqual(bound['facts'], pipeline._facts(bound, contexts, record))
        output['evidence']['event_date'] = [secondary]
        with self.assertRaisesRegex(pipeline.runtime.RuntimeError, 'event date lacks original'):
            pipeline._facts(bind_review(output, passages, record), contexts, record)
        output['evidence']['event_date'] = [primary]
        output['facts']['event_date'] = '2020-01-15'
        with self.assertRaisesRegex(pipeline.runtime.RuntimeError, 'event date lacks original'):
            pipeline._facts(bind_review(output, passages, record), contexts, record)


if __name__ == '__main__':
    unittest.main()
