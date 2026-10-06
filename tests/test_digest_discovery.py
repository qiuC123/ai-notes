"""Offline opt-in discovery contracts, including frozen pre-v11 compatibility."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from ai_notes import digest_editorial as editorial
from ai_notes import digest_output_schema as output_schema
from ai_notes import digest_selection as selection
from ai_notes.digest_passages import bind_review
from ai_notes.digest_understanding import bind_understanding_review
from tests import test_digest_editorial as editorial_fixtures
from tests import test_digest_output_schema as schema_fixtures
from tests import test_digest_passages as passage_fixtures
from tests import test_digest_selection_projection as projection_fixtures
from tests import test_digest_understanding as understanding_fixtures


ROOT = Path(__file__).resolve().parents[1]


class DiscoverySchemaBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = schema_fixtures.SourceReviewSchemaTests()
        self.fixture.setUp()

    def validator(self, kind='project', **options):
        schema = output_schema.source_review_schema(record={'kind': kind},
            passages=self.fixture.passages, **options)
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)

    def test_default_and_explicit_false_are_exact_legacy_schema(self):
        for kind in ('project', 'update', 'reading', 'news'):
            old = self.fixture.schema(kind)
            self.assertEqual(old, self.validator(kind, include_discovery=False).schema)
            self.assertNotIn('supported_systems', old['$defs']['facts']['properties'])
            changed = self.fixture.sample(kind)
            changed['facts']['supported_systems'] = None
            changed['evidence']['supported_systems'] = []
            self.assertFalse(self.validator(kind).is_valid(changed))

    def test_systems_required_for_every_kind_with_unknown_or_source_backed_known(self):
        for kind in ('project', 'update', 'reading', 'news'):
            validator = self.validator(kind, include_discovery=True)
            value = self.fixture.sample(kind)
            self.assertFalse(validator.is_valid(value))
            value['facts']['supported_systems'] = None
            value['evidence']['supported_systems'] = []
            self.assertTrue(validator.is_valid(value), kind)
            value['facts']['supported_systems'] = 'Windows'
            self.assertFalse(validator.is_valid(value))
            value['evidence']['supported_systems'] = ['passage-01']
            self.assertTrue(validator.is_valid(value))
            value['facts']['supported_systems'] = None
            self.assertFalse(validator.is_valid(value))

    def test_systems_cannot_be_empty_wrong_type_unknown_id_or_duplicate_ids(self):
        value = self.fixture.sample()
        value['facts']['supported_systems'] = 'Windows'
        value['evidence']['supported_systems'] = ['passage-01']
        validator = self.validator(include_discovery=True)
        for field, invalid in (('facts', ''), ('facts', ' '), ('facts', ['Windows']),
                               ('evidence', ['invented-id']), ('evidence', ['passage-01'] * 2)):
            changed = copy.deepcopy(value)
            changed[field]['supported_systems'] = invalid
            with self.subTest(field=field, invalid=invalid):
                self.assertFalse(validator.is_valid(changed))
        rejected = dict(qualified=False, reason='No source supports the item.', facts=None, evidence=None)
        self.assertTrue(validator.is_valid(rejected))

    def test_binder_carries_real_source_quotes_for_known_and_no_claim_for_null(self):
        fixture = passage_fixtures.PassageBindingTests()
        fixture.setUp()
        output = copy.deepcopy(fixture.output)
        output['facts']['supported_systems'] = 'A documented desktop route'
        output['evidence']['supported_systems'] = [fixture.main_id]
        before = copy.deepcopy(output)
        with self.assertRaisesRegex(ValueError, 'facts fields'):
            bind_review(output, fixture.passages, fixture.record)
        bound = bind_review(output, fixture.passages, fixture.record, include_discovery=True)
        claims = [claim for claim in bound['facts']['claims'] if claim['field'] == 'supported_systems']
        self.assertEqual(1, len(claims))
        self.assertEqual(output['facts']['supported_systems'], claims[0]['text'])
        self.assertEqual(fixture.passages[0]['quote'], claims[0]['quote'])
        self.assertEqual(fixture.main, claims[0]['evidence_url'])
        self.assertEqual(before, output)
        output['facts']['supported_systems'] = None
        with self.assertRaisesRegex(ValueError, 'empty evidence'):
            bind_review(output, fixture.passages, fixture.record, include_discovery=True)
        output['evidence']['supported_systems'] = []
        bound = bind_review(output, fixture.passages, fixture.record, include_discovery=True)
        self.assertIsNone(bound['facts']['supported_systems'])
        self.assertFalse(any(claim['field'] == 'supported_systems' for claim in bound['facts']['claims']))
        # Usage conditions mentioning a desktop installer remain separate data.
        self.assertEqual(fixture.facts['usage_conditions'], bound['facts']['usage_conditions'])

    def test_known_binder_requires_ids_and_refuses_fabricated_ids(self):
        fixture = passage_fixtures.PassageBindingTests()
        fixture.setUp()
        for ids in ([], ['invented-id'], [fixture.main_id, fixture.main_id]):
            output = copy.deepcopy(fixture.output)
            output['facts']['supported_systems'] = 'Windows'
            output['evidence']['supported_systems'] = ids
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                bind_review(output, fixture.passages, fixture.record, include_discovery=True)

    def test_understanding_and_discovery_can_be_enabled_independently_together(self):
        fixture = understanding_fixtures.UnderstandingContractTests()
        fixture.setUp()
        output = copy.deepcopy(fixture.output)
        output['facts']['supported_systems'] = None
        output['evidence']['supported_systems'] = []
        schema = output_schema.source_review_schema(record=fixture.record, passages=fixture.passages,
            include_understanding=True, include_discovery=True)
        Draft202012Validator.check_schema(schema)
        self.assertTrue(Draft202012Validator(schema).is_valid(output))
        bound = bind_understanding_review(output, fixture.passages, fixture.record, fixture.documents,
                                          include_discovery=True)
        self.assertIsNone(bound['facts']['supported_systems'])
        self.assertEqual(fixture.raw['operations'], bound['facts']['understanding']['operations'])
        self.assertEqual(fixture.documents, bound['facts']['understanding']['source_documents'])
        # False keeps the old binder shape and cannot silently consume a new field.
        with self.assertRaises(ValueError):
            bind_understanding_review(output, fixture.passages, fixture.record, fixture.documents)


class DiscoveryProjectionPromptTests(unittest.TestCase):
    projected = projection_fixtures.SourceReferenceProjectionTests.projected

    def setUp(self):
        projection_fixtures.SourceReferenceProjectionTests.setUp(self)

    def enable(self):
        self.policy['introduction_contract'] = selection.INTRODUCTION_CONTRACT
        self.policy['version'] = 'v11-discovery-introduction-uncalibrated'

    def test_current_policy_only_changes_contract_and_version_not_scoring_rules(self):
        current = selection.load_policy(ROOT)
        self.assertEqual('v17-discovery-scope-uncalibrated', current['version'])
        self.assertEqual('excluded.v1', current['license_review_scope'])
        self.assertEqual('evidence-focus.v1', current['selection_refinement_contract'])
        self.assertEqual('discovery.v1', current['introduction_contract'])
        old = copy.deepcopy(current)
        old.pop('license_review_scope')  # The v10 fixture predates this scope.
        old.pop('introduction_contract')
        old.pop('source_reading_contract', None)
        old.pop('editorial_scope', None)
        old.pop('selection_refinement_contract', None)
        old.pop('score_input_contract', None)
        old['version'] = 'v10-project-reading-uncalibrated'
        self.assertEqual(old, selection.validate_policy(old))
        assessment = dict(precheck=dict(status='PASS', reasons=['Documented use.'], evidence_refs=[self.url]),
            scores={key: dict(score=7, reason='Source-backed.', evidence_refs=[self.url]) for key in selection.DIMENSIONS},
            flags=[], reason='A limited, useful introduction.')
        old.pop('understanding_contract')
        self.enable()
        self.assertEqual(selection._validate_assessment(old, self.card, assessment),
                         selection._validate_assessment(self.policy, self.card, assessment))
        self.assertEqual(selection._calculate(old, self.card, assessment),
                         selection._calculate(self.policy, self.card, assessment))

    def test_marker_only_projection_preserves_systems_and_never_guesses_from_conditions(self):
        before = self.projected()
        self.card['material']['supported_systems'] = 'Windows 10 or later'
        self.card['material']['introduction_contract'] = 'discovery.v1'
        self.assertEqual(before, self.projected())  # Candidate data cannot enable the policy.
        self.enable()
        actual = self.projected()
        self.assertEqual('discovery.v1', actual['policy']['introduction_contract'])
        self.assertEqual('Windows 10 or later', actual['card']['material']['supported_systems'])
        del self.card['material']['supported_systems']
        self.assertIsNone(self.projected()['card']['material']['supported_systems'])
        self.assertIn('Windows', self.card['source_claims'][0]['quote'])
        self.card['material']['supported_systems'] = ['Windows']
        with self.assertRaisesRegex(ValueError, 'supported_systems'):
            self.projected()

    def test_unknown_policy_marker_fails_in_validation_projection_and_prompt_selection(self):
        for marker in (None, {}, '', 'discovery.v2'):
            self.policy['introduction_contract'] = marker
            with self.subTest(marker=marker):
                for call in (lambda: selection.validate_policy(self.policy), self.projected,
                             lambda: selection.load_prompt(ROOT, self.policy)):
                    with self.assertRaisesRegex(ValueError, 'unsupported introduction'):
                        call()

    def test_old_prompt_bytes_and_frozen_snapshot_survive_new_contract(self):
        # These are the normalized UTF-8 prompt hashes at the pre-v11 commit.
        base = selection.load_prompt(ROOT, self.policy)
        self.assertEqual('f97caff3d92af2ecf541adf0b81ed009a1189ee288b5836fb9e00b72fab61795',
                         hashlib.sha256(base.encode('utf-8')).hexdigest())
        self.policy['understanding_contract'] = 'project-reading.v1'
        old = selection.load_prompt(ROOT, self.policy)
        self.assertEqual('85bc07e8515c48a74542f2485a308961a33627033be1f0dfed64762283a6fa53',
                         hashlib.sha256(old.encode('utf-8')).hexdigest())
        frozen = dict(policy=copy.deepcopy(self.policy), prompt_text=old,
                      prompt_hash=hashlib.sha256(old.encode('utf-8')).hexdigest())
        self.enable()
        new = selection.load_prompt(ROOT, self.policy)
        self.assertTrue(new.startswith(old + selection.INTRODUCTION_PROMPT_MARKER))
        self.assertEqual(old, selection.get_prompt(ROOT, frozen))
        self.assertNotIn(selection.INTRODUCTION_PROMPT_MARKER, old)
        self.policy.pop('understanding_contract')
        discovery_only = selection.load_prompt(ROOT, self.policy)
        self.assertTrue(discovery_only.startswith(base + selection.INTRODUCTION_PROMPT_MARKER))
        self.assertNotIn(selection.UNDERSTANDING_PROMPT_MARKER, discovery_only)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / selection.PROMPT_PATH
            path.parent.mkdir(parents=True)
            path.write_text(base, encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'discovery prompt supplement'):
                selection.load_prompt(Path(temp), self.policy)

    def test_prepare_saves_marker_systems_and_claim_quotes_then_recovers_exact_values(self):
        self.enable()
        record = dict(self.card['material'], canonical_url=self.url, evidence_status='verified',
                      supported_systems='Windows 10 or later')
        cid = selection.candidate_id(record)
        system_claim = dict(field='supported_systems', text=record['supported_systems'],
                            evidence_url=self.url, quote=self.quote)
        with tempfile.TemporaryDirectory() as temp, patch.object(selection, '_query', return_value={'candidates': [record]}):
            root = Path(temp)
            prepared = selection.prepare(root, 'weekly', '2026-09-28', evidence_context=self.contexts,
                policy_snapshot=self.policy, prompt_snapshot='Frozen discovery prompt',
                source_claims={cid: self.claims + [system_claim]})
            frozen = copy.deepcopy(prepared)
            record['supported_systems'] = 'Caller mutation'
            self.policy['version'] = 'Caller mutation'
            self.assertEqual(frozen, selection.load_preparation(root, prepared['prepare_id']))
            actual = selection.build_scoring_input(frozen, cid)
            self.assertEqual('Windows 10 or later', actual['card']['material']['supported_systems'])
            proof = next(claim for claim in actual['card']['source_claims'] if claim['field'] == 'supported_systems')
            span = proof['source_span']
            self.assertEqual(self.quote, actual['card']['evidence_context'][span['context_index']]['text'][span['start']:span['end']])
            self.assertEqual('Frozen discovery prompt', selection.get_prompt(root, frozen))

    def test_explicit_source_map_reaches_frozen_and_scoring_cards_without_ledger_fields(self):
        self.enable()
        record = dict(self.card['material'], canonical_url=self.url, evidence_status='verified')
        self.assertNotIn('supported_systems', record)
        cid = selection.candidate_id(record)
        systems = {cid: 'Windows 10 or later'}
        claim = dict(field='supported_systems', text=systems[cid], evidence_url=self.url, quote=self.quote)
        original = copy.deepcopy((record, systems))
        with tempfile.TemporaryDirectory() as temp, patch.object(selection, '_query', return_value={'candidates': [record]}):
            root = Path(temp)
            prepared = selection.prepare(root, 'weekly', '2026-09-28', evidence_context=self.contexts,
                policy_snapshot=self.policy, prompt_snapshot='Frozen discovery source map',
                source_supported_systems=systems, source_claims={cid: self.claims + [claim]})
            self.assertEqual(original, (record, systems))
            systems[cid] = 'Caller mutation'
            loaded = selection.load_preparation(root, prepared['prepare_id'])
            self.assertEqual('Windows 10 or later', loaded['cards'][0]['material']['supported_systems'])
            self.assertEqual('Windows 10 or later', selection.build_scoring_input(loaded, cid)['card']['material']['supported_systems'])
            self.assertNotIn('supported_systems', record)

    def test_source_map_requires_exact_candidate_identity_valid_values_and_matching_quotes(self):
        self.enable()
        record = dict(self.card['material'], canonical_url=self.url, evidence_status='verified')
        cid = selection.candidate_id(record)
        with tempfile.TemporaryDirectory() as temp, patch.object(selection, '_query', return_value={'candidates': [record]}):
            def prepare(mapping, claims=None, policy=None):
                return selection.prepare(Path(temp), 'weekly', '2026-09-28', evidence_context=self.contexts,
                    policy_snapshot=self.policy if policy is None else policy, prompt_snapshot='Frozen discovery source map',
                    source_supported_systems=mapping, source_claims={cid: self.claims if claims is None else claims})
            for mapping in ([], {None: None}, {}, {'unknown': None}, {cid: None, 'unknown': None},
                            {cid: ''}, {cid: ' '}, {cid: ['Windows']}, {cid: False}):
                with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                    prepare(mapping)
            valid_claim = dict(field='supported_systems', text='Windows', evidence_url=self.url, quote=self.quote)
            for claims in (self.claims, self.claims + [dict(valid_claim, text='Linux')],
                           self.claims + [dict(valid_claim, quote='An invented platform assertion')]):
                with self.subTest(claims=claims), self.assertRaises(ValueError):
                    prepare({cid: 'Windows'}, claims)
            with self.assertRaisesRegex(ValueError, 'null supported_systems'):
                prepare({cid: None}, self.claims + [valid_claim])
            legacy = copy.deepcopy(self.policy)
            legacy.pop('introduction_contract')
            legacy.pop('source_reading_contract', None)
            legacy.pop('editorial_scope', None)
            with self.assertRaisesRegex(ValueError, 'requires discovery'):
                prepare({cid: None}, policy=legacy)

    def test_null_source_map_never_uses_record_or_usage_prose_as_platform_evidence(self):
        self.enable()
        record = dict(self.card['material'], canonical_url=self.url, evidence_status='verified',
                      supported_systems='Untrusted ledger value')
        cid = selection.candidate_id(record)
        with tempfile.TemporaryDirectory() as temp, patch.object(selection, '_query', return_value={'candidates': [record]}):
            prepared = selection.prepare(Path(temp), 'weekly', '2026-09-28', evidence_context=self.contexts,
                policy_snapshot=self.policy, prompt_snapshot='Frozen unknown source systems',
                source_supported_systems={cid: None}, source_claims={cid: self.claims})
            self.assertIsNone(prepared['cards'][0]['material']['supported_systems'])
            self.assertIsNone(selection.build_scoring_input(prepared, cid)['card']['material']['supported_systems'])
            self.assertEqual('Untrusted ledger value', record['supported_systems'])

    def test_omitted_new_argument_keeps_legacy_values_and_preparation_fingerprint(self):
        record = dict(self.card['material'], canonical_url=self.url, evidence_status='verified',
                      supported_systems='Candidate data must not enable discovery')
        with tempfile.TemporaryDirectory() as temp, patch.object(selection, '_query', return_value={'candidates': [record]}):
            def prepare(**options):
                return selection.prepare(Path(temp), 'weekly', '2026-09-28', evidence_context=self.contexts,
                    policy_snapshot=self.policy, prompt_snapshot='Frozen legacy prompt', **options)
            omitted = prepare()
            explicit_none = prepare(source_supported_systems=None)
            self.assertEqual(omitted, explicit_none)
            self.assertEqual(omitted['prepare_id'], explicit_none['prepare_id'])
            self.assertNotIn('supported_systems', omitted['cards'][0]['material'])


class DiscoveryEditorialTests(unittest.TestCase):
    def setUp(self):
        self.fixture = editorial_fixtures.EditorialInputTests()
        self.fixture.setUp()

    def build(self, **options):
        f = self.fixture
        return editorial.build_review_input(record=f.record, facts=f.facts, assessment=f.assessment,
            contexts=f.contexts, ranking_type='weekly', **options)

    def test_only_explicit_marker_exposes_new_fact_and_null_does_not_infer_platform(self):
        old = self.build()
        self.fixture.facts['supported_systems'] = 'Windows'
        self.fixture.record['introduction_contract'] = 'discovery.v1'
        self.assertEqual(old, self.build())
        actual = self.build(introduction_contract='discovery.v1')
        self.assertEqual('discovery.v1', actual['introduction_contract'])
        self.assertEqual('Windows', actual['facts']['supported_systems'])
        self.assertNotIn('introduction_contract', actual['candidate'])
        self.fixture.facts['supported_systems'] = None
        self.fixture.facts['usage_conditions'] = 'Windows builds require a separate optional GPU route.'
        self.assertIsNone(self.build(introduction_contract='discovery.v1')['facts']['supported_systems'])
        for marker in ('discovery.v2', '', {}):
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, 'unsupported introduction'):
                self.build(introduction_contract=marker)

    def test_discovery_review_preserves_understanding_and_all_supplied_source_text(self):
        self.fixture.add_understanding()
        self.fixture.facts['supported_systems'] = None
        actual = self.build(understanding_contract='project-reading.v1', introduction_contract='discovery.v1')
        self.assertEqual(self.fixture.facts['understanding']['operations'], actual['facts']['understanding']['operations'])
        self.assertEqual(self.fixture.contexts[0]['text'], ''.join(row['quote'] for row in actual['passages']))
        self.assertIsNone(actual['facts']['supported_systems'])

    def test_new_review_prompt_limits_omission_checks_without_changing_verdict_schema(self):
        # This tests selection of the contract text, not real-model quality.
        new = editorial.DISCOVERY_REVIEW_PROMPT
        self.assertIn('not a user manual', new)
        self.assertIn('only when it makes an actually stated claim false', new)
        self.assertIn('Missing exhaustive detail', new)
        self.assertIn('supported_systems', new)
        self.assertNotIn('discovery.v1', editorial.PROJECT_READING_REVIEW_PROMPT)
        self.assertNotIn('discovery.v1', editorial.REVIEW_PROMPT)
        passages = self.build()['passages']
        accepted = dict(verdict='accept', reason='No concrete material factual error in supplied text.', issues=[])
        self.assertEqual(accepted, editorial.validate_review(accepted, passages))


if __name__ == '__main__':
    unittest.main()
