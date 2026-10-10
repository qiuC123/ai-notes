from __future__ import annotations

import copy
from datetime import timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx

from ai_notes import digest, digest_pipeline as pipeline, digest_runtime as runtime
from tests import test_digest_pipeline as legacy


class PublicPipelineTests(unittest.TestCase):
    """Exercise v12 only in the existing temporary, synthetic HTTP fixture."""

    def setUp(self):
        self.fx = legacy.PipelineTests(methodName='runTest')
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        # The shared legacy fixture deliberately pins its own v11 contract.
        # This separate fixture explicitly opts into the current v12 policy.
        project = Path(__file__).resolve().parents[1]
        policy = json.loads((project / 'config/digest_selection.json').read_text(encoding='utf-8'))
        policy.pop('reason_review_contract', None)  # This fixture preserves pre-v15 whole-packet reviews.
        policy.pop('source_support_contract', None)
        self.assertEqual('public-introduction.v1', policy['editorial_scope'])
        (self.fx.root / 'config/digest_selection.json').write_text(json.dumps(policy), encoding='utf-8')
        self.fx.client.close()
        self.fx.client = httpx.Client(transport=httpx.MockTransport(self.respond))
        self.addCleanup(self.fx.client.close)
        self.rejected_url = None
        self.public_overclaim = False
        self.detail_overclaim = False

    def respond(self, request):
        payload = json.loads(request.content)
        material = json.loads(payload['messages'][1]['content'])
        response = self.fx.respond(request)
        body = response.json()
        output = json.loads(body['choices'][0]['message']['content'])
        if material.get('evidence_scope') == 'supplied_text_only_not_full_document_or_software_test':
            if material['candidate']['url'] == self.rejected_url:
                output = dict(verdict='defer', reason='The displayed detailed claim is unsupported.', issues=[dict(
                    field='public_fields.detail', code='unsupported_assertion',
                    passage_ids=[material['passages'][0]['id']], reason='This displayed detail overstates the supplied text.')])
        elif 'card' in material:
            index = int(material['card']['material']['url'].rsplit('-', 1)[1])
            # All six clear the real policy threshold; the small, unique
            # novelty difference makes score order differ from URL order.
            output['scores']['novelty']['score'] = 2 + index
        elif output.get('qualified') is True:
            if self.public_overclaim:
                output['facts']['summary'] = '已阅读许可证全文，确认可用于整理本地文件。'
            if self.detail_overclaim:
                output['facts']['detail'] = '已阅读许可证全文，确认可用于长期整理本地文件。'
        body['choices'][0]['message']['content'] = json.dumps(output, ensure_ascii=False)
        return httpx.Response(200, json=body)

    def weekly_period(self):
        return (self.fx.now.date() - timedelta(days=self.fx.now.weekday() + 14)).isoformat()

    def originals(self, job):
        return [value for key, value in job['checkpoints'].items() if key.startswith('original:')]

    def assert_featured_order(self, job):
        expected = [f'https://projects.example/tool-{index}' for index in (4, 3, 2, 1, 0)]
        items = job['checkpoints']['issue']['items']
        self.assertEqual(expected, [item['url'] for item in items])
        self.assertEqual(expected[:3], [item['url'] for item in items if item['featured']])
        self.assertEqual(6, len(self.fx.editorial_inputs))
        self.assertEqual(6, len({material['candidate']['url'] for material in self.fx.editorial_inputs}))
        self.assertEqual([f'https://projects.example/tool-{index}' for index in (5, 4, 3, 2, 1, 0)],
                         [material['candidate']['url'] for material in self.fx.editorial_inputs])
        for material in self.fx.editorial_inputs:
            index = int(material['candidate']['url'].rsplit('-', 1)[1])
            self.assertEqual(index >= 2, material['featured'])
            self.assertEqual(index >= 2, 'detail' in material['public_fields'])
            self.assertNotIn('facts', material)
            self.assertNotIn('assessment', material)
            self.assertNotIn('usage_conditions', material['public_fields'])
            self.assertNotIn('understanding', material)
            self.assertIn('scores', material['selection_basis'])

    def test_weekly_scores_determine_featured_and_deferred_leader_promotes_next_once(self):
        self.rejected_url = 'https://projects.example/tool-5'
        job, result = self.fx.run_job('weekly', self.weekly_period())
        self.assertEqual('completed', result['status'], result)
        self.assertEqual(5, result['result']['item_count'])
        self.assert_featured_order(job)
        self.assertEqual(19, len(self.fx.calls))

    def test_literal_id_choices_are_in_schema_without_a_repeated_material_list(self):
        job, result = self.fx.run_job()
        self.assertEqual('completed', result['status'], result)
        originals = [value['request'] for key, value in job['checkpoints'].items()
                     if key.startswith('original-input:')]
        self.assertEqual(6, len(originals))
        for request in originals:
            material = request['material']
            self.assertEqual('discovery-reading.v1', material['source_reading_contract'])
            self.assertNotIn('allowed_passage_ids', material)
            choices = sorted(passage['id'] for passage in material['passages'])
            self.assertIn('Copy passage IDs literally from supplied passages', request['system'])
            for field in ('summary', 'usage_conditions', 'supported_systems'):
                ids = request['output_schema']['$defs']['evidence']['properties'][field]['items']
                self.assertEqual(choices, ids['enum'])

    def test_unpublished_full_licence_note_is_saved_without_blocking_supported_public_summary(self):
        self.fx.text += '\n' + 'Additional license material. ' * 800
        self.fx.prose_overclaim = True
        job, result = self.fx.run_job()
        self.assertEqual('completed', result['status'], result)
        self.assertEqual(6, result['result']['item_count'])
        originals = self.originals(job)
        self.assertEqual(6, len(originals))
        self.assertTrue(all(not entry.get('deferred') for entry in originals))
        notes = [value for key, value in job['checkpoints'].items() if key.startswith('original-internal-scope:')]
        self.assertEqual(6, len(notes))
        self.assertTrue(all(any(issue['field'] == 'reason' for issue in note['issues']) for note in notes))
        self.assertTrue(all('许可证全文' in entry['facts']['reason'] for entry in originals))
        self.assertEqual(6, len(self.fx.editorial_inputs))
        self.assertTrue(all(material['public_fields']['summary'] == 'Organizes local files.'
                            for material in self.fx.editorial_inputs))
        self.assertTrue(all('许可证全文' not in json.dumps(material, ensure_ascii=False)
                            for material in self.fx.editorial_inputs))

    def test_displayed_full_licence_assertion_still_blocks_before_scoring(self):
        self.fx.text += '\n' + 'Additional license material. ' * 800
        self.public_overclaim = True
        job, result = self.fx.run_job()
        self.assertEqual('completed', result['status'], result)
        self.assertEqual(0, result['result']['item_count'])
        self.assertEqual([], self.fx.score_inputs)
        self.assertEqual([], self.fx.editorial_inputs)
        self.assertEqual(7, len(self.fx.calls))
        self.assertTrue(all(entry['deferred'] and any(issue['field'] == 'summary'
            for issue in entry['reading_scope_issues']) for entry in self.originals(job)))

    def test_scoring_full_licence_assertion_still_blocks_editorial_and_keeps_raw(self):
        self.fx.text += '\n' + 'Additional license material. ' * 800
        self.fx.score_overclaim = True
        job, result = self.fx.run_job()
        self.assertEqual('completed', result['status'], result)
        self.assertEqual(0, result['result']['item_count'])
        self.assertEqual([], self.fx.editorial_inputs)
        self.assertEqual(13, len(self.fx.calls))
        scores = [value for key, value in job['checkpoints'].items() if key.startswith('score-response:')]
        self.assertEqual(6, len(scores))
        self.assertTrue(all('许可证全文' in value['raw_output']['scores']['evidence']['reason'] for value in scores))
        scopes = [value for key, value in job['checkpoints'].items() if key.startswith('score-reading-scope:')]
        self.assertEqual(6, len(scopes))

    def test_bad_passage_ids_are_preserved_and_never_repaired_or_retried(self):
        self.fx.malformed_passage = True
        job, result = self.fx.run_job()
        self.assertEqual('completed', result['status'], result)
        self.assertEqual(0, result['result']['item_count'])
        self.assertEqual([], self.fx.score_inputs)
        self.assertEqual(7, len(self.fx.calls))
        responses = [value for key, value in job['checkpoints'].items() if key.startswith('original-response:')]
        self.assertEqual(6, len(responses))
        self.assertTrue(all(value['receipt']['output']['evidence']['usage_conditions'] == ['invented-passage']
                            for value in responses))
        self.assertTrue(all(entry['deferred'] and '原文结构核验失败' in entry['reason'] for entry in self.originals(job)))

    def test_weekly_featured_detail_scope_overclaim_is_deferred_before_review_http(self):
        self.fx.text += '\n' + 'Additional license material. ' * 800
        self.detail_overclaim = True
        job, result = self.fx.run_job('weekly', self.weekly_period())
        self.assertEqual('completed', result['status'], result)
        self.assertEqual(0, result['result']['item_count'])
        # Source verification and scoring both succeeded. The detail becomes
        # visible only when each prospective featured item is reviewed.
        self.assertEqual(6, len(self.fx.score_inputs))
        self.assertEqual([], self.fx.editorial_inputs)
        self.assertEqual(13, len(self.fx.calls))
        gates = [value for key, value in job['checkpoints'].items() if key.startswith('editorial:')]
        self.assertEqual(6, len(gates))
        self.assertTrue(all(value['verdict'] == 'defer' and any(issue['field'] == 'detail'
                            for issue in value['reading_scope_issues']) for value in gates))
        requests = [value['request']['material'] for key, value in job['checkpoints'].items()
                    if key.startswith('editorial-input:')]
        self.assertTrue(all(material['featured'] and '许可证全文' in material['public_fields']['detail']
                            for material in requests))
        self.assertTrue(all('许可证全文' in entry['facts']['detail'] for entry in self.originals(job)))

    def test_daily_unpublished_detail_scope_note_does_not_block_supported_summary(self):
        self.fx.text += '\n' + 'Additional license material. ' * 800
        self.detail_overclaim = True
        job, result = self.fx.run_job()
        self.assertEqual('completed', result['status'], result)
        self.assertEqual(6, result['result']['item_count'])
        self.assertEqual(19, len(self.fx.calls))
        notes = [value for key, value in job['checkpoints'].items() if key.startswith('original-internal-scope:')]
        self.assertEqual(6, len(notes))
        self.assertTrue(all(any(issue['field'] == 'detail' for issue in note['issues']) for note in notes))
        self.assertTrue(all('detail' not in material['public_fields'] for material in self.fx.editorial_inputs))
        self.assertTrue(all('许可证全文' in entry['facts']['detail'] for entry in self.originals(job)))

    def test_cached_accept_cannot_bypass_featured_scope_check_or_reissue_saved_http(self):
        self.fx.text += '\n' + 'Additional license material. ' * 800
        self.detail_overclaim = True
        runtime.enqueue(self.fx.root, dict(action='generate', ranking_type='weekly', period=self.weekly_period()))
        job = runtime.claim(self.fx.root, 'owner')
        policy = pipeline.selection.load_policy(self.fx.root)
        pipeline._save(self.fx.root, job, 'owner', 'screen', dict(
            source_review_contract=pipeline.understanding.SOURCE_REVIEW_CONTRACT, policy_snapshot=policy))
        record = digest.candidates(self.fx.root, ranking_type='weekly', period=self.weekly_period())['candidates'][0]
        model = self.fx.real_model(self.fx.root, base_url='https://provider.example/v1', model='fixture',
                                   api_key='fixture-only', client=self.fx.client)
        entry = pipeline._review_original(self.fx.root, job, 'owner', model, record, 'source-fixture', 37)
        self.assertFalse(entry.get('deferred'), entry)
        original_entry = copy.deepcopy(entry)
        evidence_url = entry['contexts'][0]['url']
        assessment = dict(precheck=dict(status='PASS', reasons=['Concrete workflow'], evidence_refs=[evidence_url]),
            scores={key:dict(score=8, reason='Documented practical value', evidence_refs=[evidence_url])
                    for key in pipeline.selection.DIMENSIONS}, flags=[], reason='Useful documented tool.')
        card = dict(candidate_id='fixture', input_hash='fixed-card', material=entry['observation'], ranking_type='weekly')
        score = dict(request_id='fixed-score')
        options = dict(reader_context=policy['reader_context'], editorial_position=policy['editorial_position'],
            understanding_contract=policy['understanding_contract'], introduction_contract=policy['introduction_contract'],
            editorial_scope=policy['editorial_scope'], featured=True)
        # Simulate a pre-fix public review that accepted the same frozen detail.
        # The real model adapter still produces and saves a synthetic receipt.
        with patch.object(pipeline.understanding, 'prose_scope_issues', return_value=[]):
            accepted = pipeline._review_editorial(self.fx.root, job, 'owner', model, card, entry,
                                                  assessment, score, 37, **options)
        self.assertEqual('accept', accepted['verdict'])
        frozen = copy.deepcopy(job['checkpoints']['editorial-input:fixture'])
        response = copy.deepcopy(job['checkpoints']['editorial-response:fixture'])
        self.assertEqual(2, len(self.fx.calls))
        with patch.object(pipeline.editorial, 'build_review_input', side_effect=AssertionError('must use frozen public fields')), patch.object(
                model, 'request', side_effect=AssertionError('must not reissue saved HTTP')):
            checked = pipeline._review_editorial(self.fx.root, job, 'owner', model, card, entry,
                                                 assessment, score, 37, **options)
            repeated = pipeline._review_editorial(self.fx.root, job, 'owner', model, card, entry,
                                                  assessment, score, 37, **options)
        self.assertEqual('defer', checked['verdict'])
        self.assertEqual(checked, repeated)
        self.assertTrue(any(issue['field'] == 'detail' for issue in checked['reading_scope_issues']))
        self.assertEqual(frozen, job['checkpoints']['editorial-input:fixture'])
        self.assertEqual(response, job['checkpoints']['editorial-response:fixture'])
        self.assertEqual('accept', response['receipt']['output']['verdict'])
        self.assertEqual(original_entry, entry)
        self.assertEqual(2, len(self.fx.calls))

    def test_restart_reuses_frozen_public_request_and_featured_assignment_without_http_reissue(self):
        self.rejected_url = 'https://projects.example/tool-5'
        checkpoint = runtime.checkpoint
        interrupted = False
        first_stage = None
        frozen = None

        def crash(root, job_id, owner, stage, value):
            nonlocal interrupted, first_stage
            checkpoint(root, job_id, owner, stage, value)
            if stage.startswith('editorial-response:') and not interrupted:
                interrupted = True
                first_stage = 'editorial-input:' + stage.split(':', 1)[1]
                raise OSError('Stopped after the saved public review response.')

        with patch.object(runtime, 'checkpoint', side_effect=crash):
            job, result = self.fx.run_job('weekly', self.weekly_period())
        self.assertEqual('failed', result['status'], result)
        self.assertEqual(14, len(self.fx.calls))
        frozen = copy.deepcopy(job['checkpoints'][first_stage])
        self.assertTrue(frozen['request']['material']['featured'])
        self.assertIn('detail', frozen['request']['material']['public_fields'])
        original_build = pipeline.editorial.build_review_input

        def no_first_rebuild(**kwargs):
            self.assertNotEqual(self.rejected_url, kwargs['record']['url'], 'Frozen first review must not rebuild.')
            return original_build(**kwargs)

        runtime.retry(self.fx.root, job['job_id'])
        with patch.object(pipeline.editorial, 'build_review_input', side_effect=no_first_rebuild), patch.object(
                pipeline.editorial, 'PUBLIC_INTRODUCTION_REVIEW_PROMPT', 'A later prompt cannot replace a frozen request.'):
            result = runtime.work_once(self.fx.root)
        self.assertEqual('completed', result['status'], result)
        self.assertEqual(19, len(self.fx.calls))
        with runtime._db(self.fx.root, write=False) as con:
            job['checkpoints'] = json.loads(con.execute('SELECT checkpoints FROM jobs WHERE job_id=?',
                (job['job_id'],)).fetchone()[0])
        self.assertEqual(frozen, job['checkpoints'][first_stage])
        self.assert_featured_order(job)


if __name__ == '__main__':
    unittest.main()
