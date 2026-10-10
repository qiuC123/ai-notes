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


def discovery_cards():
    return [dict(candidate_id='id-' + str(index), category='AI 应用', kind='project',
        title='Tool ' + str(index), summary='有清楚用途；原始描述含 MIT 许可字样。',
        reason='历史判断：许可待核实，不入本期。', change_note='介绍原文更新；不代表新的评分。',
        **({'event_date':'2026-10-09'} if index == 2 else {})) for index in range(19)]


def legacy_policy():
    return {'license_review_scope':'excluded.v1', 'introduction_contract':'discovery.v1',
            'source_reading_contract':'discovery-reading.v1', 'editorial_scope':'public-introduction.v1'}


def frozen_request(request, policy):
    cards = discovery_cards()
    return dict(records={card['candidate_id']:copy.deepcopy(card) for card in cards},
        offset=0, eligible_count=len(cards), source_review_contract=pipeline.understanding.SOURCE_REVIEW_CONTRACT,
        policy_snapshot=copy.deepcopy(policy), prompt_snapshot='Frozen scoring rules.', request=request)


def complete_output():
    identities = [card['candidate_id'] for card in discovery_cards()]
    return {'selected_ids':identities[:3],
            'decisions':[{'candidate_id':identity, 'reason':'用途清楚，可进一步阅读原文。'} for identity in identities]}


