from __future__ import annotations

import copy
import json
import unittest

from jsonschema import Draft202012Validator

from ai_notes import digest_reason_review as review
from ai_notes import digest_selection as selection
from ai_notes.digest_passages import build_passages
from ai_notes.digest_understanding import source_documents


SCORE_FIELDS = ('value', 'novelty', 'evidence', 'usability', 'interest')


def material_fixture():
    readme = 'https://docs.example/repository/README.md'
    install = 'https://docs.example/repository/install.md'
    licence = 'https://docs.example/repository/LICENSE_MIT_OUTSIDE_APP'
    contexts = [
        dict(url=readme, text='# Purpose\nTool searches local files.\n\n# Platforms\nWindows is supported.\n'),
        dict(url=install, text='# Desktop\nDownload a graphical desktop package.\n\n# Terminal\nNode is required only for the terminal route.\n'),
        dict(url=licence, text='MIT permission applies to files outside the application directory.\n'),
    ]
    for context in contexts:
        length = len(context['text'])
        context['source_scope'] = dict(schema_version='digest-source-scope.v1', coverage='excerpt',
            document_chars=length + 100, supplied_chars=length,
            ranges=[dict(start=10, end=10 + length)], document_sha256='a' * 64,
            reader='fixture', commit_sha='immutable123', file_path=context['url'].rsplit('/', 1)[-1])
    return dict(
        review_scope='public-introduction.v1', introduction_contract='discovery.v1',
        ranking_type='weekly', featured=False,
        candidate=dict(url='https://docs.example/repository', kind='project', human_label='HIDDEN_LABEL'),
        public_fields=dict(title='公开工具', summary='HIDDEN_PUBLIC_SUMMARY', source_url=readme),
        selection_basis=dict(
            precheck=dict(status='PASS', reasons=['用途清楚。', '读者可以理解。'], evidence_refs=[readme]),
            scores={field: dict(score=7, reason='支持 Windows；可搜索本地文件。', evidence_refs=[readme])
                    for field in SCORE_FIELDS},
            flags=[dict(code='usage_gap', reason='许可范围需要核对。', evidence_refs=[licence],
                        basis=dict(kind='licence_scope', claim='HIDDEN_BASIS_CLAIM',
                                   quote='HIDDEN_OUTSIDE_REF_QUOTE', evidence_url=install))],
            reason='整体推荐理由不能把限定许可扩展到整个应用。',
            decision='select', human_label='HIDDEN_LABEL', history=['HIDDEN_HISTORY']),
        passages=build_passages(contexts), source_documents=source_documents(contexts),
        understanding=dict(purpose='HIDDEN_UNDERSTANDING'), history=['HIDDEN_HISTORY'],
    )


