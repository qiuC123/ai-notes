from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx

from ai_notes import digest_pipeline as pipeline, digest_runtime as runtime
from ai_notes import digest_reason_review as reason_review
from ai_notes import digest_reason_statements as reason_statements
from ai_notes.digest_passages import build_passages
from ai_notes.digest_understanding import source_documents


class ReasonFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        action = {'action': 'generate', 'ranking_type': 'daily', 'period': '2026-01-01'}
        queued = runtime.enqueue(self.root, action)
        self.job = runtime.claim(self.root, 'owner', job_id=queued['job_id'])
        self.card = {'candidate_id': 'fixture', 'input_hash': 'unchanged-card'}
        self.entry = {'request_id': 'original-source-receipt'}
        self.score = {'request_id': 'original-score-receipt'}
        self.text = 'Organizes local files.'
        contexts = [{'url': 'https://example.test/install', 'text': self.text}]
        passages = build_passages(contexts)
        self.unit = {
            'field': 'selection_basis.scores.usability.reason', 'text': self.text,
            'evidence_refs': [contexts[0]['url']], 'passages': passages,
            'source_documents': source_documents(contexts),
            'source_basis': 'original_evidence_refs', 'missing_refs': [],
        }
        self.calls = []
        self.response = self.valid_output()
        self.client = httpx.Client(transport=httpx.MockTransport(self.respond))
        self.addCleanup(self.client.close)
        self.model = runtime.ModelClient(self.root, base_url='https://provider.example/v1',
            model='synthetic-reason-review', api_key='fixture-only', client=self.client,
            reasoning_effort='low')

    def valid_output(self, unit=None):
        unit = self.unit if unit is None else unit
        return {'verdict': 'accept', 'reason': 'The quoted text supports the complete reason.',
            'checks': [{'text': unit['text'], 'status': 'supported',
                'passage_ids': [unit['passages'][0]['id']], 'reason': 'Direct documented purpose.'}]}

    def respond(self, request):
        self.calls.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {
            'content': json.dumps(self.response, ensure_ascii=False)}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})

    def run_unit(self, unit=None, budget=8):
        return pipeline._review_reason_unit(self.root, self.job, 'owner', self.model,
            self.card, self.entry, self.score, self.unit if unit is None else unit, budget)

    def restore_job(self):
        with runtime._db(self.root, write=False) as con:
            saved = con.execute('SELECT checkpoints FROM jobs WHERE job_id=?',
                (self.job['job_id'],)).fetchone()[0]
        self.job['checkpoints'] = json.loads(saved)


