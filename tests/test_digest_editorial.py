from __future__ import annotations

import copy
import json
import unittest

from jsonschema import Draft202012Validator

from ai_notes import digest_editorial as editorial
from ai_notes.digest_passages import build_passages


class EditorialReviewTests(unittest.TestCase):
    def setUp(self):
        self.contexts = [dict(url='https://docs.example/install', text=(
            '# Minimum requirements\nmacOS 13 and Apple Silicon.\n\n'
            '# macOS builds\nIntel builds can run on macOS 12; choose the matching package.\n'))]
        self.passages = build_passages(self.contexts)
        self.issue = {'field': 'facts.usage_conditions', 'code': 'source_conflict',
                      'passage_ids': [row['id'] for row in self.passages],
                      'reason': '摘要统一成 macOS 12，未说明最低要求与构建分节的不同限定。'}

    def test_accept_requires_empty_issues_and_preserves_raw(self):
        raw = {'verdict': 'accept', 'reason': '在已提供的文字范围内未发现具体错误。', 'issues': []}
        before = copy.deepcopy(raw)
        schema = editorial.review_schema(self.passages)
        Draft202012Validator.check_schema(schema)
        result = editorial.validate_review(raw, self.passages)
        self.assertEqual(before, raw)
        self.assertEqual(raw, result)
        result['reason'] = 'Changed return only.'
        self.assertEqual(before, raw)

    def test_defer_requires_a_concrete_issue_with_known_evidence(self):
        raw = {'verdict': 'defer', 'reason': '使用条件需保留来源差异。', 'issues': [self.issue]}
        before = copy.deepcopy(raw)
        result = editorial.validate_review(raw, self.passages)
        self.assertEqual(raw, result)
        result['issues'][0]['passage_ids'].clear()
        self.assertEqual(before, raw)

    def test_accept_with_issues_and_defer_without_issues_are_rejected(self):
        for raw in ({'verdict': 'accept', 'reason': 'Looks fine.', 'issues': [self.issue]},
                    {'verdict': 'defer', 'reason': 'Just a preference.', 'issues': []}):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, 'invalid editorial review'):
                editorial.validate_review(raw, self.passages)

    def test_bad_codes_empty_reasons_and_unknown_repeated_ids_are_rejected(self):
        variants = [dict(code='adjust_score'), dict(field=' '), dict(reason=''),
                    dict(passage_ids=[]), dict(passage_ids=['p_invented']),
                    dict(passage_ids=[self.passages[0]['id']] * 2)]
        for change in variants:
            with self.subTest(change=change):
                raw = {'verdict': 'defer', 'reason': 'Source-backed issue.', 'issues': [dict(self.issue, **change)]}
                before = copy.deepcopy(raw)
                with self.assertRaises(ValueError):
                    editorial.validate_review(raw, self.passages)
                self.assertEqual(before, raw)

    def test_extra_fields_or_proposed_corrections_are_never_silently_dropped(self):
        variants = []
        extra = {'verdict': 'defer', 'reason': 'Condition mismatch.', 'issues': [copy.deepcopy(self.issue)],
                 'corrected_facts': None}
        variants.append(extra)
        extra = {'verdict': 'defer', 'reason': 'Condition mismatch.', 'issues': [dict(self.issue, corrected_score=None)]}
        variants.append(extra)
        variants.append({'verdict': 'select', 'reason': 'High value.', 'issues': []})
        for raw in variants:
            with self.subTest(raw=raw):
                before = copy.deepcopy(raw)
                with self.assertRaises(ValueError):
                    editorial.validate_review(raw, self.passages)
                self.assertEqual(before, raw)

    def test_cross_snapshot_ids_cannot_support_a_defer(self):
        changed = build_passages([dict(self.contexts[0], text=self.contexts[0]['text'] + 'New restriction.')])
        raw = {'verdict': 'defer', 'reason': 'Condition mismatch.',
               'issues': [dict(self.issue, passage_ids=[changed[0]['id']])]}
        with self.assertRaises(ValueError):
            editorial.validate_review(raw, self.passages)

    def test_duplicate_passage_id_is_rejected_before_schema_use(self):
        with self.assertRaisesRegex(ValueError, 'unique'):
            editorial.review_schema(self.passages + [copy.deepcopy(self.passages[0])])

    def test_schema_does_not_claim_to_verify_a_verdict_semantically(self):
        # The gate checks IDs and shape; correctness of an accepted statement
        # still needs an independent content evaluation, not a regex promise.
        raw = {'verdict': 'accept', 'reason': 'This is a model judgment, not proof.', 'issues': []}
        self.assertEqual(raw, editorial.validate_review(raw, self.passages))


