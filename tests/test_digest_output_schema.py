from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from ai_notes import digest_output_schema as output_schema, digest_selection as selection


class ScoringOutputSchemaTests(unittest.TestCase):
    def setUp(self):
        self.policy = selection.load_policy(Path(__file__).resolve().parents[1])
        self.policy.pop('score_input_contract', None)
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


class CompactScoringSchemaTests(unittest.TestCase):
    def setUp(self):
        fixture = ScoringOutputSchemaTests()
        fixture.setUp()
        self.policy, self.card, self.good, self.url = fixture.policy, fixture.card, fixture.good, fixture.url

    def schemas(self, card=None, policy=None):
        policy = copy.deepcopy(policy or self.policy)
        policy.pop('score_input_contract', None)
        legacy = output_schema.assessment_schema(policy=policy, card=card or self.card)
        policy['score_input_contract'] = output_schema.COMPACT_SCHEMA_CONTRACT
        compact = output_schema.assessment_schema(policy=policy, card=card or self.card)
        Draft202012Validator.check_schema(legacy)
        Draft202012Validator.check_schema(compact)
        return legacy, compact

    def expand(self, compact):
        def resolve(value):
            if isinstance(value, dict):
                if set(value) == {'$ref'}:
                    path = value['$ref']
                    self.assertTrue(path.startswith('#/$defs/'))
                    return resolve(compact['$defs'][path.removeprefix('#/$defs/')])
                return {key: resolve(item) for key, item in value.items() if key != '$defs'}
            if isinstance(value, list):
                return [resolve(item) for item in value]
            return value
        return resolve(compact)

    def test_unmarked_schema_is_exactly_the_pre_compaction_value(self):
        legacy, _ = self.schemas()
        encoded = json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        self.assertEqual('bbf16b610c93578f5b3327675dc59d4d8ca79dd8064ecde7ee643bb9fd90eb1a',
                         hashlib.sha256(encoded.encode()).hexdigest())
        self.assertNotIn('$defs', legacy)
        card = copy.deepcopy(self.card)
        card['material']['score_input_contract'] = output_schema.COMPACT_SCHEMA_CONTRACT
        self.assertEqual(legacy, output_schema.assessment_schema(policy=self.policy, card=card))

    def test_local_reference_expansion_preserves_every_kind_and_ranking_constraint(self):
        before = copy.deepcopy((self.policy, self.card))
        for kind in ('project', 'update', 'news', 'reading'):
            for ranking_type in ('daily', 'weekly', 'monthly'):
                for contexts in (self.card['evidence_context'], [],
                                 self.card['evidence_context'] * 2):
                    card = {**self.card, 'ranking_type': ranking_type,
                            'material': {'kind': kind}, 'evidence_context': contexts}
                    with self.subTest(kind=kind, ranking_type=ranking_type, contexts=len(contexts)):
                        legacy, compact = self.schemas(card)
                        self.assertEqual(legacy, self.expand(compact))
        self.assertEqual(before, (self.policy, self.card))

    def test_shared_definitions_reduce_wire_without_changing_sources(self):
        before = copy.deepcopy(self.card)
        legacy, compact = self.schemas()
        wire = json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        self.assertLess(len(wire), len(json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(',', ':'))))
        self.assertEqual(1, wire.count(self.url))
        self.assertEqual({'$ref': '#/$defs/score'},
                         compact['properties']['scores']['anyOf'][0]['properties']['value'])
        self.assertEqual({'$ref': '#/$defs/evidence_url'},
                         compact['$defs']['score']['properties']['evidence_refs']['items'])
        self.assertFalse(compact['$defs']['score']['additionalProperties'])
        self.assertEqual(before, self.card)

    def test_closed_output_integer_and_reference_validation_remains_equivalent(self):
        legacy, compact = self.schemas()
        variants = [('valid', copy.deepcopy(self.good), True)]
        for change in ('missing_top', 'extra_top', 'missing_score', 'extra_score', 'boolean',
                       'float', 'outside', 'url', 'empty_refs', 'blank', 'pass_null'):
            value = copy.deepcopy(self.good)
            if change == 'missing_top':
                value.pop('reason')
            elif change == 'extra_top':
                value['reason_note'] = None
            elif change == 'missing_score':
                value['scores'].pop('interest')
            elif change == 'extra_score':
                value['scores']['value']['extra'] = None
            elif change == 'url':
                value['scores']['value']['evidence_refs'] = ['https://unseen.invalid']
            elif change == 'empty_refs':
                value['scores']['value']['evidence_refs'] = []
            elif change == 'blank':
                value['scores']['value']['reason'] = ' '
            elif change == 'pass_null':
                value['scores'] = None
            else:
                value['scores']['value']['score'] = {'boolean': True, 'float': 4.5, 'outside': 11}[change]
            variants.append((change, value, False))
        unknown = copy.deepcopy(self.good)
        unknown.update(precheck={'status': 'UNKNOWN', 'reasons': ['No supplied basis.'], 'evidence_refs': []}, scores=None)
        variants.append(('unknown_null', unknown, True))
        for name, value, expected in variants:
            before = copy.deepcopy(value)
            with self.subTest(name=name):
                self.assertEqual(expected, Draft202012Validator(legacy).is_valid(value))
                self.assertEqual(expected, Draft202012Validator(compact).is_valid(value))
                self.assertEqual(before, value)

    def test_flags_keep_basis_url_gap_kind_and_closed_shape(self):
        flag = {'code': 'insufficient_usage_evidence', 'reason': 'A supplied use claim lacks steps.',
                'evidence_refs': [self.url], 'gap': 'actionable_steps',
                'basis': {'kind': 'usage_evidence_gap', 'claim': 'A usable workflow.',
                          'quote': 'The setup instructions have not been published.', 'evidence_url': self.url}}
        for change, expected in (('valid', True), ('missing_basis', False), ('extra_null', False),
                                 ('unseen_basis_url', False), ('unknown_code', False), ('wrong_gap', False)):
            value = copy.deepcopy(self.good)
            current = copy.deepcopy(flag)
            if change == 'missing_basis':
                current.pop('basis')
            elif change == 'extra_null':
                current['extra'] = None
            elif change == 'unseen_basis_url':
                current['basis']['evidence_url'] = 'https://unseen.invalid'
            elif change == 'unknown_code':
                current['code'] = 'fabricated'
            elif change == 'wrong_gap':
                current['gap'] = 'long_term_support'
            value['flags'] = [current]
            legacy, compact = self.schemas()
            with self.subTest(change=change):
                self.assertEqual(expected, Draft202012Validator(legacy).is_valid(value))
                self.assertEqual(expected, Draft202012Validator(compact).is_valid(value))
        value = {**self.good, 'flags': [flag]}
        for card, expected in (({**self.card, 'material': {'kind': 'news'}}, False),
                               ({**self.card, 'ranking_type': 'monthly'}, True)):
            legacy, compact = self.schemas(card)
            self.assertEqual(expected, Draft202012Validator(legacy).is_valid(value))
            self.assertEqual(expected, Draft202012Validator(compact).is_valid(value))

    def test_no_source_policy_preserves_the_false_reference_and_null_scores_branch(self):
        card = {**self.card, 'evidence_context': []}
        legacy, compact = self.schemas(card)
        self.assertEqual(legacy, self.expand(compact))
        self.assertEqual(False, compact['properties']['precheck']['properties']['evidence_refs']['items'])
        self.assertEqual({'type': 'null'}, compact['properties']['scores'])
        value = copy.deepcopy(self.good)
        value.update(precheck={'status': 'UNKNOWN', 'reasons': ['No readable source.'], 'evidence_refs': []}, scores=None)
        self.assertTrue(Draft202012Validator(compact).is_valid(value))
        value['precheck']['status'] = 'PASS'
        self.assertFalse(Draft202012Validator(compact).is_valid(value))


