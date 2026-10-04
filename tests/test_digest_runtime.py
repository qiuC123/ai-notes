from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
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
        env = patch.dict(runtime.os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def model_config(self, content=None):
        path = self.root / 'model.env'
        path.write_text(content if content is not None else (
            'DIGEST_MODEL_BASE_URL=https://model.example/v1\n'
            'DIGEST_MODEL_NAME=test\nDIGEST_MODEL_API_KEY=test-only\n'), encoding='utf-8-sig')
        return path

    def test_model_env_file_reads_bom_export_quotes_comments_and_key_alias(self):
        path = self.model_config('\n# model config\n'
            'export DIGEST_MODEL_BASE_URL="https://model.example/v1" # endpoint\n'
            "DIGEST_MODEL_NAME='test model'\nDIGEST_MODEL_API_KEY_VAR=GLM_API_KEY\n"
            'GLM_API_KEY="literal-${KEEP_ME}-$(no-shell)#tail"\n'
            'DIGEST_MODEL_REASONING_EFFORT=low # bounded effort\n')
        model = runtime.ModelClient(self.root, env_file=path)
        self.assertEqual('https://model.example/v1', model.base_url)
        self.assertEqual('test model', model.model)
        self.assertEqual('literal-${KEEP_ME}-$(no-shell)#tail', model.api_key)
        self.assertEqual('low', model.reasoning_effort)
        self.assertFalse((self.root / runtime.DB_PATH).exists())

    def test_original_model_environment_configuration_still_works(self):
        with patch.dict(runtime.os.environ, {
            'DIGEST_MODEL_BASE_URL': 'https://model.example/v1',
            'DIGEST_MODEL_NAME': 'legacy', 'DIGEST_MODEL_API_KEY': 'legacy-test-key'}):
            model = runtime.ModelClient(self.root)
        self.assertEqual(('https://model.example/v1', 'legacy', 'legacy-test-key', None),
                         (model.base_url, model.model, model.api_key, model.reasoning_effort))

    def test_selected_model_file_does_not_mix_ambient_provider_configuration(self):
        path = self.model_config('DIGEST_MODEL_BASE_URL=https://file.example/v1\nDIGEST_MODEL_NAME=file-model\n')
        with patch.dict(runtime.os.environ, {
            'DIGEST_MODEL_ENV_FILE': str(path), 'DIGEST_MODEL_API_KEY': 'ambient-secret',
            'DIGEST_MODEL_REASONING_EFFORT': 'high'}):
            with self.assertRaises(runtime.ConfigurationRequired):
                runtime.ModelClient(self.root)
            model = runtime.ModelClient(self.root, api_key='explicit-test-key')
        self.assertEqual('https://file.example/v1', model.base_url)
        self.assertEqual('explicit-test-key', model.api_key)
        self.assertIsNone(model.reasoning_effort)
        path = self.model_config('DIGEST_MODEL_API_KEY_VAR=GLM_API_KEY\nGLM_API_KEY=file-secret\n')
        with patch.dict(runtime.os.environ, {
            'DIGEST_MODEL_BASE_URL': 'https://ambient.example/v1', 'DIGEST_MODEL_NAME': 'ambient-model'}):
            with self.assertRaises(runtime.ConfigurationRequired):
                runtime.ModelClient(self.root, env_file=path)

    def test_explicit_model_arguments_override_file_and_env_file_selection(self):
        path = self.model_config()
        with patch.dict(runtime.os.environ, {'DIGEST_MODEL_ENV_FILE': str(self.root / 'missing.env')}):
            model = runtime.ModelClient(self.root, env_file=path, base_url='https://override.example/v1',
                model='override', api_key='override-test-key', reasoning_effort='high')
        self.assertEqual(('https://override.example/v1', 'override', 'override-test-key', 'high'),
                         (model.base_url, model.model, model.api_key, model.reasoning_effort))

    def test_model_configuration_errors_never_echo_secret_or_source_line(self):
        for content in ('DO_NOT_ECHO_SECRET invalid assignment',
                        'GLM_API_KEY="DO_NOT_ECHO_SECRET',
                        'DIGEST_MODEL_API_KEY_VAR=DO_NOT_ECHO_SECRET!\n',
                        'DIGEST_MODEL_REASONING_EFFORT=DO_NOT_ECHO_SECRET\n'):
            with self.subTest(content=content):
                with self.assertRaises(runtime.ConfigurationRequired) as error:
                    runtime.ModelClient(self.root, env_file=self.model_config(content))
                self.assertNotIn('DO_NOT_ECHO_SECRET', str(error.exception))
        missing = self.root / 'DO_NOT_ECHO_SECRET.env'
        with self.assertRaises(runtime.ConfigurationRequired) as error:
            runtime.ModelClient(self.root, env_file=missing)
        self.assertNotIn('DO_NOT_ECHO_SECRET', str(error.exception))
        path = self.model_config()
        path.write_bytes(b'GLM_API_KEY=DO_NOT_ECHO_SECRET\xff')
        with self.assertRaises(runtime.ConfigurationRequired) as error:
            runtime.ModelClient(self.root, env_file=path)
        self.assertNotIn('DO_NOT_ECHO_SECRET', str(error.exception))
        self.assertFalse((self.root / runtime.DB_PATH).exists())

    def test_reasoning_effort_payload_and_receipt_identity(self):
        payloads = []
        def response(request):
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})
        args = dict(stage='score', system='rules', material={'x': 1}, budget_key='reasoning')
        with httpx.Client(transport=httpx.MockTransport(response)) as client:
            config = dict(base_url='https://model.example/v1', model='test', api_key='test-only', client=client)
            legacy = runtime.ModelClient(self.root, **config).request(**args)
            blank = runtime.ModelClient(self.root, **config, reasoning_effort='').request(**args)
            low = runtime.ModelClient(self.root, **config, reasoning_effort='low').request(**args)
            high = runtime.ModelClient(self.root, **config, reasoning_effort='high').request(**args)
            repeated = runtime.ModelClient(self.root, **config, reasoning_effort='low').request(**args)
        self.assertEqual(runtime._hash({'endpoint': config['base_url'], 'model': config['model'],
            'stage': 'score', 'system': 'rules', 'material': {'x': 1}, 'max_tokens': 4096,
            'format': 'json-object.v1'}), legacy['request_id'])
        self.assertTrue(blank['reused'])
        self.assertTrue(repeated['reused'])
        self.assertEqual(3, len({legacy['request_id'], low['request_id'], high['request_id']}))
        self.assertEqual(3, len(payloads))
        self.assertNotIn('reasoning_effort', payloads[0])
        self.assertEqual(['low', 'high'], [payload['reasoning_effort'] for payload in payloads[1:]])

    def test_supported_reasoning_efforts_and_empty_override(self):
        path = self.model_config('DIGEST_MODEL_BASE_URL=https://model.example/v1\n'
            'DIGEST_MODEL_NAME=test\nDIGEST_MODEL_API_KEY=test-only\nDIGEST_MODEL_REASONING_EFFORT=low\n')
        for effort in ('minimal', 'none', 'low', 'medium', 'high', 'max', 'xhigh', ''):
            with self.subTest(effort=effort):
                self.assertEqual(effort, runtime.ModelClient(self.root, env_file=path,
                                                           reasoning_effort=effort).reasoning_effort)

    def test_model_check_is_offline_safe_and_does_not_create_database(self):
        path = self.model_config()
        output = io.StringIO()
        with patch.object(runtime.httpx, 'Client') as network, redirect_stdout(output):
            code = runtime.main(['--model-env-file', str(path), 'model-check', '--root', str(self.root)])
        self.assertEqual(0, code)
        self.assertEqual({'endpoint': 'https://model.example/v1', 'model': 'test',
            'reasoning_effort': None, 'key_configured': True}, json.loads(output.getvalue()))
        self.assertNotIn('test-only', output.getvalue())
        network.assert_not_called()
        self.assertNotIn('DIGEST_MODEL_ENV_FILE', runtime.os.environ)
        self.assertFalse((self.root / runtime.DB_PATH).exists())

    def test_prompt_schema_preserves_legacy_identity_and_has_its_own_cache(self):
        payloads = []
        def response(request):
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})
        schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
                  'required': ['ok'], 'additionalProperties': False}
        args = dict(stage='score', system='rules', material={'x': 1}, budget_key='schema-mode')
        with httpx.Client(transport=httpx.MockTransport(response)) as client:
            config = dict(base_url='https://model.example/v1', model='test', api_key='test-only', client=client)
            model = runtime.ModelClient(self.root, **config)
            old = model.request(**args)
            old_again = model.request(**args, output_schema=None)
            new = model.request(**args, output_schema=schema)
            new_again = model.request(**args, output_schema=schema)
            changed = model.request(**args, output_schema={**schema, 'description': 'revised shape guidance'})
        self.assertEqual(runtime._hash({'endpoint': config['base_url'], 'model': config['model'],
            'stage': 'score', 'system': 'rules', 'material': {'x': 1}, 'max_tokens': 4096,
            'format': 'json-object.v1'}), old['request_id'])
        self.assertTrue(old_again['reused'])
        self.assertTrue(new_again['reused'])
        self.assertEqual(3, len({old['request_id'], new['request_id'], changed['request_id']}))
        self.assertEqual(3, len(payloads))
        self.assertEqual('rules', payloads[0]['messages'][0]['content'])
        self.assertIn(runtime._json(schema), payloads[1]['messages'][0]['content'])
        for payload in payloads:
            self.assertEqual({'type': 'json_object'}, payload['response_format'])
            self.assertNotIn('tools', payload)
            self.assertNotIn('tool_choice', payload)

    def test_schema_mode_keeps_extra_output_fields_and_never_repairs_or_retries(self):
        raw = {'scores': {'value_note': None}, 'reason': 'Preserve invalid assessment verbatim.'}
        calls = []
        def response(request):
            calls.append(request)
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(raw)}}],
                                            'usage': {'prompt_tokens': 4, 'completion_tokens': 3}})
        with httpx.Client(transport=httpx.MockTransport(response)) as client:
            model = runtime.ModelClient(self.root, base_url='https://model.example/v1', model='test',
                                        api_key='test-only', client=client)
            args = dict(stage='score', system='rules', material={}, budget_key='invalid-shape',
                        output_schema={'type': 'object', 'additionalProperties': False})
            first = model.request(**args)
            repeated = model.request(**args)
        self.assertEqual(raw, first['output'])
        self.assertEqual(raw, repeated['output'])
        self.assertTrue(repeated['reused'])
        self.assertEqual(1, len(calls))
        with runtime._db(self.root, write=False) as con:
            self.assertEqual(raw, json.loads(con.execute('SELECT output FROM requests').fetchone()[0]))
            self.assertEqual(0, con.execute('SELECT count(*) FROM request_retries').fetchone()[0])

    def test_bad_schema_and_oversize_schema_fail_locally_before_receipt_or_network(self):
        calls = []
        def response(request):
            calls.append(request)
            self.fail('invalid request must not contact provider')
        with httpx.Client(transport=httpx.MockTransport(response)) as client:
            model = runtime.ModelClient(self.root, base_url='https://model.example/v1', model='test',
                                        api_key='test-only', client=client)
            for schema in ([], {'type': 'not-json-schema-type'}, {'type': 'object', 'description': 'x' * 60000}):
                with self.subTest(schema_type=type(schema)), self.assertRaises(runtime.RuntimeError):
                    model.request(stage='score', system='rules', material={}, budget_key='bad-schema', output_schema=schema)
        self.assertFalse((self.root / runtime.DB_PATH).exists())
        self.assertEqual([], calls)

    def test_schema_http_rejection_has_no_paid_fallback_or_implicit_retry(self):
        payloads = []
        def response(request):
            payloads.append(json.loads(request.content))
            return httpx.Response(400)
        with httpx.Client(transport=httpx.MockTransport(response)) as client:
            model = runtime.ModelClient(self.root, base_url='https://model.example/v1', model='test',
                                        api_key='test-only', client=client)
            args = dict(stage='score', system='rules', material={}, budget_key='rejected-schema',
                        output_schema={'type': 'object'})
            for _ in range(2):
                with self.assertRaises(runtime.RuntimeError):
                    model.request(**args)
        self.assertEqual(1, len(payloads))
        state = runtime.status(self.root)
        self.assertEqual(1, len(state['requests']))
        self.assertEqual(('failed', 'HTTPStatusError:400'),
                         (state['requests'][0]['status'], state['requests'][0]['error']))

    def test_model_check_rejects_incomplete_config_and_restores_environment(self):
        path = self.model_config('DIGEST_MODEL_BASE_URL=https://model.example/v1\n')
        with patch.dict(runtime.os.environ, {'DIGEST_MODEL_ENV_FILE': 'old-model.env'}):
            output = io.StringIO()
            with redirect_stdout(output):
                code = runtime.main(['model-check', '--model-env-file', str(path), '--root', str(self.root)])
            self.assertEqual('old-model.env', runtime.os.environ['DIGEST_MODEL_ENV_FILE'])
        self.assertEqual(1, code)
        self.assertEqual('error', json.loads(output.getvalue())['status'])
        self.assertFalse((self.root / runtime.DB_PATH).exists())

    def test_cli_env_file_reaches_work_without_scheduling(self):
        path = self.model_config()
        def check_work(root, *, job_id):
            self.assertEqual(str(path), runtime.os.environ['DIGEST_MODEL_ENV_FILE'])
            self.assertEqual('test', runtime.ModelClient(root).model)
            return {'status': 'idle'}
        with patch.object(runtime, 'work_once', side_effect=check_work) as work, \
                patch.object(runtime, 'schedule') as schedule, redirect_stdout(io.StringIO()):
            self.assertEqual(0, runtime.main(['work', '--model-env-file', str(path), '--root', str(self.root)]))
        work.assert_called_once_with(self.root, job_id=None)
        schedule.assert_not_called()
        self.assertNotIn('DIGEST_MODEL_ENV_FILE', runtime.os.environ)

    def test_model_smoke_has_one_request_budget_and_reuses_receipt(self):
        path = self.model_config()
        seen = []
        def response(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}],
                'usage': {'prompt_tokens': 8, 'completion_tokens': 4}})
        client = httpx.Client(transport=httpx.MockTransport(response))
        self.addCleanup(client.close)
        results = []
        with patch.object(runtime.httpx, 'Client', return_value=client), \
                patch.object(runtime, 'schedule') as schedule, patch.object(runtime, 'work_once') as work:
            for _ in range(2):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(0, runtime.main(['model-smoke', '--root', str(self.root), '--model-env-file', str(path)]))
                results.append(json.loads(output.getvalue()))
            with patch.dict(runtime.os.environ, {'DIGEST_MODEL_ENV_FILE': str(path)}):
                model = runtime.ModelClient(self.root, reasoning_effort='high')
                with self.assertRaisesRegex(runtime.RuntimeError, 'budget'):
                    model.request(stage='other', system='rules', material={}, budget_key='model-smoke', max_requests=1)
        self.assertFalse(results[0]['reused'])
        self.assertTrue(results[1]['reused'])
        self.assertEqual(results[0]['request_id'], results[1]['request_id'])
        self.assertEqual(1, len(seen))
        self.assertEqual(1024, seen[0]['max_tokens'])
        self.assertEqual({'type': 'json_object'}, seen[0]['response_format'])
        state = runtime.status(self.root)
        self.assertEqual([], state['jobs'])
        self.assertEqual([], state['pending_notices'])
        self.assertEqual(['model-smoke'], [request['budget_key'] for request in state['requests']])
        self.assertEqual(8, state['usage']['prompt_tokens'])
        schedule.assert_not_called()
        work.assert_not_called()

    def test_model_smoke_rejects_wrong_output_without_automatic_repeat(self):
        path = self.model_config()
        calls = []
        def response(request):
            calls.append(request)
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":1}'}}]})
        client = httpx.Client(transport=httpx.MockTransport(response))
        self.addCleanup(client.close)
        with patch.object(runtime.httpx, 'Client', return_value=client):
            for _ in range(2):
                with self.assertRaisesRegex(runtime.RuntimeError, 'model smoke response'):
                    runtime.model_smoke(self.root, env_file=path)
        self.assertEqual(1, len(calls))

    def test_read_only_commands_do_not_create_database(self):
        for command in ('status', 'notices'):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, runtime.main([command, '--root', str(self.root)]))
        self.assertFalse((self.root / runtime.DB_PATH).exists())

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

    def test_input_budget_including_schema_fails_before_request_or_http(self):
        calls = []
        def response(request):
            calls.append(request)
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})
        schema = {'type': 'object', 'description': 'shape guidance ' * 200}
        material = {'text': 'x' * 58000}
        system = 'r' * 1000
        self.assertLess(len(system) + len(runtime._json(material)), 60000)
        with httpx.Client(transport=httpx.MockTransport(response)) as client:
            model = runtime.ModelClient(self.root, base_url='https://model.example/v1',
                                        model='test', api_key='test-only', client=client)
            with self.assertRaises(runtime.InputBudgetExceeded) as error:
                model.request(stage='score', system=system, material=material, budget_key='one',
                              max_requests=1, output_schema=schema)
            self.assertIsInstance(error.exception, runtime.RuntimeError)
            self.assertEqual('model input exceeds 60000 character budget', str(error.exception))
            self.assertEqual([], calls)
            self.assertFalse((self.root / runtime.DB_PATH).exists())
            # The preflight failure must not consume the only allowed request.
            accepted = model.request(stage='score', system='rules', material={'text': 'small'},
                                     budget_key='one', max_requests=1, output_schema=schema)
        self.assertEqual({'ok': True}, accepted['output'])
        self.assertEqual(1, len(calls))
        self.assertEqual(1, len(runtime.status(self.root)['requests']))

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


