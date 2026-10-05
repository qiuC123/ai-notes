from __future__ import annotations

import contextlib
import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_digest import archive, batch, candidate, digest, issue, item, update_record


class DiscoveryPresentationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        clock = patch.object(digest, '_now', return_value=datetime.fromisoformat('2026-11-10T12:00:00+08:00'))
        clock.start()
        self.addCleanup(clock.stop)

    def discovery(self, **kwargs):
        document = issue(**kwargs)
        document['presentation'] = 'discovery.v1'
        for selected in document['items']:
            selected.setdefault('supported_systems', None)
        return document

    def test_legacy_daily_weekly_monthly_render_bytes_remain_unchanged(self):
        digest.ingest(self.root, batch())
        # Golden hashes captured before discovery.v1 was implemented.
        expected = {
            ('daily', '2026-09-07'): '33aca68fb84effa2783674374d0dd78fedc76033e5e503603bdb902695b6193c',
            ('weekly', '2026-09-07'): '4829ae858b11c5fcc0d4fad70f6623a1f4844d09e7324fe944ce663bf0578903',
            ('monthly', '2026-09'): '065acaf18613fd96019e663c03009c24b8a149a8823de3316f877debf0d686c5',
        }
        for (kind, period), checksum in expected.items():
            with self.subTest(kind=kind):
                document = issue(kind, period)
                rendered = digest.render(self.root, document)
                self.assertEqual(checksum, hashlib.sha256(rendered.encode('utf-8')).hexdigest())
                self.assertNotIn('presentation', document)
                self.assertNotIn('supported_systems', document['items'][0])

    def test_only_explicit_supported_presentation_is_accepted(self):
        for marker in (None, '', 'discovery.v2', True, {}, []):
            with self.subTest(marker=marker):
                document = issue()
                document['presentation'] = marker
                with self.assertRaisesRegex(digest.DigestError, 'presentation'):
                    digest._validate_issue(document)
        document = self.discovery()
        digest._validate_issue(document)
        del document['items'][0]['supported_systems']
        with self.assertRaisesRegex(digest.DigestError, 'supported_systems'):
            digest._validate_issue(document)
        for value in ('', '   ', False, ['Windows'], 1):
            with self.subTest(value=value):
                document['items'][0]['supported_systems'] = value
                with self.assertRaisesRegex(digest.DigestError, 'supported_systems'):
                    digest._validate_issue(document)

    def test_daily_keeps_intro_system_and_link_but_not_internal_fact_prose(self):
        digest.ingest(self.root, batch())
        document = self.discovery()
        selected = document['items'][0]
        selected['supported_systems'] = 'Windows、macOS'
        original = copy.deepcopy(document)
        article = digest.render(self.root, document)
        self.assertIn(selected['summary'], article)
        self.assertIn('支持系统：Windows、macOS', article)
        self.assertIn('[Practical tool 官方入口](https://github.com/example/tool)', article)
        for internal in (selected['reason'], selected['audience'], selected['usage_conditions'], selected['detail'],
                         document['verification_note'], '核验日期', '资料：[原始资料', '真实发布时间'):
            self.assertNotIn(internal, article)
        self.assertEqual(original, document)
        selected['supported_systems'] = None
        self.assertNotIn('支持系统：', digest.render(self.root, document))

    def test_weekly_expands_three_features_without_changing_selection_rules(self):
        records = [candidate(f'https://github.com/example/tool{index}', title=f'Tool {index}',
                             summary=f'Practical function {index}.') for index in range(5)]
        digest.ingest(self.root, batch(items=records))
        selected = [item(record, featured=index < 3, detail=f'Concrete highlights {index}.')
                    for index, record in enumerate(records)]
        document = self.discovery(kind='weekly', period='2026-09-07', items=selected)
        article = digest.render(self.root, document)
        for index, entry in enumerate(selected):
            self.assertIn(entry['summary'], article)
            if index < 3:
                self.assertIn(entry['detail'], article)
            else:
                self.assertNotIn(entry['detail'], article)
        document['items'][0]['featured'] = False
        with self.assertRaisesRegex(digest.DigestError, 'featured count'):
            digest._validate_issue(document)

    def test_monthly_retention_and_minimum_still_apply(self):
        digest.ingest(self.root, batch())
        document = self.discovery(kind='monthly', period='2026-09')
        article = digest.render(self.root, document)
        self.assertIn('值得保留：' + document['items'][0]['retention_reason'], article)
        completed = self.discovery(kind='monthly', period='2026-09', prepared_at='2026-10-01T11:00:00+08:00')
        with self.assertRaisesRegex(digest.DigestError, 'at least 20'):
            digest._validate_issue(completed, final=True)

    def test_news_reading_dates_and_update_links_survive_without_system_guesses(self):
        news_url = 'https://news.example/announcement'
        news = candidate(news_url, kind='news', category='AI 应用', evidence_urls=[news_url],
                         event=dict(id='announcement', url=news_url, type='news',
                                    occurred_at='2026-09-07T10:00:00+08:00'))
        reading_url = 'https://author.example/post'
        reading = candidate(reading_url, kind='reading', category='博客、帖子与访谈', evidence_urls=[reading_url])
        update = update_record(occurred='2026-09-07T11:00:00+08:00')
        digest.ingest(self.root, batch(items=[news, reading, update]))
        document = self.discovery(items=[item(news), item(reading, featured=False, author='Author', original_date='2021-01-01'),
                                         item(update, featured=False, supported_systems='Windows')])
        article = digest.render(self.root, document)
        self.assertIn('新闻事件日期：2026-09-07。', article)
        self.assertIn('作者／受访者：Author；原文日期：2021-01-01。', article)
        self.assertIn('重要更新：Adds an export workflow.', article)
        self.assertIn('[事件原文](https://github.com/example/tool/releases/tag/v2)', article)
        self.assertEqual(1, article.count('支持系统：'))

    def test_empty_and_failed_coverage_is_visible_with_internal_evidence_preserved(self):
        value = batch(items=[])
        value['sources'] = [dict(name='Unavailable', url='https://example.com/unavailable', status='failed', detail='HTTP 503')]
        digest.ingest(self.root, value)
        document = self.discovery(kind='weekly', period='2026-09-07', items=[])
        article = digest.render(self.root, document)
        for text in ('精选 0 条', document['shortfall_reason'], '本期实际采集日期：2026-09-07',
                     '缺少采集的日期：2026-09-08', '来源失败：Unavailable：HTTP 503',
                     '[Unavailable](https://example.com/unavailable)（failed）'):
            self.assertIn(text, article)

    def test_discovery_archive_requires_exact_render_and_keeps_full_manifest(self):
        digest.ingest(self.root, batch())
        document = self.discovery()
        document['items'][0]['supported_systems'] = 'Windows'
        rendered = digest.render(self.root, document, draft=False)
        with self.assertRaisesRegex(digest.DigestError, 'exactly match'):
            archive(self.root, document, rendered.replace('支持系统：Windows\n\n', ''))
        result = archive(self.root, document, rendered)
        manifest = json.loads(Path(result['manifest_path']).read_text(encoding='utf-8'))
        self.assertEqual('discovery.v1', manifest['presentation'])
        for field in ('reason', 'audience', 'usage_conditions', 'evidence_urls', 'verification_level', 'supported_systems'):
            self.assertEqual(document['items'][0][field], manifest['items'][0][field])
        self.assertEqual(rendered, Path(result['article_path']).read_text(encoding='utf-8'))
        self.assertEqual('unchanged', digest.archive(self.root, document)['status'])
        legacy = copy.deepcopy(document)
        del legacy['presentation']
        with self.assertRaisesRegex(digest.DigestError, 'different content'):
            archive(self.root, legacy)

    def test_private_preview_cannot_turn_formal_render_into_a_preview(self):
        digest.ingest(self.root, batch())
        document = self.discovery()
        with contextlib.closing(digest._connect(self.root)) as connection:
            prepared = digest._prepare(connection, document)
        formal = digest._render(prepared, draft=True)
        source_url = document['items'][0]['evidence_urls'][0]
        prepared.update(preview=True, preview_source_dates={source_url: '2026-09-07'})
        self.assertEqual(formal, digest._render(prepared, draft=True))
        # Only the explicit private argument enables preview; dates are not
        # inferred from audit fields and the result cannot be formally archived.
        preview = digest._render_discovery(prepared, draft=True, preview=True)
        self.assertIn('介绍样式预览 · 示例 1 条', preview)
        self.assertIn('未重新筛选、评分或核验', preview)
        self.assertIn(document['items'][0]['summary'], preview)
        self.assertIn('https://github.com/example/tool', preview)
        self.assertIn('资料日期：2026-09-07', preview)
        for label in ('草稿 · 日榜', '北京时间', '精选', '数量说明', '重点推荐', '首次发现', '本期实际采集', '缺少采集', '来源失败'):
            self.assertNotIn(label, preview)
        with self.assertRaisesRegex(digest.DigestError, 'exactly match'):
            archive(self.root, document, preview)


if __name__ == '__main__':
    unittest.main()
