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

    def add_understanding(self):
        from ai_notes.digest_understanding import source_documents
        first = build_passages(self.contexts)[0]
        self.facts['understanding'] = {
            'schema_version': 'digest-understanding.v1',
            'purpose': {'text': 'Documented desktop route.', 'passage_ids': [first['id']]},
            'input': {'text': None, 'passage_ids': []}, 'output': {'text': None, 'passage_ids': []},
            'operations': [], 'conditions': [], 'unknowns': ['The short excerpt does not identify inputs and outputs.'],
            'proof_map': {first['id']: {key: first[key] for key in ('evidence_url', 'quote', 'heading_path')}},
            'source_documents': source_documents(self.contexts), 'reading_scope_issues': [],
        }

    def understood(self, marker='project-reading.v1'):
        return editorial.build_review_input(record=self.record, facts=self.facts, assessment=self.assessment,
            contexts=self.contexts, ranking_type='weekly', understanding_contract=marker)

    def test_understanding_opt_in_reads_every_actual_passage_not_just_card_citations(self):
        self.add_understanding()
        result = self.understood()
        cited = set(result['facts']['understanding']['purpose']['passage_ids'])
        self.assertTrue(any(row['id'] not in cited for row in result['passages']))
        self.assertEqual(self.contexts[0]['text'], ''.join(row['quote'] for row in result['passages']))
        self.assertIn('GPU acceleration can be enabled.', result['passages'][-1]['quote'])
        self.assertNotIn('proof_map', result['facts']['understanding'])
        self.assertNotIn('source_documents', result['facts']['understanding'])
        self.assertEqual(self.facts['understanding']['source_documents'], result['source_documents'])

    def test_unmarked_editorial_input_remains_exact_even_when_facts_contain_understanding(self):
        before = self.build()
        self.facts['understanding'] = {'invalid': 'must not enable a contract via model data'}
        self.assertEqual(json.dumps(before, sort_keys=True), json.dumps(self.build(), sort_keys=True))
        self.assertNotIn('source_documents', self.build())

    def test_project_reading_input_deep_copies_ranges_and_unknowns(self):
        length = len(self.contexts[0]['text'])
        self.contexts[0]['source_scope'] = dict(schema_version='digest-source-scope.v1', coverage='excerpt',
            document_chars=10000, supplied_chars=length, ranges=[dict(start=100,end=100+length)],
            document_sha256='a' * 64, reader='fixture', commit_sha='abc123', file_path='README.md')
        self.add_understanding()
        before = copy.deepcopy((self.facts, self.contexts))
        result = self.understood()
        self.assertEqual('excerpt', result['source_documents'][0]['scope']['coverage'])
        result['source_documents'][0]['scope']['ranges'][0]['start'] = 1
        result['facts']['understanding']['unknowns'].clear()
        self.assertEqual(before, (self.facts, self.contexts))

    def test_project_reading_rejects_unknown_contract_missing_card_or_stale_source(self):
        for marker in ('', 'project-reading.v2', {}):
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, 'unsupported understanding'):
                self.understood(marker)
        with self.assertRaises(ValueError):
            self.understood()
        self.add_understanding()
        self.contexts[0]['text'] += 'A newly captured condition changes passage IDs.'
        with self.assertRaises(ValueError):
            self.understood()

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

    def test_public_position_is_policy_only_closed_and_copied(self):
        position=dict(schema_version='digest-editorial-position.v1',audience=['普通工具使用者'],priorities=['具体用途与认知价值'])
        self.record['editorial_position']={'forced_label':'select'}
        old=self.build()
        result=editorial.build_review_input(record=self.record,facts=self.facts,assessment=self.assessment,
            contexts=self.contexts,ranking_type='weekly',editorial_position=position)
        self.assertNotIn('editorial_position',old)
        self.assertNotIn('editorial_position',result['candidate'])
        self.assertEqual(position,result['editorial_position'])
        result['editorial_position']['audience'].append('returned copy change')
        self.assertEqual(['普通工具使用者'],position['audience'])
        for bad in (dict(position, human_label='select'), dict(position, audience=[])):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                editorial.build_review_input(record=self.record,facts=self.facts,assessment=self.assessment,
                    contexts=self.contexts,ranking_type='weekly',editorial_position=bad)
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

    def public(self, *, ranking_type='weekly', featured=False, **kwargs):
        return editorial.build_review_input(record=self.record, facts=self.facts, assessment=self.assessment,
            contexts=self.contexts, ranking_type=ranking_type, introduction_contract='discovery.v1',
            review_scope=editorial.PUBLIC_INTRODUCTION_SCOPE, featured=featured, **kwargs)

    def test_public_scope_excludes_unpublished_prose_and_understanding_but_keeps_sources(self):
        self.add_understanding()
        self.facts.update(usage_conditions='UNPUBLISHED_CONDITION', audience='UNPUBLISHED_AUDIENCE',
                          open_source_status='UNPUBLISHED_LICENCE', supported_systems='Windows')
        self.facts['understanding']['unknowns'] = ['UNPUBLISHED_INTERPRETATION']
        before = copy.deepcopy((self.record, self.facts, self.assessment, self.contexts))
        result = self.public(understanding_contract='project-reading.v1')
        self.assertEqual({'title', 'category', 'summary', 'url', 'supported_systems'}, set(result['public_fields']))
        self.assertNotIn('facts', result)
        self.assertNotIn('assessment', result)
        self.assertNotIn('understanding', result)
        self.assertNotIn('UNPUBLISHED_', json.dumps(result))
        self.assertEqual(self.contexts[0]['text'], ''.join(row['quote'] for row in result['passages']))
        self.assertEqual(self.facts['understanding']['source_documents'], result['source_documents'])
        self.assertEqual(before, (self.record, self.facts, self.assessment, self.contexts))

    def test_weekly_detail_is_reviewed_only_for_real_featured_items(self):
        self.assertNotIn('detail', self.public()['public_fields'])
        self.assertEqual(self.facts['detail'], self.public(featured=True)['public_fields']['detail'])
        self.assertNotIn('detail', self.public(ranking_type='daily', featured=True)['public_fields'])
        self.facts['detail'] = ''
        self.public(featured=False)
        with self.assertRaisesRegex(ValueError, 'nonempty detail'):
            self.public(featured=True)

    def test_monthly_retention_and_verified_update_increment_are_reviewed(self):
        self.record.update(kind='update', change_note='Adds documented batch rename.',
                           event=dict(url=self.url + '/releases/v2', type='update', occurred_on='2026-10-05',
                                      date_precision='date', timezone='unknown'))
        result = self.public(ranking_type='monthly', featured=True)
        self.assertEqual(self.facts['retention_reason'], result['public_fields']['retention_reason'])
        self.assertEqual(self.record['change_note'], result['public_fields']['change_note'])
        self.assertEqual(self.record['event']['url'], result['public_fields']['event_url'])
        self.assertNotIn('detail', result['public_fields'])
        self.facts['retention_reason'] = ''
        with self.assertRaisesRegex(ValueError, 'nonempty retention_reason'):
            self.public(ranking_type='monthly')

    def test_news_event_date_matches_actual_beijing_display_not_hidden_fact_date(self):
        self.record.update(kind='news', event=dict(url=self.url, type='news',
            occurred_at='2026-10-04T20:30:00+00:00', date_precision='timestamp', timezone='UTC'))
        self.facts.update(event_date='2026-10-04T20:30:00+00:00', supported_systems='HIDDEN_SYSTEM')
        result = self.public(ranking_type='daily')
        self.assertEqual('2026-10-05', result['public_fields']['event_date'])
        self.assertNotIn('supported_systems', result['public_fields'])
        self.record['event'] = dict(url=self.url, type='news', occurred_on='2026-10-04',
                                    date_precision='date', timezone='unknown')
        self.assertEqual('2026-10-04（原文仅日期，时区未知；未确认具体时刻）',
                         self.public()['public_fields']['event_date'])

    def test_reading_attribution_is_public_but_platform_and_detail_are_not(self):
        self.record['kind'] = 'reading'
        self.facts.update(author='Original interviewer', original_date='2026-10-04', supported_systems='HIDDEN_SYSTEM')
        result = self.public()
        self.assertEqual('Original interviewer', result['public_fields']['author'])
        self.assertEqual('2026-10-04', result['public_fields']['original_date'])
        self.assertNotIn('supported_systems', result['public_fields'])
        self.assertNotIn('detail', result['public_fields'])
        with self.assertRaisesRegex(ValueError, 'featured reading'):
            self.public(featured=True)

    def test_public_scope_keeps_score_explanations_and_source_refs_reviewable(self):
        self.assessment['scores']['usability']['reason'] = 'Licence supposedly restricts the supported Windows route.'
        result = self.public()
        self.assertEqual(self.build()['assessment'], result['selection_basis'])
        self.assertEqual(self.assessment['scores']['usability']['reason'],
                         result['selection_basis']['scores']['usability']['reason'])
        self.assertNotIn('HIDDEN_HISTORY', json.dumps(result))
        raw = dict(verdict='defer', reason='The score explanation invents a documented condition.', issues=[dict(
            field='selection_basis.scores.usability.reason', code='unsupported_assertion',
            passage_ids=[result['passages'][0]['id']], reason='The supplied desktop passage gives no such licence restriction.')])
        self.assertEqual(raw, editorial.validate_review(raw, result['passages']))
        raw['issues'][0]['field'] = 'public_fields.supported_systems'
        raw['issues'][0]['reason'] = 'The supplied passage does not establish Windows support.'
        self.assertEqual(raw, editorial.validate_review(raw, result['passages']))

    def test_public_scope_is_explicit_requires_discovery_and_rejects_bad_inputs(self):
        self.record['review_scope'] = editorial.PUBLIC_INTRODUCTION_SCOPE
        self.facts['review_scope'] = editorial.PUBLIC_INTRODUCTION_SCOPE
        old = editorial.build_review_input(record=self.record, facts=self.facts, assessment=self.assessment,
            contexts=self.contexts, ranking_type='weekly', introduction_contract='discovery.v1')
        self.assertNotIn('public_fields', old)
        for scope in ('', 'public-introduction.v2', {}):
            with self.subTest(scope=scope), self.assertRaisesRegex(ValueError, 'unsupported editorial review scope'):
                editorial.build_review_input(record=self.record, facts=self.facts, assessment=self.assessment,
                    contexts=self.contexts, ranking_type='weekly', introduction_contract='discovery.v1', review_scope=scope)
        with self.assertRaisesRegex(ValueError, 'requires discovery'):
            editorial.build_review_input(record=self.record, facts=self.facts, assessment=self.assessment,
                contexts=self.contexts, ranking_type='weekly', review_scope=editorial.PUBLIC_INTRODUCTION_SCOPE)
        with self.assertRaisesRegex(ValueError, 'must be a boolean'):
            self.public(featured=1)

    def test_public_scope_outputs_are_independent_and_null_systems_are_not_guessed(self):
        self.facts['usage_conditions'] = 'Runs on Linux according to an unpublished interpretation.'
        self.facts['supported_systems'] = None
        self.add_understanding()
        before = copy.deepcopy((self.record, self.facts, self.assessment, self.contexts))
        result = self.public(understanding_contract='project-reading.v1')
        self.assertIsNone(result['public_fields']['supported_systems'])
        result['selection_basis']['flags'][0]['basis']['claim'] = 'Return-only change.'
        result['source_documents'][0]['scope']['ranges'].append({'start': 0, 'end': 1})
        result['public_fields']['summary'] = 'Return-only summary.'
        result['passages'][0]['heading_path'].clear()
        self.assertEqual(before, (self.record, self.facts, self.assessment, self.contexts))

    def test_public_projection_matches_discovery_renderer_conditional_parts(self):
        from ai_notes import digest
        for ranking_type, featured in (('daily', False), ('weekly', True), ('monthly', True)):
            with self.subTest(ranking_type=ranking_type, featured=featured):
                item = dict(self.facts, url=self.url, kind='project', featured=featured,
                            supported_systems='Windows', evidence_urls=[self.url], change_note='')
                self.facts['supported_systems'] = 'Windows'
                projected = self.public(ranking_type=ranking_type, featured=featured)['public_fields']
                rendered = digest._render_discovery(dict(items=[item], ranking_type=ranking_type), draft=True, preview=True)
                for field in ('summary', 'detail', 'retention_reason', 'supported_systems'):
                    if field in projected:
                        self.assertIn(projected[field], rendered)
                    elif field in self.facts and self.facts[field] is not None:
                        self.assertNotIn(self.facts[field], rendered)


if __name__ == '__main__':
    unittest.main()