class ReasonPipelineTests(ReasonFixture):
    def test_reason_input_is_frozen_and_contains_only_unchanged_own_sources(self):
        result = self.run_unit()
        self.assertEqual('accept', result['verdict'])
        material = json.loads(self.calls[0]['messages'][1]['content'])
        self.assertEqual(self.unit, material['unit'])
        self.assertEqual('own-refs.v1', material['reason_review_contract'])
        frozen = self.job['checkpoints']['reason-input:fixture:' + self.unit['field']]
        self.assertEqual('reason-review-v15-own-refs', frozen['request']['stage'])
        self.assertEqual(result['signature'], frozen['signature'])
        self.assertIn('base_signature', frozen)
        self.assertEqual(self.response, self.job['checkpoints'][
            'reason-response:fixture:' + self.unit['field']]['receipt']['output'])

    def test_missing_own_source_defers_without_http_or_receipt(self):
        for missing, passages in ((['https://example.test/missing'], self.unit['passages']),
                                  ([], [])):
            unit = copy.deepcopy(self.unit)
            unit.update(field='missing-' + str(len(passages)), missing_refs=missing,
                        passages=passages)
            result = self.run_unit(unit)
            self.assertEqual('defer', result['verdict'])
            self.assertTrue(result['missing_source'])
            self.assertEqual(missing, result['missing_refs'])
            self.assertNotIn('reason-response:fixture:' + unit['field'], self.job['checkpoints'])
        self.assertEqual([], self.calls)
        self.assertEqual([], runtime.status(self.root)['requests'])

    def test_invalid_model_output_is_preserved_and_never_repaired_or_retried(self):
        self.response['repaired_refs'] = ['https://example.test/readme']
        result = self.run_unit()
        self.assertTrue(result['invalid_response'])
        self.assertEqual('defer', result['verdict'])
        self.restore_job()
        self.assertEqual(result, self.run_unit())
        self.assertEqual(1, len(self.calls))
        saved = self.job['checkpoints']['reason-response:fixture:' + self.unit['field']]
        self.assertEqual(self.response, saved['receipt']['output'])

    def test_paid_response_before_response_checkpoint_recovers_same_frozen_request(self):
        checkpoint = runtime.checkpoint
        def crash(root, job_id, owner, stage, value):
            if stage.startswith('reason-response:'):
                raise OSError('after successful HTTP, before response checkpoint')
            return checkpoint(root, job_id, owner, stage, value)
        with patch.object(runtime, 'checkpoint', side_effect=crash):
            with self.assertRaises(OSError):
                self.run_unit()
        self.restore_job()
        frozen = copy.deepcopy(self.job['checkpoints']['reason-input:fixture:' + self.unit['field']])
        original_schema = reason_review.review_schema
        with patch.object(reason_review, 'REASON_REVIEW_PROMPT', 'Later prompt must not replace frozen input'), \
             patch.object(reason_review, 'review_schema', wraps=original_schema) as schema:
            result = self.run_unit()
        # The validator checks the existing output; no request schema is rebuilt.
        self.assertEqual(1, schema.call_count)
        self.assertEqual('accept', result['verdict'])
        self.assertEqual(1, len(self.calls))
        self.assertEqual(frozen, self.job['checkpoints']['reason-input:fixture:' + self.unit['field']])

    def test_saved_response_before_result_checkpoint_recovers_without_http(self):
        checkpoint = runtime.checkpoint
        def crash(root, job_id, owner, stage, value):
            if stage.startswith('reason-result:'):
                raise OSError('after response checkpoint, before result')
            return checkpoint(root, job_id, owner, stage, value)
        with patch.object(runtime, 'checkpoint', side_effect=crash):
            with self.assertRaises(OSError):
                self.run_unit()
        self.restore_job()
        self.assertEqual('accept', self.run_unit()['verdict'])
        self.assertEqual(1, len(self.calls))

    def test_uncertain_response_is_not_reissued(self):
        client = httpx.Client(transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(httpx.ReadTimeout('unknown provider outcome'))))
        self.addCleanup(client.close)
        self.model.client = client
        with self.assertRaises(runtime.RequestUncertain):
            self.run_unit()
        self.restore_job()
        self.model.client = self.client
        with self.assertRaises(runtime.RequestUncertain):
            self.run_unit()
        self.assertEqual([], self.calls)
        self.assertEqual('uncertain', runtime.status(self.root)['requests'][0]['status'])

    def test_input_character_budget_defers_before_http_and_keeps_material(self):
        unit = copy.deepcopy(self.unit)
        unit['text'] = 'Long source reason. ' * 4000
        result = self.run_unit(unit)
        self.assertTrue(result['input_exceeded'])
        self.assertEqual('defer', result['verdict'])
        self.assertEqual([], self.calls)
        self.assertEqual(unit, self.job['checkpoints']['reason-input:fixture:' + unit['field']]['unit'])

    def test_request_budget_exhaustion_defers_but_keeps_preceding_receipt(self):
        first = self.run_unit(budget=1)
        next_unit = copy.deepcopy(self.unit)
        next_unit['field'] = 'selection_basis.scores.interest.reason'
        second = self.run_unit(next_unit, budget=1)
        self.assertEqual('accept', first['verdict'])
        self.assertTrue(second['budget_exhausted'])
        self.assertEqual('defer', second['verdict'])
        self.assertEqual(1, len(self.calls))
        self.assertEqual(1, len(runtime.status(self.root)['requests']))
        self.assertEqual(first, self.job['checkpoints']['reason-result:fixture:' + self.unit['field']])

    def test_other_real_runtime_failure_is_not_disguised_as_budget_gap(self):
        with patch.object(self.model, 'request', side_effect=runtime.RuntimeError('provider rejected request')):
            with self.assertRaisesRegex(runtime.RuntimeError, 'provider rejected'):
                self.run_unit()
        self.assertNotIn('reason-result:fixture:' + self.unit['field'], self.job['checkpoints'])

    def test_unit_identity_changes_when_raw_source_score_or_reason_changes(self):
        first = self.run_unit()
        self.entry['request_id'] = 'different-source-receipt'
        second = self.run_unit()
        self.score['request_id'] = 'different-score-receipt'
        third = self.run_unit()
        changed = copy.deepcopy(self.unit)
        changed['text'] = 'Organizes local files'
        self.response = self.valid_output(changed)
        fourth = self.run_unit(changed)
        self.assertEqual(4, len(self.calls))
        self.assertEqual(4, len({result['signature'] for result in (first, second, third, fourth)}))


