"""Fresh source support gates, bounded wire packets and immutable legacy requests."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx

from ai_notes import digest_pipeline as pipeline, digest_selection as selection
from ai_notes import digest_runtime as runtime, digest_verify_packet as packet
from tests import test_digest_public_pipeline as fixtures


ROOT = Path(__file__).resolve().parents[1]
URL = 'https://publisher.example/news/tool'
FEED = 'https://publisher.example/rss/'


def facts(kind='project', category='MCP 服务与连接器'):
    value = {key: '原文支持的具体文件用途' for key in (
        'title','summary','reason','audience','usage_conditions','detail','retention_reason')}
    value.update(category=category, open_source_status='unknown', evidence_urls=[URL],
        claims=[dict(field=key, text=value.get(key, category), evidence_url=URL, quote='文件工具')
                for key in ('summary','usage_conditions','category')])
    if kind == 'news':
        value['event_date'] = '2026-10-09T07:00:00 GMT'
    return dict(qualified=True, reason='具体文件用途', facts=value)


class SourceSupportGateTests(unittest.TestCase):
    def test_unknown_identity_is_allowed_for_functional_mcp_only_by_frozen_new_contract(self):
        output = facts()
        contexts = [dict(url=URL, text='文件工具')]
        record = dict(url=URL, kind='project')
        with self.assertRaisesRegex(runtime.RuntimeError, 'licence'):
            pipeline._facts(output, contexts, record)
        result = pipeline._facts(output, contexts, record, source_support_contract=selection.SOURCE_SUPPORT_CONTRACT)
        self.assertEqual('unknown', result['open_source_status'])
        self.assertEqual('MCP 服务与连接器', result['category'])

    def test_open_source_column_still_requires_identity_and_not_a_legal_interpretation(self):
        output = facts(category='开源项目')
        contexts = [dict(url=URL, text='文件工具，作者说明它是开源工具')]
        with self.assertRaisesRegex(runtime.RuntimeError, 'identity'):
            pipeline._facts(output, contexts, dict(url=URL, kind='project'), source_support_contract=selection.SOURCE_SUPPORT_CONTRACT)
        output['facts']['open_source_status'] = 'confirmed'
        output['facts']['claims'].append(dict(field='open_source_status', text='confirmed', evidence_url=URL, quote='作者说明它是开源工具'))
        result = pipeline._facts(output, contexts, dict(url=URL, kind='project'), source_support_contract=selection.SOURCE_SUPPORT_CONTRACT)
        self.assertEqual('confirmed', result['open_source_status'])
        self.assertNotIn('license', {c['field'] for c in result['claims']})

    def test_rss_date_keeps_real_feed_url_and_normalizes_only_explicit_zone(self):
        rss = '<rss><channel><item><link>'+URL+'</link><pubDate>Fri, 09 Oct 2026 07:00:00 GMT</pubDate></item></channel></rss>'
        contexts = [dict(url=URL, text='文件工具 October 9, 2026'), dict(url=FEED, text=rss)]
        record = dict(url=URL, kind='news')
        hints = packet.build_verify_packet(contexts, record)['news_date_sources']
        output = facts(kind='news', category='AI 应用')
        output['facts']['evidence_urls'].append(FEED)
        output['facts']['claims'].append(dict(field='event_date', text=output['facts']['event_date'], evidence_url=FEED, quote=hints[0]['quote']))
        before = copy.deepcopy(output)
        with self.assertRaises(ValueError):
            pipeline._facts(output, contexts, record)
        result = pipeline._facts(output, contexts, record, source_support_contract=selection.SOURCE_SUPPORT_CONTRACT, news_date_sources=hints)
        self.assertEqual('2026-10-09T07:00:00+00:00', result['event_date'])
        self.assertEqual(FEED, result['claims'][-1]['evidence_url'])
        self.assertEqual(before, output)  # Immutable paid raw output.
        wrong = copy.deepcopy(hints)
        wrong[0]['event_url'] = URL + '/another'
        with self.assertRaisesRegex(runtime.RuntimeError, 'date lacks'):
            pipeline._facts(output, contexts, record, source_support_contract=selection.SOURCE_SUPPORT_CONTRACT, news_date_sources=wrong)

    def test_date_only_is_not_promoted_and_missing_zone_is_not_guessed(self):
        self.assertEqual(dict(event_date='2026-10-09', date_precision='date', timezone='unknown'), packet.parse_publication_date('2026-10-09'))
        self.assertIsNone(packet.parse_publication_date('2026-10-09T07:00:00'))

    def test_split_rss_pubdate_requires_contiguous_referenced_coverage(self):
        date = 'Fri, 09 Oct 2026 07:00:00 GMT'
        prefix = '<item><link>'+URL+'</link><description>'
        body = prefix + 'x' * (1790-len(prefix)-len('</description><pubDate>')) + '</description><pubDate>'+date+'</pubDate></item>'
        contexts = [dict(url=URL, text='文件工具'), dict(url=FEED, text=body)]
        hint = dict(event_url=URL, feed_url=FEED, quote=body, raw_date=date,
                    event_date='2026-10-09T07:00:00+00:00', date_precision='timestamp')
        output = facts(kind='news', category='AI 应用')
        output['facts']['evidence_urls'].append(FEED)
        for quote in (body[:1800], body[1800:]):
            output['facts']['claims'].append(dict(field='event_date', text=output['facts']['event_date'], evidence_url=FEED, quote=quote))
        record = dict(url=URL, kind='news')
        result = pipeline._facts(output, contexts, record, source_support_contract=selection.SOURCE_SUPPORT_CONTRACT, news_date_sources=[hint])
        self.assertEqual('2026-10-09T07:00:00+00:00', result['event_date'])
        output['facts']['claims'].pop()
        with self.assertRaisesRegex(runtime.RuntimeError, 'date lacks'):
            pipeline._facts(output, contexts, record, source_support_contract=selection.SOURCE_SUPPORT_CONTRACT, news_date_sources=[hint])

    def test_article_date_cannot_discard_known_matching_feed_timestamp(self):
        output = facts(kind='news', category='AI 应用')
        output['facts']['event_date'] = '2026-10-09'
        contexts = [dict(url=URL, text='文件工具 October 9, 2026')]
        output['facts']['claims'].append(dict(field='event_date', text='2026-10-09', evidence_url=URL, quote='October 9, 2026'))
        with self.assertRaisesRegex(runtime.RuntimeError, 'cannot be reduced'):
            pipeline._facts(output, contexts, dict(url=URL, kind='news'), source_support_contract=selection.SOURCE_SUPPORT_CONTRACT,
                news_date_sources=[dict(event_url=URL, date_precision='timestamp')])

    def test_wire_shrink_that_cannot_fit_matching_item_keeps_last_packet_for_local_guard(self):
        record = dict(url=URL, title='News', kind='news', summary='x' * 20000)
        rss = '<rss><channel><item><link>'+URL+'</link><description>'+'y'*18000+'</description><pubDate>Fri, 09 Oct 2026 07:00:00 GMT</pubDate></item></channel></rss>'
        contexts = [dict(url=FEED, text=rss)]
        request, _, receipt = pipeline._bounded_source_request(dict(material=dict(candidate=record, ranking_type='daily')), contexts, [], record)
        self.assertGreater(receipt['wire_input_chars'], 60000)
        self.assertEqual('verify-facts-v19-source-support', request['stage'])

    def test_wire_packet_budget_preserves_full_sources_and_has_no_licence_gate(self):
        contexts = [dict(url='https://source.example/'+str(i), text=('Concrete file tool.\n' * 1000), fetched_at='2026-10-09T08:00:00+08:00') for i in range(6)]
        before = copy.deepcopy(contexts)
        record = dict(url=contexts[0]['url'], title='Concrete file tool', kind='project')
        request = dict(material=dict(candidate=record, ranking_type='daily', introduction_contract='discovery.v1', selection_refinement_contract='evidence-focus.v1'))
        new, supplied, receipt = pipeline._bounded_source_request(request, contexts, [], record)
        measured = len(runtime.model_wire_system(new['system'], new['output_schema'])) + len(runtime._json(new['material']))
        self.assertEqual(measured, receipt['wire_input_chars'])
        self.assertLessEqual(measured, 60000)
        self.assertLessEqual(sum(len(c['text']) for c in supplied), 20000)
        self.assertEqual(before, contexts)
        self.assertNotIn('requires confirmed open_source_status and license IDs', new['system'])
        self.assertEqual('verify-facts-v19-source-support', new['stage'])

    def test_new_policy_does_not_change_numeric_rules_or_upgrade_a_legacy_prompt(self):
        current = selection.load_policy(ROOT)
        old = copy.deepcopy(current)
        old.pop('source_support_contract')
        old_prompt = selection.load_prompt(ROOT, old)
        self.assertNotIn(selection.SOURCE_SUPPORT_PROMPT_MARKER, old_prompt)
        self.assertIn(selection.SOURCE_SUPPORT_PROMPT_MARKER, selection.load_prompt(ROOT, current))
        self.assertEqual(37, pipeline._request_budget(current, 12))
        for bad in (None, '', 'source-support.v2'):
            with self.assertRaises(selection.SelectionError):
                selection.validate_policy(dict(current, source_support_contract=bad))


class FreshPipelineTests(unittest.TestCase):
    def test_one_bounded_generation_freezes_new_stages_and_keeps_unknown_notes_in_review(self):
        fixture = fixtures.PublicPipelineTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fx = fixture.fx
        (fx.root / selection.POLICY_PATH).write_text(json.dumps(selection.load_policy(ROOT)), encoding='utf-8')
        fx.client.close()
        def respond(request):
            response = fixture.respond(request)
            body = response.json()
            material = json.loads(json.loads(request.content)['messages'][1]['content'])
            value = json.loads(body['choices'][0]['message']['content'])
            if material.get('source_support_contract') == selection.SOURCE_SUPPORT_CONTRACT and value.get('qualified'):
                value['evidence']['open_source_status'] = value['evidence']['license']
                value['evidence']['license'] = []
                value['understanding']['unknowns'] = ['网页入口是否需要登录未知。']
            body['choices'][0]['message']['content'] = json.dumps(value)
            return httpx.Response(200, json=body)
        fx.client = httpx.Client(transport=httpx.MockTransport(respond))
        self.addCleanup(fx.client.close)
        job, result = fx.run_job()
        self.assertEqual('completed', result['status'], result)
        self.assertEqual(6, result['result']['item_count'], result)
        self.assertEqual(19, len(fx.calls))  # 1 screen + 6*(verify, score, editorial).
        originals = [v for k,v in job['checkpoints'].items() if k.startswith('original-input:')]
        reviews = [v for k,v in job['checkpoints'].items() if k.startswith('editorial-input:')]
        self.assertEqual(6, len(originals))
        self.assertTrue(all(v['request']['stage'] == 'verify-facts-v19-source-support' for v in originals))
        self.assertTrue(all('full_sources' in v for v in originals))
        self.assertTrue(all(v['request']['material']['selection_limits']['unknowns'] == ['网页入口是否需要登录未知。'] for v in reviews))
        old = copy.deepcopy(job['checkpoints'])
        with patch.object(fx.real_model, 'request', side_effect=AssertionError('terminal job must not repeat paid calls')):
            self.assertEqual('idle', runtime.work_once(fx.root, job_id=job['job_id'])['status'])
        with runtime._db(fx.root, write=False) as con:
            stored = json.loads(con.execute('SELECT checkpoints FROM jobs WHERE job_id=?', (job['job_id'],)).fetchone()[0])
        self.assertEqual(old, stored)


if __name__ == '__main__':
    unittest.main()