class EditorialInputTests(unittest.TestCase):
    def setUp(self):
        self.url = 'https://docs.example/tool'
        self.record = dict(url=self.url, title='Tool', category='开源项目', kind='project',
                           published_at=None, change_note='', old_score='HIDDEN_HISTORY',
                           human_label='HIDDEN_HISTORY', selection_history=['HIDDEN_HISTORY'])
        self.facts = dict(title='工具', category='开源项目', summary='整理文件。', reason='能减少重复操作。',
                          audience='需要整理文件的读者', usage_conditions='桌面安装包可用；加速功能可选。',
                          detail='批量处理具有复用价值。', retention_reason='反复整理文件时可使用。',
                          open_source_status='confirmed', evidence_urls=[self.url],
                          claims=[dict(quote='Duplicated legacy quote, not needed twice.')],
                          human_label='HIDDEN_HISTORY')
        self.assessment = dict(precheck=dict(status='PASS', reasons=['用途清楚。'], evidence_refs=[self.url],
                                             gold='HIDDEN_HISTORY'),
                               scores={field: dict(score=7, reason='基于用途和使用条件的编辑判断。', evidence_refs=[self.url],
                                                   historical_score='HIDDEN_HISTORY')
                                       for field in ('value', 'novelty', 'evidence', 'usability', 'interest')},
                               flags=[dict(code='insufficient_usage_evidence', reason='需要明确长期主张的支持范围。',
                                           evidence_refs=[self.url], gap='long_term_support', human_note='HIDDEN_HISTORY',
                                           basis=dict(kind='usage_gap', claim='未经证明的长期主张。', quote='Current release.',
                                                      evidence_url=self.url, human_gold='HIDDEN_HISTORY'))],
                               reason='这是待核对的当前评分解释。', old_results=['HIDDEN_HISTORY'], decision='select')
        self.contexts = [dict(url=self.url, text='# Desktop\nA desktop package is available.\n\n# Optional features\nGPU acceleration can be enabled.\n',
                              fetched_at='2026-10-04T01:00:00+08:00')]

    def build(self):
        return editorial.build_review_input(record=self.record, facts=self.facts, assessment=self.assessment,
                                             contexts=self.contexts, ranking_type='weekly')

    def test_all_fact_prose_and_current_score_explanations_are_retained(self):
        result = self.build()
        self.assertEqual(set(self.facts) - {'evidence_urls', 'claims', 'human_label'}, set(result['facts']))
        for field, text in result['facts'].items():
            self.assertEqual(self.facts[field], text)
        self.assertEqual(self.assessment['reason'], result['assessment']['reason'])
        for field in self.assessment['scores']:
            self.assertEqual(self.assessment['scores'][field]['reason'], result['assessment']['scores'][field]['reason'])
        self.assertEqual(self.assessment['flags'][0]['basis']['claim'], result['assessment']['flags'][0]['basis']['claim'])
        self.assertEqual('supplied_text_only_not_full_document_or_software_test', result['evidence_scope'])

    def test_historical_scores_labels_and_ledger_authority_do_not_leak(self):
        result = self.build()
        self.assertNotIn('HIDDEN_HISTORY', json.dumps(result))
        self.assertNotIn('decision', result['assessment'])
        self.assertNotIn('claims', result['facts'])
        self.assertNotIn('evidence_context', result)
        self.assertEqual({'ranking_type', 'candidate', 'facts', 'assessment', 'passages', 'evidence_scope'}, set(result))

    def test_complete_supplied_text_and_heading_context_are_not_truncated(self):
        self.contexts[0]['text'] += ('Long but real supplied condition.\n\n' * 700) + 'Final exception must remain.'
        result = self.build()
        self.assertEqual(self.contexts[0]['text'], ''.join(row['quote'] for row in result['passages']))
        self.assertIn('Final exception must remain.', result['passages'][-1]['quote'])
        self.assertEqual(['Optional features'], result['passages'][-1]['heading_path'])

    def test_build_is_readonly_and_returned_nested_values_are_independent(self):
        before = copy.deepcopy((self.record, self.facts, self.assessment, self.contexts))
        result = self.build()
        self.assertEqual(before, (self.record, self.facts, self.assessment, self.contexts))
        result['assessment']['precheck']['reasons'].clear()
        result['assessment']['flags'][0]['basis']['claim'] = 'Changed returned value.'
        result['facts']['usage_conditions'] = 'Changed returned conditions.'
        result['passages'][0]['heading_path'].clear()
        self.assertEqual(before, (self.record, self.facts, self.assessment, self.contexts))

    def test_optional_reader_context_is_closed_copied_and_not_backfilled(self):
        context=dict(schema_version='digest-reader-context.v1',background=['不太会代码'],
                     exploration_interests=['信息筛选和周报'])
        old=self.build()
        result=editorial.build_review_input(record=self.record,facts=self.facts,assessment=self.assessment,
            contexts=self.contexts,ranking_type='weekly',reader_context=context)
        self.assertNotIn('reader_context',old)
        self.assertEqual(context,result['reader_context'])
        result['reader_context']['exploration_interests'].append('Return-only change')
        self.assertEqual(['信息筛选和周报'],context['exploration_interests'])
        context['selection_gold']='select'
        with self.assertRaises(ValueError):
            editorial.build_review_input(record=self.record,facts=self.facts,assessment=self.assessment,
                contexts=self.contexts,ranking_type='weekly',reader_context=context)
    def test_date_precision_and_news_event_fields_are_retained_without_event_metadata(self):
        self.record.update(kind='news', event=dict(id='HIDDEN_HISTORY', url=self.url, type='news',
                            occurred_on='2020-01-15', date_precision='date', timezone='unknown', labels='HIDDEN_HISTORY'))
        self.facts['event_date'] = '2020-01-15'
        result = self.build()
        self.assertEqual('2020-01-15', result['facts']['event_date'])
        self.assertEqual('unknown', result['candidate']['event']['timezone'])
        self.assertNotIn('occurred_at', result['candidate']['event'])
        self.assertNotIn('HIDDEN_HISTORY', json.dumps(result))

    def test_null_scores_and_empty_flags_preserve_their_real_state(self):
        self.assessment.update(scores=None, flags=[])
        result = self.build()
        self.assertIsNone(result['assessment']['scores'])
        self.assertEqual([], result['assessment']['flags'])

    def test_missing_source_text_cannot_produce_a_review_input(self):
        self.contexts = []
        with self.assertRaisesRegex(ValueError, 'requires supplied original text'):
            self.build()


if __name__ == '__main__':
    unittest.main()
