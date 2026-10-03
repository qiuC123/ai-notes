from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ai_notes import digest_jev as jev


class JevTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = self.root / 'test.env'
        self.env.write_text('TYPESAFE_API_KEY="test-secret-only"\n', encoding='utf-8-sig')
        self.questions = {'pick': {'type': 'choice', 'instructions': 'Choose one.', 'criteria': {'yes': 'Accept', 'no': 'Reject'}}}
        self.output = {'model': jev.DEFAULT_MODEL, 'answers': {'pick': {'type': 'choice', 'choice': 'yes',
                       'probabilities': {'yes': .75, 'no': .25}, 'confidence': .4}},
                       'usage': {'input_tokens': 100, 'output_tokens': 15}, 'extra': {'preserved': True}}

    def model(self, handler, **kwargs):
        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True, timeout=600)
        self.addCleanup(client.close)
        return jev.JevClient(self.root, env_file=self.env, client=client, **kwargs)

    def call(self, model, **kwargs):
        return model.request(**({'state': {'title': 'Test'}, 'questions': self.questions, 'budget_key': 'run'} | kwargs))

    def receipts(self):
        return [json.loads(p.read_text(encoding='utf-8')) for p in (self.root / 'jev-receipts').glob('*.json')]

    def test_official_wire_shape_cache_usage_timing_and_no_credentials_in_receipt(self):
        seen = []
        def response(request):
            seen.append(request)
            self.assertEqual(jev.ENDPOINT, str(request.url))
            self.assertEqual('Bearer test-secret-only', request.headers['Authorization'])
            self.assertEqual({'model': jev.DEFAULT_MODEL, 'state': {'title': 'Test'}, 'questions': self.questions}, json.loads(request.content))
            self.assertTrue(all(v <= 60 for v in request.extensions['timeout'].values()))
            return httpx.Response(200, json=self.output)
        model = self.model(response)
        first = self.call(model)
        self.env.write_text('TYPESAFE_API_KEY=rotated-test-key\n', encoding='utf-8')
        second = self.call(self.model(response), budget_key='different-budget', max_requests=1)
        self.assertEqual(1, len(seen))
        self.assertEqual(self.output, first['output'])
        self.assertEqual(self.output['usage'], first['usage'])
        self.assertEqual(first | {'reused': True}, second)
        self.assertGreaterEqual(first['elapsed_seconds'], 0)
        self.assertNotIn('test-secret-only', json.dumps(self.receipts()))
        self.assertNotIn('rotated-test-key', json.dumps(self.receipts()))

    def test_questions_state_and_model_are_part_of_cache_key(self):
        seen = []
        def response(request):
            seen.append(request)
            return httpx.Response(200, json=self.output)
        model = self.model(response)
        ids = {self.call(model)['request_id'], self.call(model, state={'title': 'Other'})['request_id']}
        questions = copy.deepcopy(self.questions)
        questions['pick']['instructions'] = 'Different instructions'
        ids.add(self.call(model, questions=questions)['request_id'])
        ids.add(self.call(self.model(response, model='jev-preview'))['request_id'])
        self.assertEqual(4, len(ids))
        self.assertEqual(4, len(seen))

    def test_failed_attempt_counts_budget_and_never_retries_or_leaks_http_body(self):
        seen = []
        def response(request):
            seen.append(request)
            return httpx.Response(401, text='secret-body test-secret-only', headers={'secret-header': 'test-secret-only'})
        model = self.model(response)
        for _ in range(2):
            with self.assertRaises(jev.JevError) as caught:
                self.call(model, max_requests=1)
            self.assertEqual(401, caught.exception.status_code)
            self.assertNotIn('test-secret-only', str(caught.exception))
        with self.assertRaisesRegex(jev.JevError, 'budget exhausted'):
            self.call(model, state={'title': 'Another'}, max_requests=1)
        self.assertEqual(1, len(seen))
        self.assertNotIn('secret-body', json.dumps(self.receipts()))
        self.assertNotIn('test-secret-only', json.dumps(self.receipts()))
        self.assertEqual('failed', self.receipts()[0]['status'])

    def test_transport_uncertain_and_pending_receipts_never_resubmit(self):
        seen = []
        def response(request):
            seen.append(request)
            raise httpx.ReadTimeout('Authorization: Bearer test-secret-only')
        model = self.model(response)
        for _ in range(2):
            with self.assertRaises(jev.RequestUncertain) as caught:
                self.call(model)
            self.assertNotIn('test-secret-only', str(caught.exception))
        path = next((self.root / 'jev-receipts').glob('*.json'))
        data = self.receipts()[0]
        self.assertEqual('uncertain', data['status'])
        data['status'] = 'pending'
        path.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(jev.RequestUncertain):
            self.call(model)
        self.assertEqual(1, len(seen))
        self.assertNotIn('test-secret-only', path.read_text(encoding='utf-8'))

    def test_disk_lock_blocks_second_client_during_network_attempt(self):
        second = self.model(lambda _: self.fail('duplicate request sent'))
        def response(request):
            with self.assertRaisesRegex(jev.JevError, 'active'):
                self.call(second)
            return httpx.Response(200, json=self.output)
        self.call(self.model(response))
        self.assertTrue(self.call(second)['reused'])

    def test_redirect_is_rejected_even_when_injected_client_follows_redirects(self):
        seen = []
        def response(request):
            seen.append(request)
            return httpx.Response(307, headers={'location': 'https://other.example/steal'})
        with self.assertRaises(jev.JevError) as caught:
            self.call(self.model(response))
        self.assertEqual(307, caught.exception.status_code)
        self.assertEqual(1, len(seen))

    def test_bounds_reject_nonfinite_confidence_and_oversized_responses(self):
        invalid = copy.deepcopy(self.output)
        invalid['answers']['pick']['confidence'] = 1.1
        with self.assertRaisesRegex(jev.JevError, 'validation'):
            self.call(self.model(lambda _: httpx.Response(200, json=invalid)))
        with patch.object(jev, 'MAX_RESPONSE_BYTES', 10):
            with self.assertRaisesRegex(jev.JevError, 'validation'):
                self.call(self.model(lambda _: httpx.Response(200, json=self.output)), state={'title': 'oversize'})
        self.assertTrue(all(r['status'] == 'failed' for r in self.receipts()))

    def test_invalid_configuration_and_score_rubric_do_not_attempt_network(self):
        model = self.model(lambda _: self.fail('invalid local input sent'))
        questions = {'score': {'type': 'score', 'instructions': 'Rate', 'criteria': [str(i) for i in range(11)]}}
        with self.assertRaisesRegex(jev.JevError, '2..10'):
            self.call(model, questions=questions)
        self.assertEqual([], self.receipts())
        self.env.write_text('DIGEST_MODEL_API_KEY=wrong-provider\n', encoding='utf-8')
        with patch.dict(jev.runtime.os.environ, {'TYPESAFE_API_KEY': 'ambient-do-not-use'}):
            with self.assertRaises(jev.runtime.ConfigurationRequired):
                jev.JevClient(self.root, env_file=self.env)


if __name__ == '__main__':
    unittest.main()
