from __future__ import annotations

import copy
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from ai_notes import digest_output_schema as output_schema, digest_selection as selection


class ScoringOutputSchemaTests(unittest.TestCase):
    def setUp(self):
        self.policy = selection.load_policy(Path(__file__).resolve().parents[1])
        self.url = 'https://example.com/manual'
        self.card = {'ranking_type': 'daily', 'profile': 'practical', 'eligibility': {'state': 'available'},
            'material': {'kind': 'project'}, 'evidence_context': [{'url': self.url,
                'text': 'The setup instructions have not been published. This update fixes a typo.'}]}
        self.good = {'precheck': {'status': 'PASS', 'reasons': ['Concrete workflow.'], 'evidence_refs': [self.url]},
            'scores': {key: {'score': 7, 'reason': 'Source-backed judgment.', 'evidence_refs': [self.url]}
                       for key in self.policy['dimensions']}, 'flags': [], 'reason': 'Useful workflow with limits.'}

    def validator(self, card=None):
        schema = output_schema.assessment_schema(policy=self.policy, card=card or self.card)
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)

    def test_schema_accepts_assessment_without_mutating_frozen_inputs(self):
        before = copy.deepcopy((self.policy, self.card))
        self.assertTrue(self.validator().is_valid(self.good))
        self.assertEqual(before, (self.policy, self.card))
        self.assertEqual('accepted', selection.adapt_assessment(self.good, policy=self.policy, card=self.card)['status'])

    def test_extraneous_nulls_aliases_and_authority_fields_are_rejected_without_deletion(self):
        variants = []
        extra = copy.deepcopy(self.good)
        extra['scores']['value_note'] = None
        variants.append(extra)
        extra = copy.deepcopy(self.good)
        extra['scores']['value']['note'] = None
        variants.append(extra)
        extra = copy.deepcopy(self.good)
        extra['decision'] = 'select'
        variants.append(extra)
        extra = copy.deepcopy(self.good)
        extra['scores']['news_usability'] = extra['scores'].pop('usability')
        variants.append(extra)
        for output in variants:
            original = copy.deepcopy(output)
            with self.subTest(output=output):
                self.assertFalse(self.validator().is_valid(output))
                result = selection.adapt_assessment(output, policy=self.policy, card=self.card)
                self.assertEqual('rejected', result['status'])
                self.assertEqual(original, result['raw_output'])
                self.assertEqual(original, output)

    def test_closed_scores_require_all_dimensions_integer_scores_and_seen_urls(self):
        for change in ('missing', 'boolean', 'float', 'outside', 'url', 'blank'):
            value = copy.deepcopy(self.good)
            if change == 'missing':
                value['scores'].pop('interest')
            elif change == 'url':
                value['scores']['value']['evidence_refs'] = ['https://unseen.invalid']
            elif change == 'blank':
                value['scores']['value']['reason'] = '  '
            else:
                value['scores']['value']['score'] = {'boolean': True, 'float': 4.5, 'outside': 11}[change]
            with self.subTest(change=change):
                self.assertFalse(self.validator().is_valid(value))

    def test_unknown_allows_null_without_fake_evidence_but_pass_does_not(self):
        card = {**self.card, 'evidence_context': []}
        value = {**self.good, 'precheck': {'status': 'UNKNOWN', 'reasons': ['No readable source.'], 'evidence_refs': []}, 'scores': None}
        self.assertTrue(self.validator(card).is_valid(value))
        self.assertEqual('accepted', selection.adapt_assessment(value, policy=self.policy, card=card)['status'])
        value['precheck']['status'] = 'PASS'
        self.assertFalse(self.validator(card).is_valid(value))
        self.assertFalse(self.validator().is_valid(value))

    def test_flags_follow_actual_kind_and_ranking_scope(self):
        value = copy.deepcopy(self.good)
        flag = {'code': 'insufficient_usage_evidence', 'reason': 'Claim depends on missing steps.', 'evidence_refs': [self.url],
                'basis': {'kind': 'usage_evidence_gap', 'claim': 'The supplied steps are actionable.',
                          'quote': 'The setup instructions have not been published.', 'evidence_url': self.url},
                'gap': 'actionable_steps'}
        value['flags'] = [flag]
        self.assertTrue(self.validator().is_valid(value))
        self.assertEqual('accepted', selection.adapt_assessment(value, policy=self.policy, card=self.card)['status'])
        flag['gap'] = 'long_term_support'
        self.assertFalse(self.validator().is_valid(value))
        self.assertTrue(self.validator({**self.card, 'ranking_type': 'monthly'}).is_valid(value))
        self.assertFalse(self.validator({**self.card, 'material': {'kind': 'news'}}).is_valid(value))
        flag.pop('gap')
        flag['code'] = 'routine_update'
        flag['basis'] = {'kind': 'limited_increment', 'claim': 'Typo fix.', 'quote': 'This update fixes a typo.', 'evidence_url': self.url}
        self.assertFalse(self.validator().is_valid(value))
        self.assertTrue(self.validator({**self.card, 'material': {'kind': 'update'}}).is_valid(value))

    def test_schema_cannot_establish_quote_truth_and_does_not_replace_application_validation(self):
        value = copy.deepcopy(self.good)
        value['flags'] = [{'code': 'reader_mismatch', 'reason': 'Unsupported source reading.', 'evidence_refs': [self.url],
            'basis': {'kind': 'reader_requirement', 'claim': 'Only developers can use it.',
                      'quote': 'This text does not occur in the source.', 'evidence_url': self.url}}]
        self.assertTrue(self.validator().is_valid(value))
        adapted = selection.adapt_assessment(value, policy=self.policy, card=self.card)
        self.assertEqual('rejected', adapted['status'])
        self.assertIn('quote not found', adapted['error'])


if __name__ == '__main__':
    unittest.main()