class ReasonSelectionRoutingTests(ReasonFixture):
    def ranks(self):
        ranked = []
        materials = {}
        for index, score in ((0, 80), (1, 90), (2, 70)):
            cid = 'candidate-' + str(index)
            card = {'candidate_id': cid, 'input_hash': 'card-' + cid}
            entry = {'request_id': 'source-' + cid, 'observation': {'kind': 'project'}}
            review = {'decision': 'select', 'total_score': score}
            ranked.append((score, card, {}, {'request_id': 'score-' + cid}, review))
            materials[cid] = entry
        return ranked, materials

    def test_failed_reason_stops_that_candidate_preserves_whole_gate_and_promotes_next(self):
        ranked, materials = self.ranks()
        unit_calls = []
        featured = []
        def whole(root, job, owner, model, card, entry, assessment, receipt, budget, **kwargs):
            featured.append((card['candidate_id'], kwargs['featured']))
            material = {'candidate_id': card['candidate_id']}
            pipeline._save(root, job, owner, 'editorial-input:' + card['candidate_id'],
                {'request': {'material': material}})
            return pipeline._save(root, job, owner, 'editorial:' + card['candidate_id'],
                {'verdict': 'accept', 'reason': 'Original whole-packet accept.', 'issues': []})
        def per_reason(root, job, owner, model, card, entry, receipt, unit, budget):
            unit_calls.append((card['candidate_id'], unit['field']))
            blocked = card['candidate_id'] == 'candidate-1'
            return {'verdict': 'defer' if blocked else 'accept', 'reason': 'Own source mismatch.'}
        units = [dict(self.unit), dict(self.unit, field='selection_basis.reason')]
        failures = []; exclusions = []
        policy = {'editorial_scope': 'public-introduction.v1', 'reason_review_contract': reason_review.CONTRACT}
        with patch.object(pipeline, '_review_editorial', side_effect=whole), \
             patch.object(reason_review, 'build_reason_units', return_value=units), \
             patch.object(pipeline, '_review_reason_unit', side_effect=per_reason), \
             patch.object(pipeline.selection, 'record', side_effect=lambda root, review: review) as record:
            selected, actual_featured = pipeline._select_public_items(self.root, self.job, 'owner', self.model,
                {'policy': policy, 'ranking_type': 'weekly'}, materials, ranked, 50, 2, failures, exclusions)
        self.assertEqual(['candidate-0', 'candidate-2'], [row[1] for row in selected])
        self.assertEqual({'candidate-0', 'candidate-2'}, actual_featured)
        self.assertEqual([('candidate-1', self.unit['field']), ('candidate-0', self.unit['field']),
            ('candidate-0', 'selection_basis.reason'), ('candidate-2', self.unit['field']),
            ('candidate-2', 'selection_basis.reason')], unit_calls)
        self.assertEqual(2, record.call_count)
        self.assertIn('Own source mismatch.', failures[0])
        self.assertEqual('accept', self.job['checkpoints']['editorial:candidate-1']['verdict'])
        self.assertEqual([('candidate-1', True), ('candidate-0', True), ('candidate-2', True)], featured)

    def test_absent_marker_or_nonselected_score_does_not_add_reason_calls(self):
        for marker, decision in ((None, 'select'), (reason_review.CONTRACT, 'defer'),
                                 (reason_review.CONTRACT, 'reject')):
            ranked, materials = self.ranks()
            ranked = ranked[:1]
            ranked[0][4]['decision'] = decision
            policy = {'editorial_scope': 'public-introduction.v1'}
            if marker:
                policy['reason_review_contract'] = marker
            with patch.object(pipeline, '_review_editorial', return_value={'verdict': 'accept'}), \
                 patch.object(pipeline.selection, 'record', side_effect=lambda root, review: review), \
                 patch.object(reason_review, 'build_reason_units', side_effect=AssertionError('must not build')):
                selected, _ = pipeline._select_public_items(self.root, self.job, 'owner', self.model,
                    {'policy': policy, 'ranking_type': 'daily'}, materials, ranked, 37, 8, [], [])
            self.assertEqual(1 if decision == 'select' else 0, len(selected))

    def test_whole_packet_defer_stops_before_reason_calls(self):
        ranked, materials = self.ranks()
        with patch.object(pipeline, '_review_editorial', return_value={'verdict': 'defer', 'reason': 'Public mismatch'}), \
             patch.object(reason_review, 'build_reason_units', side_effect=AssertionError('must not build')), \
             patch.object(pipeline.selection, 'record', side_effect=AssertionError('must not record')):
            selected, _ = pipeline._select_public_items(self.root, self.job, 'owner', self.model,
                {'policy': {'editorial_scope': 'public-introduction.v1', 'reason_review_contract': reason_review.CONTRACT},
                 'ranking_type': 'daily'}, materials, ranked, 193, 8, [], [])
        self.assertEqual([], selected)

    def test_new_budget_is_bounded_and_old_budget_is_unchanged(self):
        for deep, old, new in ((12, 37, 193), (35, 106, 561), (60, 181, 961)):
            self.assertEqual(old, pipeline._request_budget({}, deep))
            self.assertEqual(new, pipeline._request_budget({'reason_review_contract': reason_review.CONTRACT}, deep))
            self.assertLessEqual(new, 1000)

    def test_screen_budget_uses_frozen_policy_not_current_configuration(self):
        for policy, budget in (({}, 37), ({'reason_review_contract': reason_review.CONTRACT}, 193)):
            model = Mock()
            model.request.return_value = {'output': {'selected_ids': [], 'decisions': [
                {'candidate_id': 'a', 'reason': 'Synthetic preliminary reason'}]}}
            frozen = {'records': {'a': {}}, 'request': {'stage': 'synthetic-screen',
                'system': 'screen', 'material': {'deep_limit': 12}}, 'offset': 0,
                'eligible_count': 1, 'source_review_contract': 'frozen-source',
                'policy_snapshot': policy, 'prompt_snapshot': 'frozen prompt'}
            with patch.object(pipeline.selection, 'load_policy', side_effect=AssertionError('no upgrade')):
                pipeline._run_screen(self.root, self.job, 'owner', model, frozen)
            self.assertEqual(budget, model.request.call_args.kwargs['max_requests'])


