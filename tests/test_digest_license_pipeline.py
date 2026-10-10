from __future__ import annotations

import copy
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_notes import digest, digest_pipeline as pipeline, digest_selection as selection, digest_runtime as runtime
from tests import test_digest_score_input as score_fixtures
from tests import test_digest_public_pipeline as public_fixtures
from tests import test_digest_reason_pipeline as reason_fixtures


ROOT = Path(__file__).resolve().parents[1]


class LicensePolicyTests(unittest.TestCase):
    def test_new_scope_is_closed_and_requires_public_discovery(self):
        policy = selection.load_policy(ROOT)
        policy.pop('source_support_contract', None)
        self.assertEqual('excluded.v1', policy['license_review_scope'])
        for key, value in (('license_review_scope', None), ('license_review_scope', {}),
                           ('license_review_scope', 'unknown'), ('editorial_scope', None),
                           ('introduction_contract', None)):
            changed = copy.deepcopy(policy)
            changed[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(selection.SelectionError):
                selection.validate_policy(changed)

    def test_scoring_uses_only_its_frozen_scope_and_keeps_other_material(self):
        fixture = score_fixtures.ScoreInputTests()
        fixture.setUp()
        old = copy.deepcopy(fixture.prepared)
        old['policy'].pop('license_review_scope', None)
        new = copy.deepcopy(old)
        new['policy']['license_review_scope'] = 'excluded.v1'
        with patch.object(selection, 'load_policy', side_effect=AssertionError('no canonical upgrade')):
            legacy = selection.build_scoring_input(old, 'fixture')
            scoped = selection.build_scoring_input(new, 'fixture')
        self.assertEqual('excluded.v1', scoped['policy'].pop('license_review_scope'))
        self.assertEqual(legacy, scoped)
        self.assertNotIn('license_review_scope', old['policy'])

    def test_old_prepared_prompt_is_reused_without_new_scope_suffix(self):
        scoped = selection.load_policy(ROOT)
        scoped.pop('source_support_contract', None)
        legacy = copy.deepcopy(scoped)
        legacy.pop('license_review_scope')
        text = selection.load_prompt(ROOT, legacy)
        old = {'policy': legacy, 'prompt_text': text,
               'prompt_hash': hashlib.sha256(text.encode()).hexdigest()}
        self.assertNotIn(selection.LICENSE_SCOPE_GUIDANCE, text)
        self.assertTrue(selection.load_prompt(ROOT, scoped).endswith(selection.LICENSE_SCOPE_GUIDANCE))
        with patch.object(selection, 'load_prompt', side_effect=AssertionError('use saved prompt')):
            self.assertEqual(text, selection.get_prompt(ROOT, old))


class LicenseSourceAndEditorialTests(unittest.TestCase):
    def setUp(self):
        self.fixture = public_fixtures.PublicPipelineTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fx = self.fixture.fx

    def original(self, scoped):
        policy = selection.load_policy(self.fx.root)
        if not scoped:
            policy.pop('license_review_scope', None)
        queued = runtime.enqueue(self.fx.root, dict(action='generate', ranking_type='daily', period=self.fx.period))
        job = runtime.claim(self.fx.root, 'owner', job_id=queued['job_id'])
        pipeline._save(self.fx.root, job, 'owner', 'screen', dict(
            source_review_contract=pipeline.understanding.SOURCE_REVIEW_CONTRACT,
            policy_snapshot=policy))
        record = digest.candidates(self.fx.root, ranking_type='daily', period=self.fx.period)['candidates'][0]
        model = self.fx.real_model(self.fx.root, base_url='https://provider.example/v1', model='fixture',
                                  api_key='fixture-only', client=self.fx.client)
        with patch.object(selection, 'load_policy', side_effect=AssertionError('frozen scope only')):
            entry = pipeline._review_original(self.fx.root, job, 'owner', model, record, 'fixture', 37)
        self.assertFalse(entry.get('deferred'), entry)
        return job, entry, model, policy

    def test_verify_scope_is_frozen_in_both_request_material_and_system(self):
        job, _, _, _ = self.original(True)
        request = job['checkpoints']['original-input:fixture']['request']
        self.assertEqual('excluded.v1', request['material']['license_review_scope'])
        self.assertTrue(request['system'].endswith(selection.LICENSE_SCOPE_GUIDANCE))

    def test_unmarked_old_source_does_not_inherit_current_canonical_scope(self):
        job, _, _, _ = self.original(False)
        request = job['checkpoints']['original-input:fixture']['request']
        self.assertNotIn('license_review_scope', request['material'])
        self.assertNotIn(selection.LICENSE_SCOPE_GUIDANCE, request['system'])

    def test_editorial_scope_and_cached_raw_defer_are_not_rewritten(self):
        job, entry, model, policy = self.original(True)
        self.fx.editorial_defer = True
        url = entry['contexts'][0]['url']
        card = dict(candidate_id='fixture', input_hash='unchanged-card',
                    material=entry['observation'], ranking_type='daily')
        assessment = dict(precheck=dict(status='PASS', reasons=['Useful files'], evidence_refs=[url]),
            scores={key:dict(score=8, reason='Documented file use', evidence_refs=[url]) for key in selection.DIMENSIONS},
            flags=[], reason='Documented use.')
        options = dict(introduction_contract=policy['introduction_contract'], editorial_scope=policy['editorial_scope'],
                       license_review_scope=policy['license_review_scope'])
        first = pipeline._review_editorial(self.fx.root, job, 'owner', model, card, entry,
                                           assessment, dict(request_id='score'), 37, **options)
        frozen = copy.deepcopy(job['checkpoints']['editorial-input:fixture'])
        raw = copy.deepcopy(job['checkpoints']['editorial-response:fixture'])
        self.assertEqual('defer', first['verdict'])
        self.assertEqual('excluded.v1', frozen['request']['material']['license_review_scope'])
        self.assertTrue(frozen['request']['system'].endswith(pipeline.editorial.LICENSE_REVIEW_SUPPLEMENT))
        with patch.object(model, 'request', side_effect=AssertionError('no reissue')):
            restored = pipeline._review_editorial(self.fx.root, job, 'owner', model, card, entry,
                                                 assessment, dict(request_id='score'), 37, **options)
        self.assertEqual(first, restored)
        self.assertEqual(frozen, job['checkpoints']['editorial-input:fixture'])
        self.assertEqual(raw, job['checkpoints']['editorial-response:fixture'])


class LicenseReasonPipelineTests(reason_fixtures.ReasonFixture):
    def test_reason_request_uses_unit_scope_and_reuses_its_frozen_response(self):
        self.unit['license_review_scope'] = 'excluded.v1'
        first = self.run_unit()
        self.assertEqual('accept', first['verdict'])
        frozen = copy.deepcopy(self.job['checkpoints']['reason-input:fixture:' + self.unit['field']])
        self.assertEqual('excluded.v1', frozen['request']['material']['unit']['license_review_scope'])
        self.assertTrue(frozen['request']['system'].endswith(pipeline.editorial.LICENSE_REVIEW_SUPPLEMENT))
        self.restore_job()
        with patch.object(pipeline.reason_review, 'REASON_REVIEW_PROMPT', 'Later source rules'):
            self.assertEqual(first, self.run_unit())
        self.assertEqual(1, len(self.calls))
        self.assertEqual(frozen, self.job['checkpoints']['reason-input:fixture:' + self.unit['field']])


if __name__ == '__main__':
    unittest.main()