class SourceReviewSchemaTests(unittest.TestCase):
    def setUp(self):
        self.passages = [
            {'id': 'passage-01', 'evidence_url': 'https://example.com/manual',
             'quote': 'Import a local file and save the result.', 'heading_path': ['Usage']},
            {'id': 'passage-02', 'evidence_url': 'https://example.com/license',
             'quote': 'MIT License.', 'heading_path': ['License']},
        ]

    def schema(self, kind='project', passages=None):
        schema = output_schema.source_review_schema(record={'kind': kind},
            passages=self.passages if passages is None else passages)
        Draft202012Validator.check_schema(schema)
        return schema

    def valid(self, value, kind='project', passages=None):
        return Draft202012Validator(self.schema(kind, passages)).is_valid(value)

    def sample(self, kind='project'):
        facts = dict(title='Local organizer', category='开源项目', summary='Organizes local files.',
                     reason='A documented task.', audience='People organizing files.',
                     usage_conditions='Import a local file.', detail='Save the result to disk.',
                     retention_reason='File organization is reusable.', open_source_status='confirmed')
        evidence = {name: ['passage-01'] for name in ('category', 'summary', 'usage_conditions')}
        evidence['license'] = ['passage-02']
        if kind == 'reading':
            facts.update(category='博客、帖子与访谈', author='An author', original_date='2026-10-04')
            evidence.update(author=['passage-01'], original_date=['passage-01'])
        elif kind == 'news':
            facts.update(category='AI 应用', event_date='2026-10-04')
            evidence['event_date'] = ['passage-01']
        elif kind == 'update':
            evidence['change_note'] = ['passage-01']
        return dict(qualified=True, reason='Original passages support the facts.', facts=facts, evidence=evidence)

    def test_valid_samples_for_all_kinds_preserve_program_owned_passages(self):
        original = copy.deepcopy(self.passages)
        for kind in ('project', 'update', 'reading', 'news'):
            with self.subTest(kind=kind):
                self.assertTrue(self.valid(self.sample(kind), kind))
        self.assertEqual(original, self.passages)

    def test_ids_are_defined_once_and_referenced_without_urls_quotes_or_scope(self):
        schema = self.schema('reading')
        self.assertEqual(['passage-01', 'passage-02'], schema['$defs']['passage_id']['enum'])
        for name, definition in schema['$defs']['evidence']['properties'].items():
            self.assertEqual({'$ref': '#/$defs/passage_id'}, definition['items'], name)
        wire = json.dumps(schema)
        self.assertEqual(1, wire.count('passage-01'))
        self.assertNotIn('https://example.com', wire)
        self.assertEqual({'qualified', 'reason', 'facts', 'evidence'}, set(schema['properties']))
        self.assertFalse(schema['additionalProperties'])
        self.assertFalse(schema['$defs']['facts']['additionalProperties'])
        self.assertFalse(schema['$defs']['evidence']['additionalProperties'])

    def test_extra_null_fields_rejected_at_every_output_object(self):
        for path in ('root', 'facts', 'evidence'):
            value = self.sample()
            target = value if path == 'root' else value[path]
            target['extra'] = None
            with self.subTest(path=path):
                self.assertFalse(self.valid(value))
        for key in ('quote', 'evidence_url', 'scope'):
            value = self.sample()
            value['facts'][key] = None
            self.assertFalse(self.valid(value))

    def test_unknown_passage_ids_or_model_generated_quotes_are_rejected(self):
        for ref in ('invented-id', 'https://example.com/manual', {'id': 'passage-01'}, None):
            value = self.sample()
            value['evidence']['summary'] = [ref]
            with self.subTest(ref=ref):
                self.assertFalse(self.valid(value))

    def test_required_fields_depend_on_candidate_kind(self):
        required = {'project': ('category',), 'reading': ('category', 'author', 'original_date'),
                    'news': ('category', 'event_date'), 'update': ('category', 'change_note')}
        for kind, fields in required.items():
            for field in fields:
                for part in ('facts', 'evidence'):
                    value = self.sample(kind)
                    if field not in value[part]:
                        continue
                    del value[part][field]
                    with self.subTest(kind=kind, field=field, part=part):
                        self.assertFalse(self.valid(value, kind))
        update = self.sample('update')
        update['facts']['change_note'] = 'Do not duplicate evidence fields in facts.'
        self.assertFalse(self.valid(update, 'update'))

    def test_unqualified_requires_null_facts_and_evidence_even_with_no_passages(self):
        value = dict(qualified=False, reason='No supplied source supports this item.', facts=None, evidence=None)
        self.assertTrue(self.valid(value, passages=[]))
        for part in ('facts', 'evidence'):
            for invalid in ({}, [], self.sample()[part]):
                changed = copy.deepcopy(value)
                changed[part] = invalid
                with self.subTest(part=part, invalid=invalid):
                    self.assertFalse(self.valid(changed))
        value['qualified'] = True
        self.assertFalse(self.valid(value))
        self.assertFalse(self.valid(self.sample(), passages=[]))

    def test_license_evidence_can_be_empty_but_other_evidence_cannot(self):
        value = self.sample()
        value['evidence']['license'] = []
        self.assertTrue(self.valid(value))  # Existing fact/license validation decides whether it is sufficient.
        for field in ('category', 'summary', 'usage_conditions'):
            changed = self.sample()
            changed['evidence'][field] = []
            with self.subTest(field=field):
                self.assertFalse(self.valid(changed))

    def test_repeated_passage_ids_are_rejected_in_every_evidence_array(self):
        for kind in ('project', 'update', 'reading', 'news'):
            for field in self.sample(kind)['evidence']:
                value = self.sample(kind)
                value['evidence'][field] = ['passage-01', 'passage-01']
                with self.subTest(kind=kind, field=field):
                    self.assertFalse(self.valid(value, kind))

    def test_nonempty_strings_category_license_enum_and_date_shape(self):
        for field, invalid in (('summary', ''), ('audience', '  '), ('title', None),
                               ('category', 'Unknown category'), ('open_source_status', 'probably')):
            value = self.sample()
            value['facts'][field] = invalid
            with self.subTest(field=field):
                self.assertFalse(self.valid(value))
        value = self.sample()
        value['reason'] = '  '
        self.assertFalse(self.valid(value))
        value['reason'] = 'A reason.'
        value['qualified'] = 1
        self.assertFalse(self.valid(value))
        reading = self.sample('reading')
        for invalid in ('2026-1-04', '2026-10-04T12:00:00Z', ''):
            reading['facts']['original_date'] = invalid
            self.assertFalse(self.valid(reading, 'reading'))
        news = self.sample('news')
        self.assertTrue(self.valid(news, 'news'))
        news['facts']['event_date'] = '2026-10-04T12:00:00+08:00'
        self.assertTrue(self.valid(news, 'news'))  # It preserves source precision, not a generated default time.


if __name__ == '__main__':
    unittest.main()