class ScreenDiscoveryContextTests(unittest.TestCase):
    def test_fresh_request_omits_only_historical_reason_without_mutating_inputs(self):
        cards = discovery_cards()
        before = copy.deepcopy(cards)
        policy = legacy_policy()
        policy['source_support_contract'] = pipeline.selection.SOURCE_SUPPORT_CONTRACT
        request = pipeline._screen_request(cards, policy, 'daily', 12)
        self.assertEqual('screen-v20-discovery-context', request['stage'])
        self.assertEqual([{key:value for key,value in card.items() if key != 'reason'} for card in cards],
                         request['material']['cards'])
        self.assertEqual(before, cards)
        self.assertNotEqual(runtime._hash(cards), runtime._hash(request['material']['cards']))
        self.assertTrue(all('reason' not in card for card in request['material']['cards']))
        # Descriptions remain literal even when they contain licensing words.
        self.assertIn('MIT 许可', request['material']['cards'][0]['summary'])
        self.assertEqual('2026-10-09', request['material']['cards'][2]['event_date'])
        self.assertIn('unverified discovery descriptions', request['system'])
        self.assertIn('Make this round\'s shortlist', request['system'])
        self.assertIn('value to the publication audience', request['system'])
        self.assertIn(pipeline.selection.LICENSE_SCOPE_GUIDANCE, request['system'])
        request['material']['cards'][0]['summary'] = 'Request-only change'
        self.assertEqual(before, cards)

    def test_policy_without_source_support_keeps_exact_legacy_request_fingerprint(self):
        cards = discovery_cards()
        request = pipeline._screen_request(cards, legacy_policy(), 'daily', 12)
        self.assertEqual('screen-v18-complete-decisions', request['stage'])
        self.assertIs(cards, request['material']['cards'])
        self.assertEqual('df8ce9c663ef52f9b85eb087c4f79fe87a0eba55d8eac58189ab65149d83fe31',
                         runtime._hash(request))
        self.assertTrue(all('reason' in card for card in request['material']['cards']))
        self.assertNotIn('unverified discovery descriptions', request['system'])

    def test_fresh_projection_keeps_complete_nineteen_decision_contract(self):
        policy = legacy_policy()
        policy['source_support_contract'] = pipeline.selection.SOURCE_SUPPORT_CONTRACT
        request = pipeline._screen_request(discovery_cards(), policy, 'daily', 12)
        frozen = frozen_request(request, policy)
        original = copy.deepcopy(frozen)
        model = Mock()
        model.request.return_value = {'output':complete_output()}
        with patch.object(runtime, 'renew'), patch.object(pipeline, '_save', side_effect=lambda *args:args[-1]):
            result = pipeline._run_screen(Path('unused-offline-root'), {'job_id':'offline-job'}, 'owner', model, frozen)
        self.assertEqual(19, len(result['decisions']))
        self.assertEqual(set(frozen['records']), {item['candidate_id'] for item in result['decisions']})
        self.assertEqual(original, frozen)
        self.assertEqual(5888, request['max_output_tokens'])
        self.assertEqual(37, model.request.call_args.kwargs['max_requests'])
        self.assertTrue(all('reason' in record for record in result['records'].values()))
        model.request.return_value = {'output':dict(selected_ids=[], decisions=complete_output()['decisions'][:-2])}
        with patch.object(runtime, 'renew'), patch.object(pipeline, '_save') as save:
            with self.assertRaisesRegex(runtime.RuntimeError, 'frozen schema'):
                pipeline._run_screen(Path('unused-offline-root'), {'job_id':'offline-job'}, 'owner', model, frozen)
            save.assert_not_called()

    def test_frozen_v19_request_reuses_paid_receipt_without_constructing_v20(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            period = (digest._now().date() - timedelta(days=2)).isoformat()
            runtime.enqueue(root, {'action':'generate', 'ranking_type':'daily', 'period':period})
            job = runtime.claim(root, 'owner')
            # v19 changed only its marker/stage from this legacy constructor.
            # Reconstruct the old frozen fixture, never call the fresh branch.
            policy = legacy_policy()
            request = pipeline._screen_request(discovery_cards(), policy, 'daily', 12)
            policy['source_support_contract'] = pipeline.selection.SOURCE_SUPPORT_CONTRACT
            request['stage'] = 'screen-v19-source-support'
            request['material']['source_support_contract'] = pipeline.selection.SOURCE_SUPPORT_CONTRACT
            frozen = frozen_request(request, policy)
            before = copy.deepcopy(frozen)
            calls = []
            def respond(http_request):
                calls.append(json.loads(http_request.content))
                return httpx.Response(200, json={'choices':[{'message':{'content':runtime._json(complete_output())}}],
                                                'usage':{'total_tokens':1}})
            with httpx.Client(transport=httpx.MockTransport(respond)) as client:
                model = runtime.ModelClient(root, base_url='https://provider.example/v1',
                    model='fixture', api_key='fixture-only', client=client)
                pipeline._save(root, job, 'owner', 'screen-input', frozen)
                receipt = model.request(**request, budget_key=job['job_id'], max_requests=37)
                self.assertEqual(1, len(calls))
                job['checkpoints']['screen-input'] = copy.deepcopy(frozen)
                with patch.object(pipeline, '_screen_request', side_effect=AssertionError('cannot upgrade frozen v19')), \
                     patch.object(pipeline.selection, 'load_policy', side_effect=AssertionError('cannot replace frozen policy')), \
                     patch.object(digest, 'candidates', side_effect=AssertionError('cannot rediscover candidates')), \
                     patch.object(client, 'post', side_effect=AssertionError('cannot make another provider request')):
                    result = pipeline._screen(root, job, 'owner', model, 'daily', period, 30, 12)
                    self.assertEqual(19, len(result['decisions']))
                    self.assertEqual(before, job['checkpoints']['screen-input'])
                    # A completed checkpoint returns without even asking the
                    # model client for its cached response a second time.
                    with patch.object(model, 'request', side_effect=AssertionError('completed screen needs no request')):
                        self.assertEqual(result, pipeline._screen(root, job, 'owner', model, 'daily', period, 30, 12))
                requests = runtime.status(root)['requests']
                self.assertEqual(1, len(requests))
                self.assertEqual(receipt['request_id'], requests[0]['request_id'])
                self.assertEqual('screen-v19-source-support', requests[0]['stage'])
                self.assertEqual(1, len(calls))
                self.assertEqual(before, frozen)


if __name__ == '__main__':
    unittest.main()