class StatementReasonPipelineTests(ReasonFixture):
    def prepare_statement(self):
        self.unit['statements'] = [{'id': 's0', 'text': self.unit['text']}]
        self.response = {'verdict': 'accept', 'reason': 'Original purpose is supported.',
            'checks': [{'statement_id': 's0', 'status': 'supported',
                'passage_ids': [self.unit['passages'][0]['id']], 'reason': 'Direct source purpose.'}]}

    def run_statement(self):
        return pipeline._review_reason_unit(self.root, self.job, 'owner', self.model,
            self.card, self.entry, self.score, self.unit, 3,
            reason_review_contract=reason_statements.CONTRACT)

    def test_v2_request_binds_statement_ids_and_uses_separate_frozen_contract(self):
        self.prepare_statement()
        result = self.run_statement()
        self.assertEqual('accept', result['verdict'])
        frozen = self.job['checkpoints']['reason-input:fixture:' + self.unit['field']]
        self.assertEqual('reason-review-v16-statements', frozen['request']['stage'])
        self.assertEqual(self.unit, frozen['request']['material']['unit'])
        self.assertEqual('own-refs.v2', frozen['request']['material']['reason_review_contract'])
        self.assertEqual(self.response, self.job['checkpoints'][
            'reason-response:fixture:' + self.unit['field']]['receipt']['output'])
        self.assertNotIn('text', result['checks'][0])

    def test_wrong_statement_id_preserves_invalid_raw_and_never_retries(self):
        self.prepare_statement()
        self.response['checks'][0]['statement_id'] = 'invented-id'
        first = self.run_statement()
        self.assertTrue(first['invalid_response'])
        self.assertEqual('defer', first['verdict'])
        self.restore_job()
        self.assertEqual(first, self.run_statement())
        self.assertEqual(1, len(self.calls))
        self.assertEqual(self.response, self.job['checkpoints'][
            'reason-response:fixture:' + self.unit['field']]['receipt']['output'])

    def test_v2_paid_response_recovers_without_upgrading_frozen_prompt(self):
        self.prepare_statement()
        original = runtime.checkpoint
        def crash(root, job_id, owner, stage, value):
            if stage.startswith('reason-response:'):
                raise OSError('interrupted after successful request')
            return original(root, job_id, owner, stage, value)
        with patch.object(runtime, 'checkpoint', side_effect=crash):
            with self.assertRaises(OSError):
                self.run_statement()
        self.restore_job()
        frozen = copy.deepcopy(self.job['checkpoints']['reason-input:fixture:' + self.unit['field']])
        with patch.object(reason_statements, 'REASON_REVIEW_PROMPT', 'Later prompt'):
            recovered = self.run_statement()
        self.assertEqual('accept', recovered['verdict'])
        self.assertEqual(1, len(self.calls))
        self.assertEqual(frozen, self.job['checkpoints']['reason-input:fixture:' + self.unit['field']])

    def test_v2_inherits_frozen_budget_and_policy_dependencies(self):
        from ai_notes import digest_selection as selection
        policy = selection.load_policy(Path(__file__).resolve().parents[1])
        policy['reason_review_contract'] = reason_statements.CONTRACT
        selection.validate_policy(policy)
        for deep, expected in ((12, 193), (35, 561), (60, 961)):
            self.assertEqual(expected, pipeline._request_budget(policy, deep))
        del policy['score_input_contract']
        with self.assertRaises(selection.SelectionError):
            selection.validate_policy(policy)

    def test_v2_selection_gate_routes_own_statements_and_stops_before_record(self):
        self.prepare_statement()
        cid = self.card['candidate_id']
        entry = dict(self.entry, observation={'kind': 'project'})
        self.job['checkpoints']['editorial-input:' + cid] = {'request': {'material': {'own': True}}}
        policy = {'editorial_scope': 'public-introduction.v1', 'reason_review_contract': reason_statements.CONTRACT}
        failures = []
        with patch.object(pipeline, '_review_editorial', return_value={'verdict': 'accept'}), \
             patch.object(reason_statements, 'build_reason_units', return_value=[self.unit]) as build, \
             patch.object(pipeline, '_review_reason_unit', return_value={'verdict': 'defer', 'reason': 'Scope mismatch'}) as review, \
             patch.object(pipeline.selection, 'record', side_effect=AssertionError('must not record')):
            selected, _ = pipeline._select_public_items(self.root, self.job, 'owner', self.model,
                {'policy': policy, 'ranking_type': 'daily'}, {cid: entry},
                [(80, self.card, {}, self.score, {'decision': 'select'})], 193, 8, failures, [])
        self.assertEqual([], selected)
        build.assert_called_once_with({'own': True})
        self.assertEqual(reason_statements.CONTRACT, review.call_args.kwargs['reason_review_contract'])
        self.assertIn('Scope mismatch', failures[0])

    def test_unknown_direct_contract_stops_before_http(self):
        with self.assertRaisesRegex(ValueError, 'unsupported reason review contract'):
            pipeline._review_reason_unit(self.root, self.job, 'owner', self.model,
                self.card, self.entry, self.score, self.unit, 3, reason_review_contract='unknown')
        self.assertEqual([], self.calls)


if __name__ == '__main__':
    unittest.main()
