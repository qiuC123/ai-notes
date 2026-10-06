"""Opt-in guidance and frozen inputs; these tests do not certify model meaning."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from jsonschema import Draft202012Validator

from ai_notes import digest_editorial as editorial, digest_pipeline as pipeline
from ai_notes import digest_runtime as runtime, digest_selection as selection
from ai_notes.digest_output_schema import assessment_schema, source_review_schema
from ai_notes.digest_understanding import bind_understanding_review
from tests import test_digest_editorial as editorial_fixtures
from tests import test_digest_pipeline as pipeline_fixtures
from tests import test_digest_selection_projection as projection_fixtures
from tests import test_digest_understanding as understanding_fixtures

ROOT = Path(__file__).resolve().parents[1]
MARKER = 'evidence-focus.v1'


class EvidenceFocusPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = selection.load_policy(ROOT)

    def test_marker_is_closed_and_requires_existing_v12_contracts(self):
        self.assertEqual(MARKER, self.policy['selection_refinement_contract'])
        self.assertEqual(self.policy, selection.validate_policy(self.policy))
        for value in (None, {}, '', 'evidence-focus.v2'):
            policy = dict(self.policy, selection_refinement_contract=value)
            with self.subTest(value=value), self.assertRaises(selection.SelectionError):
                selection.validate_policy(policy)
        for field in ('assessment_contract', 'editorial_review_contract', 'understanding_contract',
                      'introduction_contract', 'source_reading_contract', 'editorial_scope'):
            policy = copy.deepcopy(self.policy)
            policy.pop(field)
            with self.subTest(missing=field), self.assertRaises(selection.SelectionError):
                selection.validate_policy(policy)

    def test_unmarked_prompts_are_exactly_the_prior_file_prefix_at_every_contract(self):
        text = (ROOT / selection.PROMPT_PATH).read_text(encoding='utf-8')
        prefix, marker, _ = text.partition(selection.SELECTION_REFINEMENT_PROMPT_MARKER)
        self.assertTrue(marker)
        variants = []
        old = copy.deepcopy(self.policy)
        old.pop('selection_refinement_contract')
        variants.append(copy.deepcopy(old))
        for fields in (('source_reading_contract', 'editorial_scope'),
                       ('introduction_contract',), ('understanding_contract',)):
            for field in fields:
                old.pop(field)
            variants.append(copy.deepcopy(old))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / selection.PROMPT_PATH
            path.parent.mkdir(parents=True)
            path.write_text(prefix, encoding='utf-8')
            for policy in variants:
                with self.subTest(contracts=policy.keys()):
                    self.assertEqual(selection.load_prompt(root, policy), selection.load_prompt(ROOT, policy))
                    self.assertNotIn(marker, selection.load_prompt(ROOT, policy))
            with self.assertRaisesRegex(selection.SelectionError, 'supplement is unavailable'):
                selection.load_prompt(root, self.policy)
        self.assertEqual(text, selection.load_prompt(ROOT, self.policy))

    def test_scoring_only_adds_policy_marker_and_structure_example(self):
        fixture = projection_fixtures.ProjectReadingProjectionTests()
        fixture.setUp()
        fixture.enable()
        fixture.policy.update(copy.deepcopy(self.policy))
        fixture.policy.pop('selection_refinement_contract')
        before = copy.deepcopy((fixture.policy, fixture.card))
        old = fixture.projected()
        fixture.card['material']['selection_refinement_contract'] = MARKER
        self.assertEqual(old, fixture.projected())  # Model/candidate data cannot opt in.
        fixture.policy['selection_refinement_contract'] = MARKER
        new = fixture.projected()
        self.assertEqual(MARKER, new['policy']['selection_refinement_contract'])
        example = new['output_example']['unknown_example']
        schema = assessment_schema(policy=fixture.policy, card=fixture.card)
        self.assertTrue(Draft202012Validator(schema).is_valid(example))
        self.assertEqual({'precheck', 'scores', 'flags', 'reason'}, set(example))
        self.assertIsNone(example['scores'])
        self.assertEqual([], example['precheck']['evidence_refs'])
        projected = copy.deepcopy(new)
        projected.pop('output_example')
        projected['policy'].pop('selection_refinement_contract')
        self.assertEqual(old, projected)
        self.assertEqual(before[0], {key: value for key, value in fixture.policy.items()
                                    if key != 'selection_refinement_contract'})


class EvidenceFocusSourceGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = understanding_fixtures.UnderstandingContractTests()
        self.fixture.setUp()

    def test_task_kind_checklist_and_minimal_example_follow_unchanged_schema(self):
        for kind in ('project', 'update', 'reading', 'news'):
            with self.subTest(kind=kind):
                record = dict(self.fixture.record, kind=kind)
                schema = source_review_schema(record=record, passages=self.fixture.passages,
                    include_understanding=True, include_discovery=True, inline_passage_ids=True)
                before = copy.deepcopy(schema)
                guidance = pipeline._source_output_guidance(schema)
                self.assertTrue(Draft202012Validator(schema).is_valid(guidance['minimal_deferred_example']))
                self.assertEqual(schema, before)
                self.assertEqual(schema['required'], guidance['qualified_required_fields']['top_level'])
                self.assertEqual(schema['$defs']['facts']['required'], guidance['qualified_required_fields']['facts'])
                self.assertEqual(schema['$defs']['evidence']['required'], guidance['qualified_required_fields']['evidence'])
                self.assertEqual(['purpose', 'input', 'output', 'operations', 'conditions', 'unknowns'],
                                 guidance['qualified_required_fields']['understanding'])
                self.assertEqual({'text': None, 'passage_ids': []}, guidance['unknown_input_or_output_shape'])
                encoded = json.dumps(guidance)
                self.assertNotIn('confirmed', encoded)
                self.assertNotIn(self.fixture.passages[0]['id'], encoded)
                self.assertIn('structure_only_not_candidate_evidence', encoded)

    def test_missing_output_remains_invalid_and_raw_response_unchanged(self):
        raw = copy.deepcopy(self.fixture.output)
        raw['understanding'].pop('output')
        before = copy.deepcopy(raw)
        schema = source_review_schema(record=self.fixture.record, passages=self.fixture.passages,
                                      include_understanding=True, inline_passage_ids=True)
        self.assertFalse(Draft202012Validator(schema).is_valid(raw))
        with self.assertRaisesRegex(ValueError, 'purpose, input, output'):
            bind_understanding_review(raw, self.fixture.passages, self.fixture.record, self.fixture.documents)
        self.assertEqual(before, raw)


class EvidenceFocusEditorialTests(unittest.TestCase):
    def setUp(self):
        self.fixture = editorial_fixtures.EditorialInputTests()
        self.fixture.setUp()

    def test_new_projection_is_only_marker_and_keeps_exact_selection_refs(self):
        old = self.fixture.public(understanding_contract='project-reading.v1', featured=True)
        new = self.fixture.public(understanding_contract='project-reading.v1', featured=True,
                                  selection_refinement_contract=MARKER)
        self.assertEqual(MARKER, new.pop('selection_refinement_contract'))
        self.assertEqual(old, new)
        self.assertEqual(old['selection_basis']['scores']['value']['evidence_refs'],
                         self.fixture.assessment['scores']['value']['evidence_refs'])
        self.assertEqual(editorial.review_schema(old['passages']), editorial.review_schema(new['passages']))
        self.fixture.facts['selection_refinement_contract'] = MARKER
        self.fixture.record['selection_refinement_contract'] = MARKER
        self.assertEqual(old, self.fixture.public(understanding_contract='project-reading.v1', featured=True))

    def test_refinement_rejects_unknown_or_missing_public_dependencies(self):
        for marker in ('', None, 'evidence-focus.v2', {}):
            if marker is None:
                continue  # Omission preserves the old contract.
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, 'unsupported selection refinement'):
                self.fixture.public(understanding_contract='project-reading.v1', selection_refinement_contract=marker)
        with self.assertRaisesRegex(ValueError, 'requires public discovery'):
            self.fixture.public(selection_refinement_contract=MARKER)
        with self.assertRaisesRegex(ValueError, 'requires public discovery'):
            editorial.build_review_input(record=self.fixture.record, facts=self.fixture.facts,
                assessment=self.fixture.assessment, contexts=self.fixture.contexts, ranking_type='weekly',
                understanding_contract='project-reading.v1', introduction_contract='discovery.v1',
                selection_refinement_contract=MARKER)


class EvidenceFocusPipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = pipeline_fixtures.PipelineTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        path = self.fixture.root / selection.POLICY_PATH
        path.write_text(json.dumps(selection.load_policy(ROOT)), encoding='utf-8')

    def test_new_stage_material_and_guidance_are_frozen_through_all_stages(self):
        job, result = self.fixture.run_job()
        self.assertEqual('completed', result['status'], result)
        self.assertEqual(MARKER, job['checkpoints']['screen']['policy_snapshot']['selection_refinement_contract'])
        self.assertEqual(MARKER, job['checkpoints']['screen-input']['request']['material']['selection_refinement_contract'])
        originals = [value for key, value in job['checkpoints'].items() if key.startswith('original-input:')]
        self.assertEqual(6, len(originals))
        for frozen in originals:
            request = frozen['request']
            self.assertEqual('verify-facts-v13-evidence-focus', request['stage'])
            self.assertEqual(MARKER, request['material']['selection_refinement_contract'])
            self.assertIn('output', request['material']['output_guidance']['qualified_required_fields']['understanding'])
            self.assertTrue(request['system'].endswith(pipeline.EVIDENCE_FOCUS_VERIFY_SUPPLEMENT))
        for value in self.fixture.score_inputs:
            self.assertEqual(MARKER, value['policy']['selection_refinement_contract'])
            self.assertIn('output_example', value)
        for value in self.fixture.editorial_inputs:
            self.assertEqual(MARKER, value['selection_refinement_contract'])
        with runtime._db(self.fixture.root, write=False) as con:
            stages = {row['stage'] for row in con.execute('SELECT stage FROM requests')}
        self.assertEqual({'screen-v13-evidence-focus', 'verify-facts-v13-evidence-focus',
                          'value-score-v13-evidence-focus', 'editorial-v13-evidence-focus'}, stages)

    def test_unmarked_source_request_remains_exact_under_new_current_policy(self):
        policy = selection.load_policy(ROOT)
        policy.pop('selection_refinement_contract')
        record = dict(url='https://projects.example/legacy', title='Synthetic legacy tool',
                      category='游戏', kind='project', summary='Supplied purpose', reason='Source checking',
                      evidence_urls=[], source_urls=[])
        captured = []

        class DeferredModel:
            def request(self, **request):
                captured.append(copy.deepcopy(request))
                return {'request_id': 'offline-structure-response', 'output': {
                    'qualified': False, 'reason': 'Synthetic deferred fixture',
                    'facts': None, 'evidence': None, 'understanding': None}}

        def save(root, job, owner, stage, value):
            job['checkpoints'][stage] = copy.deepcopy(value)
            return value

        for hidden_marker in (False, True):
            candidate = copy.deepcopy(record)
            if hidden_marker:
                candidate['selection_refinement_contract'] = MARKER
            job = {'job_id': 'offline-legacy-job', 'payload': {'ranking_type': 'daily'},
                   'checkpoints': {'screen': {'source_review_contract': pipeline.understanding.SOURCE_REVIEW_CONTRACT,
                                              'policy_snapshot': copy.deepcopy(policy)}}}
            with patch.object(pipeline, '_save', side_effect=save), patch.object(runtime, 'renew'):
                result = pipeline._review_original(self.fixture.root, job, 'offline-owner',
                                                   DeferredModel(), candidate, 'legacy', 1)
            self.assertTrue(result['deferred'])
        self.assertEqual(captured[0], captured[1])
        self.assertEqual('verify-facts-v12-discovery-sources', captured[0]['stage'])
        self.assertNotIn('selection_refinement_contract', captured[0]['material'])
        self.assertNotIn('output_guidance', captured[0]['material'])
        self.assertNotIn(pipeline.EVIDENCE_FOCUS_VERIFY_SUPPLEMENT, captured[0]['system'])

    def test_missing_output_receipts_are_saved_rejected_and_not_retried(self):
        original = self.fixture.respond

        def malformed(request):
            response = original(request)
            payload = response.json()
            output = json.loads(payload['choices'][0]['message']['content'])
            if output.get('qualified') and 'understanding' in output:
                output['understanding'].pop('output')
                payload['choices'][0]['message']['content'] = json.dumps(output)
            return httpx.Response(200, json=payload)

        self.fixture.client = httpx.Client(transport=httpx.MockTransport(malformed))
        self.addCleanup(self.fixture.client.close)
        job, result = self.fixture.run_job()
        self.assertEqual('completed', result['status'], result)
        self.assertEqual([], self.fixture.score_inputs)
        responses = [value for key, value in job['checkpoints'].items() if key.startswith('original-response:')]
        self.assertEqual(6, len(responses))
        for response in responses:
            self.assertNotIn('output', response['receipt']['output']['understanding'])
        rejected = [value for key, value in job['checkpoints'].items() if key.startswith('original:')]
        self.assertTrue(all(value.get('deferred') and '原文结构核验失败' in value['reason'] for value in rejected))
        before = copy.deepcopy(job['checkpoints'])
        calls = len(self.fixture.calls)
        self.assertEqual('idle', runtime.work_once(self.fixture.root)['status'])
        self.assertEqual(calls, len(self.fixture.calls))
        self.assertEqual(before, job['checkpoints'])

    def test_editorial_frozen_request_recovery_keeps_guidance_and_identity(self):
        original = runtime.checkpoint
        stopped = False

        def crash(root, job_id, owner, stage, value):
            nonlocal stopped
            original(root, job_id, owner, stage, value)
            if stage.startswith('editorial-response:') and not stopped:
                stopped = True
                raise OSError('synthetic interruption after saved offline response')

        with patch.object(runtime, 'checkpoint', side_effect=crash):
            job, result = self.fixture.run_job()
        self.assertEqual('failed', result['status'])
        frozen = {key: copy.deepcopy(value) for key, value in job['checkpoints'].items()
                  if key.startswith('editorial-input:') or key.startswith('editorial-response:')}
        with patch.object(editorial, 'EVIDENCE_FOCUS_REVIEW_SUPPLEMENT', 'Later instructions cannot replace a frozen request.'):
            runtime.retry(self.fixture.root, job['job_id'])
            result = runtime.work_once(self.fixture.root)
        self.assertEqual('completed', result['status'], result)
        self.assertEqual(19, len(self.fixture.calls))
        with runtime._db(self.fixture.root, write=False) as con:
            checkpoints = json.loads(con.execute('SELECT checkpoints FROM jobs WHERE job_id=?', (job['job_id'],)).fetchone()[0])
        for key, value in frozen.items():
            self.assertEqual(value, checkpoints[key])


if __name__ == '__main__':
    unittest.main()
