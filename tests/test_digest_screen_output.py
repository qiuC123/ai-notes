from __future__ import annotations

import copy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx

from ai_notes import digest, digest_pipeline as pipeline, digest_runtime as runtime


class ScreenOutputTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        project = Path(__file__).resolve().parents[1]
        self.policy = pipeline.selection.load_policy(project)
        self.period = (digest._now().date() - timedelta(days=2)).isoformat()
        runtime.enqueue(self.root, {'action':'generate', 'ranking_type':'daily', 'period':self.period})
        self.job = runtime.claim(self.root, 'owner')
        self.calls = []
        self.output = None
        self.client = httpx.Client(transport=httpx.MockTransport(self.respond))
        self.addCleanup(self.client.close)
        self.model = runtime.ModelClient(self.root, base_url='https://provider.example/v1',
            model='fixture', api_key='fixture-only', client=self.client)

    def respond(self, request):
        self.calls.append(json.loads(request.content))
        return httpx.Response(200, json={'choices':[{'message':{'content':runtime._json(self.output)}}],
                                        'usage':{'total_tokens':1}})

    def frozen(self, count=19, *, kind='daily', deep_limit=12, long=False):
        cards = [dict(url=f'https://projects.example/tool-{i}',
            canonical_url=f'https://projects.example/tool-{i}', kind='project', category='开源项目',
            title=('题名' * 1000 if long else f'Tool {i}'),
            summary=('文字' * 1000 if long else '整理文件'),
            reason=('理由' * 1000 if long else '有具体用途'), change_note='') for i in range(count)]
        compact = pipeline._bounded_triage(cards)
        return dict(records={pipeline.selection.candidate_id(c):c for c in cards},
            offset=0, eligible_count=count, source_review_contract=pipeline.SOURCE_REVIEW_CONTRACT,
            policy_snapshot=copy.deepcopy(self.policy), prompt_snapshot='Frozen scoring rules.',
            request=pipeline._screen_request(compact, self.policy, kind, deep_limit))

    def complete(self, frozen):
        ids = list(frozen['records'])
        return {'selected_ids':ids[:7],
                'decisions':[{'candidate_id':cid, 'reason':'用途清楚，值得进一步阅读原文。'} for cid in ids]}

    def run_screen(self, frozen):
        return pipeline._run_screen(self.root, self.job, 'owner', self.model, frozen)

    def test_cloud_19_input_17_decisions_pattern_still_fails_without_repair_or_retry(self):
        frozen = self.frozen()
        self.output = self.complete(frozen)
        ids = list(frozen['records'])
        # Two of the seven chosen IDs have no decisions, as in the failed cloud trial.
        self.output['selected_ids'] = ids[:5] + ids[-2:]
        self.output['decisions'] = self.output['decisions'][:-2]
        original = copy.deepcopy(self.output)
        with self.assertRaisesRegex(runtime.RuntimeError, 'frozen schema'):
            self.run_screen(frozen)
        self.assertEqual(1, len(self.calls))
        self.assertNotIn('screen', self.job['checkpoints'])
        receipts = runtime.status(self.root)['requests']
        self.assertEqual(1, len(receipts))
        with runtime._db(self.root, write=False) as con:
            retained = json.loads(con.execute('SELECT output FROM requests').fetchone()[0])
        self.assertEqual(original, retained)
        # Re-reading a retained invalid result never creates a second HTTP request.
        with self.assertRaisesRegex(runtime.RuntimeError, 'frozen schema'):
            self.run_screen(frozen)
        self.assertEqual(1, len(self.calls))

    def test_complete_19_decisions_pass_with_same_request_budget_and_frozen_schema(self):
        frozen = self.frozen()
        self.output = self.complete(frozen)
        result = self.run_screen(frozen)
        self.assertEqual(19, len(result['decisions']))
        self.assertEqual(self.output['selected_ids'], result['selected_ids'])
        request = frozen['request']
        self.assertEqual('screen-v18-complete-decisions', request['stage'])
        self.assertEqual('complete-decisions.v1', request['material']['screen_output_contract'])
        self.assertEqual(5888, request['max_output_tokens'])
        self.assertEqual(5888, self.calls[0]['max_tokens'])
        self.assertIn('at most 80 characters', request['system'])
        self.assertEqual('excluded.v1', request['material']['license_review_scope'])
        self.assertIn(pipeline.selection.LICENSE_SCOPE_GUIDANCE, request['system'])
        schema = request['output_schema']
        self.assertEqual(19, schema['properties']['decisions']['minItems'])
        self.assertEqual(19, schema['properties']['decisions']['maxItems'])
        self.assertEqual(37, pipeline._request_budget(self.policy, 12))

    def test_duplicate_unknown_empty_long_extra_or_missing_decisions_are_rejected(self):
        frozen = self.frozen()
        base = self.complete(frozen)
        cases = {}
        value = copy.deepcopy(base); value['decisions'] = [value['decisions'][0]] * 20
        cases['twenty duplicate identities'] = value
        value = copy.deepcopy(base); value['decisions'][1] = copy.deepcopy(value['decisions'][0])
        cases['nineteen duplicate identities'] = value
        value = copy.deepcopy(base); value['decisions'][0]['candidate_id'] = 'unknown-id'
        cases['unknown decision identity'] = value
        value = copy.deepcopy(base); value['selected_ids'][0] = 'unknown-id'
        cases['unknown selection identity'] = value
        value = copy.deepcopy(base); value['selected_ids'] = [value['selected_ids'][0]] * 2
        cases['duplicate selection'] = value
        value = copy.deepcopy(base); value['selected_ids'] = list(frozen['records'])[:13]
        cases['selection over deep limit'] = value
        for reason in ('', '  \n', '字' * 81):
            value = copy.deepcopy(base); value['decisions'][0]['reason'] = reason
            cases[f'invalid reason {reason!r}'] = value
        for identity in ('', None, {'id':'nested'}):
            value = copy.deepcopy(base); value['decisions'][0]['candidate_id'] = identity
            cases[f'invalid identity {identity!r}'] = value
        value = copy.deepcopy(base); value['decisions'][0]['extra'] = 'not in contract'
        cases['extra decision field'] = value
        value = copy.deepcopy(base); value['extra'] = None
        cases['extra output field'] = value
        value = copy.deepcopy(base); del value['decisions'][0]['reason']
        cases['missing required reason'] = value
        for name, output in cases.items():
            with self.subTest(name=name):
                # This fake returns only a fixed parsed receipt; no provider or state write.
                with patch.object(runtime, 'renew'), patch.object(pipeline, '_save') as save:
                    model = Mock()
                    model.request.return_value = {'output':output}
                    with self.assertRaises(runtime.RuntimeError):
                        pipeline._run_screen(self.root, self.job, 'owner', model, frozen)
                    save.assert_not_called()
                    self.assertEqual(1, model.request.call_count)

    def test_exact_80_character_reason_and_empty_pool_are_valid(self):
        frozen = self.frozen()
        self.output = self.complete(frozen)
        self.output['decisions'][0]['reason'] = '字' * 80
        self.assertEqual(80, len(self.run_screen(frozen)['decisions'][0]['reason']))
        empty = self.frozen(0)
        with patch.object(self.model, 'request', side_effect=AssertionError('empty pool cannot call model')):
            self.assertEqual([], self.run_screen(empty)['decisions'])

    def test_legacy_frozen_request_keeps_original_budget_and_reuses_old_receipt(self):
        frozen = self.frozen()
        frozen['request'] = dict(stage='screen-v13-evidence-focus', system='Original legacy triage.',
            material={'cards':frozen['request']['material']['cards'], 'deep_limit':12,
                      'ranking_type':'daily'}, max_output_tokens=2222)
        original = copy.deepcopy(frozen)
        self.output = self.complete(frozen)
        # An old response obeyed its old contract, with no new 80-character/closed-key rule.
        self.output['decisions'][0].update(reason='字' * 81, old_optional='retain')
        pipeline._save(self.root, self.job, 'owner', 'screen-input', frozen)
        self.model.request(**frozen['request'], budget_key=self.job['job_id'],
                           max_requests=pipeline._request_budget(self.policy, 12))
        self.assertEqual(1, len(self.calls))
        with runtime._db(self.root, write=False) as con:
            self.job['checkpoints'] = json.loads(con.execute('SELECT checkpoints FROM jobs WHERE job_id=?',
                                               (self.job['job_id'],)).fetchone()[0])
        with patch.object(pipeline, '_screen_request', side_effect=AssertionError('cannot upgrade frozen request')), \
             patch.object(pipeline.selection, 'load_policy', side_effect=AssertionError('cannot use new policy')), \
             patch.object(digest, 'candidates', side_effect=AssertionError('cannot requery')):
            result = pipeline._screen(self.root, self.job, 'owner', self.model, 'daily', self.period, 30, 12)
        self.assertEqual(original, self.job['checkpoints']['screen-input'])
        self.assertEqual(1, len(self.calls))
        self.assertEqual(2222, self.calls[0]['max_tokens'])
        self.assertNotIn('OUTPUT JSON SHAPE CONTRACT', self.calls[0]['messages'][0]['content'])
        self.assertEqual('retain', result['decisions'][0]['old_optional'])

    def test_legacy_incomplete_receipt_retains_original_coverage_failure(self):
        frozen = self.frozen()
        frozen['request'].pop('output_schema')
        frozen['request']['stage'] = 'screen-v13-evidence-focus'
        frozen['request']['max_output_tokens'] = 2222
        self.output = self.complete(frozen)
        self.output['decisions'] = self.output['decisions'][:-2]
        original = copy.deepcopy(frozen)
        for _ in range(2):
            with self.assertRaisesRegex(runtime.RuntimeError, 'one reason for every screened candidate'):
                self.run_screen(frozen)
        self.assertEqual(original, frozen)
        self.assertEqual(1, len(self.calls))
        self.assertNotIn('screen', self.job['checkpoints'])

    def test_new_contract_has_distinct_fingerprint_and_reuses_complete_receipt(self):
        frozen = self.frozen()
        legacy = dict(stage='screen-v13-evidence-focus', system='Original legacy triage.',
            material={'cards':frozen['request']['material']['cards'], 'deep_limit':12,
                      'ranking_type':'daily'}, max_output_tokens=2222)
        self.output = self.complete(frozen)
        self.output['decisions'] = self.output['decisions'][:-2]
        old = self.model.request(**legacy, budget_key=self.job['job_id'], max_requests=37)
        self.output = self.complete(frozen)
        self.assertEqual(19, len(self.run_screen(frozen)['decisions']))
        self.assertEqual(2, len(self.calls))
        with patch.object(self.client, 'post', side_effect=AssertionError('must reuse successful receipt')):
            self.assertEqual(19, len(self.run_screen(frozen)['decisions']))
        with runtime._db(self.root, write=False) as con:
            rows = con.execute('SELECT request_id, stage, output FROM requests').fetchall()
            self.assertEqual(0, con.execute('SELECT count(*) FROM request_retries').fetchone()[0])
        self.assertEqual(2, len(rows))
        self.assertEqual(2, len({row['request_id'] for row in rows}))
        old_row = next(row for row in rows if row['request_id'] == old['request_id'])
        self.assertEqual(17, len(json.loads(old_row['output'])['decisions']))
        new_row = next(row for row in rows if row['stage'] == 'screen-v18-complete-decisions')
        self.assertEqual(19, len(json.loads(new_row['output'])['decisions']))

    def test_monthly_150_cards_with_full_context_and_schema_fit_actual_wire_budget(self):
        frozen = self.frozen(150, kind='monthly', deep_limit=60, long=True)
        self.output = self.complete(frozen)
        result = self.run_screen(frozen)
        self.assertEqual(150, len(result['decisions']))
        self.assertEqual(16384, frozen['request']['max_output_tokens'])
        messages = self.calls[0]['messages']
        self.assertLessEqual(len(messages[0]['content']) + len(messages[1]['content']), 60000)
        schema_text = runtime._json(frozen['request']['output_schema'])
        self.assertTrue(all(cid not in schema_text for cid in frozen['records']))
        self.assertEqual(150, len(json.loads(messages[1]['content'])['cards']))

    def test_license_scope_guidance_only_applies_when_exclusion_is_frozen(self):
        request = self.frozen()['request']
        policy = copy.deepcopy(self.policy)
        policy.pop('license_review_scope')
        legacy_scope = pipeline._screen_request(request['material']['cards'], policy, 'daily', 12)
        self.assertNotIn('license_review_scope', legacy_scope['material'])
        self.assertNotIn(pipeline.selection.LICENSE_SCOPE_GUIDANCE, legacy_scope['system'])


if __name__ == '__main__':
    unittest.main()
