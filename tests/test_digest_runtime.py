from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ai_notes import digest_runtime as runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_enqueue_is_business_key_idempotent_and_claim_exclusive(self):
        action = {'action': 'generate', 'ranking_type': 'daily', 'period': '2026-10-02'}
        first = runtime.enqueue(self.root, action)
        self.assertEqual(first['job_id'], runtime.enqueue(self.root, action)['job_id'])
        job = runtime.claim(self.root, 'worker-a', now=100, lease_seconds=60)
        self.assertEqual(first['job_id'], job['job_id'])
        self.assertIsNone(runtime.claim(self.root, 'worker-b', now=101))
        with self.assertRaises(runtime.RuntimeError):
            runtime.finish(self.root, job['job_id'], 'worker-b', {})
        runtime.finish(self.root, job['job_id'], 'worker-a', {'status': 'draft'})
        self.assertIsNone(runtime.claim(self.root, 'worker-b', now=200))

    def test_expired_lease_resumes_and_checkpoint_is_retained(self):
        runtime.enqueue(self.root, {'action': 'collect', 'period': '2026-10-03'})
        job = runtime.claim(self.root, 'a', now=100, lease_seconds=10)
        with patch.object(runtime.time, 'time', return_value=100):
            runtime.checkpoint(self.root, job['job_id'], 'a', 'collected', {'run_id': 'frozen'})
        resumed = runtime.claim(self.root, 'b', now=401)
        self.assertEqual({'run_id': 'frozen'}, resumed['checkpoints']['collected'])
        with self.assertRaises(runtime.RuntimeError):
            runtime.checkpoint(self.root, job['job_id'], 'a', 'stale', {})

    def test_dispatch_is_evaluated_only_once(self):
        with patch.object(runtime.digest, 'dispatch', return_value={'actions': []}) as dispatch:
            result = runtime.schedule(self.root)
        dispatch.assert_called_once_with(self.root)
        self.assertEqual([], result['jobs'])
        self.assertFalse((self.root / runtime.DB_PATH).exists())

    def test_retry_waiting_job_is_explicit(self):
        runtime.enqueue(self.root, {'action': 'generate', 'ranking_type': 'daily', 'period': '2026-10-02'})
        result = runtime.work_once(self.root, handlers={'generate': lambda *_: (_ for _ in ()).throw(runtime.ConfigurationRequired('model not configured'))})
        self.assertEqual('waiting_input', result['status'])
        self.assertIsNone(runtime.claim(self.root, 'other'))
        runtime.retry(self.root, result['job_id'])
        self.assertIsNotNone(runtime.claim(self.root, 'other'))

    def test_notice_dedup_uses_meaning_not_timestamp(self):
        a = runtime.queue_notice(self.root, 'monthly:2026-09', {'items': ['b','a'], 'shortfall': 2, 'failures': [], 'action_required': False})
        b = runtime.queue_notice(self.root, 'monthly:2026-09', {'items': ['a','b'], 'shortfall': 2, 'failures': [], 'action_required': False})
        self.assertEqual(a['notice_id'], b['notice_id'])
        c = runtime.queue_notice(self.root, 'monthly:2026-09', {'items': ['a','b','c'], 'shortfall': 1, 'failures': [], 'action_required': False})
        self.assertNotEqual(a['notice_id'], c['notice_id'])
        self.assertEqual(2, len(runtime.notices(self.root)))
        runtime.ack_notice(self.root, a['notice_id'], 'operator-reviewed')
        self.assertEqual(1, len(runtime.notices(self.root)))

    def test_request_receipt_reuses_success_and_records_usage(self):
        seen=[]
        def response(request):
            seen.append(request)
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"answer": 3}'}}], 'usage':{'prompt_tokens':11,'completion_tokens':4}})
        with httpx.Client(transport=httpx.MockTransport(response)) as client:
            model = runtime.ModelClient(self.root, base_url='https://model.example/v1', model='test', api_key='test-only', client=client)
            args=dict(stage='score', system='rules-v1', material={'body':'source'}, budget_key='one', max_requests=2)
            first=model.request(**args)
            second=model.request(**args)
        self.assertEqual({'answer':3}, first['output'])
        self.assertEqual(first['completed_at'],second['completed_at'])
        self.assertTrue(second['reused'])
        self.assertEqual(1,len(seen))
        state=runtime.status(self.root)
        self.assertEqual(11,state['usage']['prompt_tokens'])
        self.assertNotIn('test-only',json.dumps(state))

    def test_uncertain_request_is_not_automatically_repeated(self):
        calls=[]
        def fail(request):
            calls.append(request)
            raise httpx.ReadTimeout('ambiguous upstream outcome')
        with httpx.Client(transport=httpx.MockTransport(fail)) as client:
            model=runtime.ModelClient(self.root,base_url='https://model.example/v1',model='m',api_key='test-only',client=client)
            args=dict(stage='score',system='rules',material={'x':1},budget_key='run',max_requests=3)
            with self.assertRaises(runtime.RequestUncertain): model.request(**args)
            with self.assertRaises(runtime.RequestUncertain): model.request(**args)
        self.assertEqual(1,len(calls))

    def test_budget_counts_failed_provider_attempts(self):
        def response(request): return httpx.Response(200,json={'choices':[{'message':{'content':'not json'}}]})
        with httpx.Client(transport=httpx.MockTransport(response)) as client:
            model=runtime.ModelClient(self.root,base_url='https://model.example/v1',model='m',api_key='test-only',client=client)
            with self.assertRaises(runtime.RuntimeError):
                model.request(stage='score',system='rules',material={'x':1},budget_key='run',max_requests=1)
            with self.assertRaisesRegex(runtime.RuntimeError, 'budget'):
                model.request(stage='score',system='rules',material={'x':2},budget_key='run',max_requests=1)

    def test_completed_checkpoint_prevents_repeating_handler(self):
        runtime.enqueue(self.root, {'action':'collect','period':'2026-10-03'})
        job=runtime.claim(self.root,'old',now=1,lease_seconds=1)
        with patch.object(runtime.time, 'time', return_value=1):
            runtime.checkpoint(self.root,job['job_id'],'old','result',{'status':'ok','new_candidates':0})
        def unexpected(*args): self.fail('completed work must not run again')
        result=runtime.work_once(self.root,handlers={'collect':unexpected})
        self.assertEqual('completed',result['status'])

    def test_explicit_provider_resolution_preserves_actual_completion(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ReadTimeout('unknown')))) as client:
            model=runtime.ModelClient(self.root,base_url='https://model.example/v1',model='m',api_key='test-only',client=client)
            args=dict(stage='score',system='rules',material={'x':1},budget_key='run')
            with self.assertRaises(runtime.RequestUncertain): model.request(**args)
            request_id=runtime.status(self.root)['requests'][0]['request_id']
            completed=runtime.digest._now().isoformat()
            resolved=dict(provider_receipt='provider-request-123',completed_at=completed,output={'answer':1},usage={'prompt_tokens':5})
            runtime.resolve_request(self.root,request_id,resolved)
            result=model.request(**args)
        self.assertEqual({'answer':1},result['output'])
        self.assertTrue(result['reused'])
        self.assertEqual(runtime.digest._timestamp(completed,'t'),runtime.digest._timestamp(result['completed_at'],'t'))
        with self.assertRaises(runtime.RuntimeError): runtime.resolve_request(self.root,request_id,resolved)

    def test_transient_http_retry_is_explicit_audited_and_budgeted(self):
        calls=[]
        def response(request):
            calls.append(request)
            if len(calls)==1: return httpx.Response(503)
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"ok":true}'}}]})
        with httpx.Client(transport=httpx.MockTransport(response)) as client:
            model=runtime.ModelClient(self.root,base_url='https://model.example/v1',model='m',api_key='test-only',client=client)
            args=dict(stage='score',system='rules',material={'x':1},budget_key='run',max_requests=2)
            with self.assertRaises(runtime.RuntimeError): model.request(**args)
            request_id=runtime.status(self.root)['requests'][0]['request_id']
            with self.assertRaises(runtime.RuntimeError): model.request(**args)
            self.assertEqual(1,len(calls))
            runtime.retry_request(self.root,request_id,'Provider recovered after HTTP 503')
            self.assertTrue(model.request(**args)['output']['ok'])
            with self.assertRaisesRegex(runtime.RuntimeError,'budget'):
                model.request(**{**args,'material':{'x':2}})
        self.assertEqual(2,len(calls))
        self.assertEqual(2,len(runtime.status(self.root)['requests']))

    def test_targeted_work_does_not_take_an_older_queued_action(self):
        old=runtime.enqueue(self.root,{'action':'collect','period':'2026-10-02'})
        current=runtime.enqueue(self.root,{'action':'collect','period':'2026-10-03'})
        result=runtime.work_once(self.root,job_id=current['job_id'],handlers={'collect':lambda *_:{'status':'ok'}})
        self.assertEqual(current['job_id'],result['job_id'])
        self.assertEqual(old['job_id'],runtime.claim(self.root,'manual-resume')['job_id'])


if __name__ == '__main__':
    unittest.main()