def keys_recursive(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from keys_recursive(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from keys_recursive(nested)


class ReasonUnitIsolationTests(unittest.TestCase):
    def setUp(self):
        self.material = material_fixture()
        self.units = review.build_reason_units(self.material)
        self.by_field = {unit['field']: unit for unit in self.units}

    def test_contract_and_bounded_complete_unit_set_are_explicit(self):
        self.assertEqual('own-refs.v1', review.CONTRACT)
        self.assertEqual(13, review.MAX_REASON_UNITS)
        expected = ['selection_basis.precheck.reasons']
        expected += ['selection_basis.scores.' + field + '.reason' for field in SCORE_FIELDS]
        expected += ['selection_basis.flags.0.reason', 'selection_basis.reason']
        self.assertEqual(set(expected), {unit['field'] for unit in self.units})
        self.assertEqual(len(expected), len(self.units))
        self.assertEqual('selection_basis.reason', self.units[-1]['field'])

    def test_each_reason_gets_only_its_original_refs_and_every_passage_at_those_urls(self):
        unit = self.by_field['selection_basis.scores.value.reason']
        original = self.material['selection_basis']['scores']['value']
        self.assertEqual(original['reason'], unit['text'])
        self.assertEqual(original['evidence_refs'], unit['evidence_refs'])
        self.assertEqual('original_evidence_refs', unit['source_basis'])
        self.assertEqual([p for p in self.material['passages'] if p['evidence_url'] in unit['evidence_refs']],
                         unit['passages'])
        self.assertEqual([d for d in self.material['source_documents'] if d['evidence_url'] in unit['evidence_refs']],
                         unit['source_documents'])
        self.assertEqual(2, len(unit['passages']))
        self.assertEqual([], unit['missing_refs'])
        serialized = json.dumps(unit, ensure_ascii=False)
        self.assertNotIn('Download a graphical desktop package.', serialized)
        self.assertNotIn('MIT permission applies', serialized)

    def test_isolation_does_not_forward_public_prose_understanding_scores_or_labels(self):
        forbidden_keys = {'score', 'scores', 'decision', 'human_label', 'history', 'public_fields',
                          'summary', 'understanding', 'candidate', 'assessment', 'selection_basis'}
        for unit in self.units:
            with self.subTest(field=unit['field']):
                self.assertTrue(forbidden_keys.isdisjoint(set(keys_recursive(unit))))
                rendered = json.dumps(unit, ensure_ascii=False)
                for hidden in ('HIDDEN_LABEL', 'HIDDEN_HISTORY', 'HIDDEN_PUBLIC_SUMMARY',
                               'HIDDEN_UNDERSTANDING', 'HIDDEN_OUTSIDE_REF_QUOTE'):
                    self.assertNotIn(hidden, rendered)

    def test_precheck_group_and_flag_source_gaps_are_preserved_without_repair(self):
        precheck = self.by_field['selection_basis.precheck.reasons']
        self.assertEqual('\n'.join(self.material['selection_basis']['precheck']['reasons']), precheck['text'])
        flag = self.by_field['selection_basis.flags.0.reason']
        original = self.material['selection_basis']['flags'][0]
        self.assertEqual(original['reason'], flag['text'])
        self.assertEqual(original['evidence_refs'], flag['evidence_refs'])
        self.assertEqual([original['basis']['evidence_url']], flag['missing_refs'])
        self.assertNotIn(original['basis']['evidence_url'], [p['evidence_url'] for p in flag['passages']])

    def test_overall_is_last_with_explicit_derived_components_and_no_invented_own_refs(self):
        overall = self.units[-1]
        self.assertEqual('selection_basis.reason', overall['field'])
        self.assertEqual(self.material['selection_basis']['reason'], overall['text'])
        self.assertIsNone(overall['evidence_refs'])
        self.assertEqual('derived_component_sources', overall['source_basis'])
        expected_components = [dict(field=u['field'], text=u['text'], evidence_refs=u['evidence_refs'])
                               for u in self.units[:-1]]
        self.assertEqual(expected_components, overall['components'])
        refs = {url for component in expected_components for url in component['evidence_refs']}
        self.assertEqual([p for p in self.material['passages'] if p['evidence_url'] in refs], overall['passages'])
        self.assertEqual([d for d in self.material['source_documents'] if d['evidence_url'] in refs],
                         overall['source_documents'])
        self.assertTrue(any('OUTSIDE_APP' in d['scope']['file_path'] for d in overall['source_documents']))

    def test_unknown_refs_are_retained_as_gaps_including_empty_source_coverage(self):
        material = copy.deepcopy(self.material)
        missing = 'https://docs.example/not-captured.md'
        material['selection_basis']['scores']['interest']['evidence_refs'] = [missing]
        units = review.build_reason_units(material)
        unit = next(u for u in units if u['field'] == 'selection_basis.scores.interest.reason')
        self.assertEqual([missing], unit['evidence_refs'])
        self.assertEqual([missing], unit['missing_refs'])
        self.assertEqual([], unit['passages'])
        self.assertEqual([], unit['source_documents'])
        self.assertIn(missing, units[-1]['missing_refs'])

    def test_null_scores_and_empty_flags_do_not_create_fabricated_units(self):
        material = copy.deepcopy(self.material)
        material['selection_basis']['scores'] = None
        material['selection_basis']['flags'] = []
        units = review.build_reason_units(material)
        self.assertEqual(['selection_basis.precheck.reasons', 'selection_basis.reason'],
                         [u['field'] for u in units])
        self.assertEqual(1, len(units[-1]['components']))

    def test_unit_limit_fails_instead_of_dropping_original_reasons(self):
        material = copy.deepcopy(self.material)
        material['selection_basis']['flags'] = [copy.deepcopy(material['selection_basis']['flags'][0]) for _ in range(7)]
        with self.assertRaises(ValueError):
            review.build_reason_units(material)

    def test_projection_is_readonly_and_returned_nested_values_are_independent(self):
        before = copy.deepcopy(self.material)
        units = review.build_reason_units(self.material)
        self.assertEqual(before, self.material)
        units[0]['passages'][0]['heading_path'].append('return-only mutation')
        units[0]['source_documents'][0]['scope']['ranges'][0]['start'] = 999
        units[-1]['components'][0]['evidence_refs'].append('https://changed.example')
        self.assertEqual(before, self.material)


class ReasonReviewValidationTests(unittest.TestCase):
    def setUp(self):
        self.material = material_fixture()
        self.unit = next(u for u in review.build_reason_units(self.material)
                         if u['field'] == 'selection_basis.scores.value.reason')
        self.passage_id = self.unit['passages'][0]['id']

    def check(self, text=None, status='supported', passage_ids=None):
        return dict(text=self.unit['text'] if text is None else text, status=status,
                    passage_ids=[self.passage_id] if passage_ids is None else passage_ids,
                    reason='仅说明本段主张与自身来源的关系，不改分数。')

    def output(self, *, verdict='accept', checks=None):
        return dict(verdict=verdict, reason='已检查原理由的全部实际文字。',
                    checks=[self.check()] if checks is None else checks)

    def reject_unchanged(self, output, unit=None):
        before = copy.deepcopy(output)
        with self.assertRaises(ValueError):
            review.validate_review(output, self.unit if unit is None else unit)
        self.assertEqual(before, output)

    def test_closed_schema_is_valid_and_accept_does_not_mutate_or_rewrite_output(self):
        schema = review.review_schema(self.unit)
        Draft202012Validator.check_schema(schema)
        raw = self.output()
        before = copy.deepcopy(raw)
        result = review.validate_review(raw, self.unit)
        self.assertEqual(before, raw)
        self.assertEqual(raw, result)
        result['checks'][0]['passage_ids'].clear()
        self.assertEqual(before, raw)

    def test_semantic_defer_requires_a_negative_check_but_not_an_adjusted_score(self):
        for status, ids in [('not_supported', []), ('scope_conflict', [self.passage_id])]:
            raw = self.output(verdict='defer', checks=[self.check(status=status, passage_ids=ids)])
            self.assertEqual(raw, review.validate_review(raw, self.unit))

    def test_explicit_editorial_judgment_is_allowed_without_product_evidence(self):
        unit = copy.deepcopy(self.unit)
        unit['text'] = '对相关读者可能有探索价值。'
        raw = self.output(checks=[self.check(text=unit['text'], status='editorial_judgment', passage_ids=[])])
        self.assertEqual(raw, review.validate_review(raw, unit))

    def test_supported_and_scope_conflict_require_known_own_unit_ids(self):
        other_id = next(p['id'] for p in self.material['passages']
                        if p['evidence_url'] not in self.unit['evidence_refs'])
        for status in ('supported', 'scope_conflict'):
            for ids in ([], ['p_invented'], [other_id], [self.passage_id] * 2):
                with self.subTest(status=status, ids=ids):
                    self.reject_unchanged(self.output(verdict='defer' if status == 'scope_conflict' else 'accept',
                        checks=[self.check(status=status, passage_ids=ids)]))

    def test_span_must_be_a_literal_contiguous_slice_not_a_rewritten_reason(self):
        for text in ('支持Windows；可搜索本地文件。', 'Windows 支持文件搜索。', '不在原理由中', '', '   '):
            with self.subTest(text=text):
                self.reject_unchanged(self.output(checks=[self.check(text=text)]))

    def test_all_substantive_reason_characters_must_be_covered_without_needing_punctuation(self):
        good = self.output(checks=[self.check(text='支持 Windows'), self.check(text='可搜索本地文件')])
        self.assertEqual(good, review.validate_review(good, self.unit))
        for checks in ([], [self.check(text='支持 Windows')],
                       [self.check(text='支持 Windows'), self.check(text='可搜索')]):
            with self.subTest(checks=checks):
                self.reject_unchanged(self.output(checks=checks))

    def test_accept_negative_and_defer_without_negative_are_rejected(self):
        self.reject_unchanged(self.output(checks=[self.check(status='not_supported', passage_ids=[])]))
        self.reject_unchanged(self.output(verdict='defer'))
        self.reject_unchanged(self.output(verdict='accept', checks=[self.check(status='scope_conflict')]))

    def test_extra_missing_and_invalid_fields_are_not_silently_repaired(self):
        variants = [dict(self.output(), adjusted_score=None), dict(self.output(), verdict='select'),
                    dict(self.output(), reason='')]
        bad_check = self.check()
        bad_check['adjusted_score'] = None
        variants.append(self.output(checks=[bad_check]))
        for key in ('text', 'status', 'passage_ids', 'reason'):
            check = self.check()
            check.pop(key)
            variants.append(self.output(checks=[check]))
        variants.append(self.output(checks=[self.check(status='unknown')]))
        for raw in variants:
            with self.subTest(raw=raw):
                self.reject_unchanged(raw)

    def test_empty_passage_unit_can_report_missing_support_without_invented_ids(self):
        unit = copy.deepcopy(self.unit)
        unit['passages'] = []
        unit['source_documents'] = []
        unit['missing_refs'] = unit['evidence_refs'][:]
        schema = review.review_schema(unit)
        Draft202012Validator.check_schema(schema)
        raw = self.output(verdict='defer', checks=[self.check(status='not_supported', passage_ids=[])])
        self.assertEqual(raw, review.validate_review(raw, unit))
        self.reject_unchanged(self.output(), unit)


class ReasonReviewPolicyTests(unittest.TestCase):
    def setUp(self):
        # Reuse an existing modern, evidence-bound card fixture. The reason gate
        # is post-score policy and must not alter that model scoring material.
        from tests.test_digest_score_input import ScoreInputTests
        fixture = ScoreInputTests()
        fixture.setUp()
        self.prepared = copy.deepcopy(fixture.prepared)
        self.prepared['policy'].pop('reason_review_contract', None)

    def test_absent_marker_is_not_backfilled_and_policy_is_unchanged(self):
        before = copy.deepcopy(self.prepared['policy'])
        self.assertEqual(before, selection.validate_policy(self.prepared['policy']))
        self.assertEqual(before, self.prepared['policy'])
        self.assertNotIn('reason_review_contract', self.prepared['policy'])

    def test_explicit_marker_requires_all_compact_schema_dependencies(self):
        marked = copy.deepcopy(self.prepared)
        marked['policy']['reason_review_contract'] = review.CONTRACT
        self.assertEqual(marked['policy'], selection.validate_policy(marked['policy']))
        dependencies = ('score_input_contract', 'assessment_contract', 'scoring_projection',
                        'selection_refinement_contract', 'editorial_review_contract',
                        'understanding_contract', 'introduction_contract',
                        'source_reading_contract', 'editorial_scope')
        for field in dependencies:
            bad = copy.deepcopy(marked)
            bad['policy'].pop(field)
            with self.subTest(field=field):
                with self.assertRaises(selection.SelectionError):
                    selection.validate_policy(bad['policy'])
                with self.assertRaises(selection.SelectionError):
                    selection.build_scoring_input(bad, 'fixture')

    def test_unknown_marker_in_current_or_frozen_policy_is_rejected(self):
        for marker in (None, '', {}, 'own-refs.v3'):
            bad = copy.deepcopy(self.prepared)
            bad['policy']['reason_review_contract'] = marker
            before = copy.deepcopy(bad)
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(selection.SelectionError, 'unsupported reason review'):
                    selection.validate_policy(bad['policy'])
                with self.assertRaisesRegex(selection.SelectionError, 'unsupported reason review'):
                    selection.build_scoring_input(bad, 'fixture')
            self.assertEqual(before, bad)

    def test_new_post_score_marker_does_not_enter_or_change_scoring_material(self):
        old = selection.build_scoring_input(self.prepared, 'fixture')
        for contract in (review.CONTRACT, 'own-refs.v2'):
            marked = copy.deepcopy(self.prepared)
            marked['policy']['reason_review_contract'] = contract
            new = selection.build_scoring_input(marked, 'fixture')
            self.assertEqual(old, new)
            self.assertNotIn('reason_review_contract', set(keys_recursive(new)))
            self.assertNotIn(contract, json.dumps(new, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