class RuntimeCliTests(unittest.TestCase):
    """Exercise the real -m entrypoint after pipeline imports canonical runtime."""

    def run_worker(self, scenario):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        bootstrap = root / 'bootstrap'
        bootstrap.mkdir()
        (bootstrap / 'sitecustomize.py').write_text('''
import os
from ai_notes import digest_pipeline

def no_network(*args, **kwargs):
    raise AssertionError('network must not be used in CLI regression')
digest_pipeline.runtime.httpx.Client = no_network

def fail(root, job, owner):
    runtime = digest_pipeline.runtime
    if os.environ['DIGEST_TEST_SCENARIO'] == 'input_limit':
        model = runtime.ModelClient(root, base_url='https://model.example/v1',
                                    model='test', api_key='test-only')
        model.request(stage='test', system='rules', material={'text': 'x' * 60001}, budget_key='test')
    elif os.environ['DIGEST_TEST_SCENARIO'] == 'configuration':
        runtime.ModelClient(root, env_file=root / 'absent-model.env')
    else:
        raise LookupError('DO_NOT_ECHO_SECRET https://provider.example/?key=DO_NOT_ECHO_SECRET')
digest_pipeline.generate = fail
''', encoding='utf-8')
        job = runtime.enqueue(root, {'action': 'generate', 'ranking_type': 'daily', 'period': '2026-10-03'})
        env = {key: value for key, value in os.environ.items() if not key.startswith('DIGEST_MODEL_')}
        env['PYTHONPATH'] = os.pathsep.join((str(bootstrap), str(Path(__file__).resolve().parents[1] / 'src')))
        env['DIGEST_TEST_SCENARIO'] = scenario
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-m', 'ai_notes.digest_runtime',
                                 'work', '--root', str(root), '--id', job['job_id']],
                                cwd=root, env=env, capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertNotIn('DO_NOT_ECHO_SECRET', result.stdout + result.stderr)
        output = json.loads(result.stdout)
        state = runtime.status(root)
        self.assertEqual(job['job_id'], output['job_id'])
        self.assertEqual(1, state['jobs'][0]['attempts'])
        self.assertEqual([], state['requests'])
        self.assertEqual(output['status'], state['jobs'][0]['status'])
        self.assertEqual(output['error'], state['jobs'][0]['error'])
        self.assertEqual([output['error']], state['pending_notices'][0]['payload']['failures'])
        self.assertEqual(output['status'], state['pending_notices'][0]['payload']['outcome'])
        return output

    def test_cli_preserves_imported_runtime_safe_input_limit_error(self):
        output = self.run_worker('input_limit')
        self.assertEqual('failed', output['status'])
        self.assertEqual('model input exceeds 60000 character budget', output['error'])

    def test_cli_classifies_imported_configuration_as_waiting_input(self):
        output = self.run_worker('configuration')
        self.assertEqual('waiting_input', output['status'])
        self.assertEqual('cannot read model env file as UTF-8', output['error'])

    def test_cli_redacts_unknown_exception_text(self):
        output = self.run_worker('unknown')
        self.assertEqual('failed', output['status'])
        self.assertEqual('LookupError', output['error'])


if __name__ == '__main__':
    unittest.main()
