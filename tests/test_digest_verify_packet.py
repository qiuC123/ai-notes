"""Offline source bounds and provenance checks; no provider calls or state writes."""
import copy
import hashlib
import unittest

from ai_notes import digest_understanding as understanding
from ai_notes import digest_verify_packet as packet


def context(url, text, *, full_document=None, ranges=None):
    document = text if full_document is None else full_document
    ranges = [{'start': 0, 'end': len(text)}] if ranges is None else ranges
    return {'url': url, 'text': text, 'fetched_at': '2026-10-10T12:00:00+08:00',
            'source_scope': {'schema_version': 'digest-source-scope.v1',
                'coverage': 'complete_text' if text == document and ranges == [{'start': 0, 'end': len(text)}] else 'excerpt',
                'document_chars': len(document), 'supplied_chars': len(text), 'ranges': ranges,
                'document_sha256': hashlib.sha256(document.encode()).hexdigest(),
                'reader': 'fixture-original', 'commit_sha': 'a' * 40, 'file_path': 'README.md'}}


def item(link, stamp, *, field='pubDate'):
    return f'<item><title>Original announcement</title><link>{link}</link><{field}>{stamp}</{field}></item>'


class VerifyPacketTests(unittest.TestCase):
    def test_total_text_budget_is_shared_and_originals_stay_unchanged(self):
        originals = [context(f'https://example.com/{i}', str(i) * 10000) for i in range(3)]
        before = copy.deepcopy(originals)
        result = packet.build_verify_packet(originals, {'url': 'https://example.com/project', 'title': 'Useful'}, max_chars=20000)
        self.assertEqual(20000, result['supplied_text_chars'])
        self.assertEqual([6668, 6666, 6666], [len(value['text']) for value in result['contexts']])
        self.assertEqual(before, originals)
        self.assertTrue(all(value['source_scope']['coverage'] == 'excerpt' for value in result['contexts']))
        understanding.source_documents(result['contexts'])

    def test_prefix_and_candidate_neighbourhood_are_literal_slices(self):
        text = 'Concrete purpose and use. ' + 'x' * 500 + ' Parrot useful meeting recording ' + 'y' * 300
        original = context('https://example.com/parrot', text)
        result = packet.build_verify_packet([original], {'url': original['url'], 'title': 'Parrot'}, max_chars=160)
        supplied = result['contexts'][0]
        self.assertTrue(supplied['text'].startswith('Concrete purpose and use.'))
        self.assertIn('Parrot useful meeting recording', supplied['text'])
        self.assertEqual(supplied['text'], '\n\n'.join(text[value['start']:value['end']] for value in supplied['source_scope']['ranges']))
        self.assertEqual(original['source_scope']['document_sha256'], supplied['source_scope']['document_sha256'])
        self.assertLessEqual(len(supplied['text']), 160)
        understanding.source_documents(result['contexts'])

    def test_compiled_ranges_map_back_to_existing_original_coordinates(self):
        document = 'First purpose. ' + 'A' * 70 + 'skip' * 20 + 'Second Parrot capability. ' + 'B' * 90
        spans = [{'start': 0, 'end': 70}, {'start': 165, 'end': len(document)}]
        text = '\n\n'.join(document[value['start']:value['end']] for value in spans)
        original = context('https://example.com/parrot', text, full_document=document, ranges=spans)
        result = packet.build_verify_packet([original], {'url': original['url'], 'title': 'Parrot'}, max_chars=100)
        scope = result['contexts'][0]['source_scope']
        self.assertEqual('excerpt', scope['coverage'])
        self.assertTrue(any(value['start'] >= 165 for value in scope['ranges']))
        self.assertTrue(all(any(old['start'] <= value['start'] < value['end'] <= old['end'] for old in spans) for value in scope['ranges']))
        self.assertEqual(original['source_scope']['document_sha256'], scope['document_sha256'])
        understanding.source_documents(result['contexts'])

    def test_unproven_compiled_coordinates_stay_unknown(self):
        original = context('https://example.com/tool', 'A' * 40 + '--' + 'B' * 40,
                           full_document='A' * 100 + 'B' * 100,
                           ranges=[{'start': 0, 'end': 40}, {'start': 100, 'end': 140}])
        result = packet.build_verify_packet([original], {'url': original['url']}, max_chars=30)
        scope = result['contexts'][0]['source_scope']
        self.assertEqual('unknown', scope['coverage'])
        self.assertEqual([], scope['ranges'])
        self.assertIsNone(scope['document_chars'])
        self.assertEqual(original['source_scope']['document_sha256'], scope['document_sha256'])
        understanding.source_documents(result['contexts'])

    def test_missing_scope_does_not_invent_document_hash_or_complete_coverage(self):
        original = {'url': 'https://example.com/tool', 'text': 'Original useful description ' * 20}
        result = packet.build_verify_packet([original], {'url': original['url']}, max_chars=100)
        scope = result['contexts'][0]['source_scope']
        self.assertEqual('unknown', scope['coverage'])
        self.assertIsNone(scope['document_chars'])
        self.assertIsNone(scope['document_sha256'])
        self.assertEqual([], scope['ranges'])
        understanding.source_documents(result['contexts'])

    def test_short_source_preserves_its_complete_or_unknown_scope(self):
        original = context('https://example.com/tool', 'Known purpose.')
        result = packet.build_verify_packet([original], {'url': original['url']})
        self.assertEqual(original, result['contexts'][0])
        self.assertIsNot(original, result['contexts'][0])

    def test_official_matching_feed_retains_only_exact_item_and_its_feed_origin(self):
        target = 'https://openai.com/index/asana-browser-agent'
        own = item(target, 'Fri, 09 Oct 2026 07:00:00 GMT')
        rss = '<rss><channel><lastBuildDate>Sat, 10 Oct 2026 04:23:30 GMT</lastBuildDate>' + item('https://openai.com/index/neighbour', 'Sun, 11 Oct 2026 10:00:00 GMT') + own + '</channel></rss>'
        original = context('https://openai.com/news/rss.xml', rss)
        article = context(target, 'Article says October 9, 2026 and describes its actual event.')
        before = copy.deepcopy([original, article])
        result = packet.build_verify_packet([original, article], {'url': target, 'kind': 'news', 'title': 'Asana'})
        retained = result['contexts'][0]
        self.assertEqual(own, retained['text'])
        self.assertEqual(original['url'], retained['url'])
        self.assertEqual(own, rss[retained['source_scope']['ranges'][0]['start']:retained['source_scope']['ranges'][0]['end']])
        hint = result['news_date_sources'][0]
        self.assertEqual(target, hint['event_url'])
        self.assertEqual(original['url'], hint['feed_url'])
        self.assertEqual('2026-10-09T07:00:00+00:00', hint['event_date'])
        self.assertEqual('pubDate', hint['publication_field'])
        self.assertEqual(own, hint['quote'])
        self.assertNotIn('lastBuildDate', retained['text'])
        self.assertEqual(before, [original, article])
        understanding.source_documents(result['contexts'])

    def test_neighbour_or_mismatched_publisher_cannot_supply_date(self):
        target = 'https://openai.com/index/asana'
        for feed_url, linked in [('https://openai.com/news/rss.xml', 'https://openai.com/index/other'),
                                 ('https://aggregator.example/rss', target)]:
            with self.subTest(feed=feed_url):
                rss = '<rss><channel>' + item(linked, 'Fri, 09 Oct 2026 07:00:00 GMT') + '</channel></rss>'
                result = packet.build_verify_packet([context(feed_url, rss), context(target, 'Original article')], {'url': target, 'kind': 'news'})
                self.assertEqual([], result['news_date_sources'])
                self.assertEqual([target], [value['url'] for value in result['contexts']])

    def test_feed_namespace_and_truncated_feed_keep_complete_item_provenance(self):
        target = 'https://openai.com/index/asana'
        own = '<item><title>Asana</title><link>' + target + '</link><content:encoded><![CDATA[Original publisher body]]></content:encoded><pubDate>Fri, 09 Oct 2026 07:00:00 GMT</pubDate></item>'
        rss = '<?xml version="1.0"?><rss xmlns:content="https://purl.org/rss/1.0/modules/content/"><channel>' + own + '<item><title>truncated neighbour'
        result = packet.build_verify_packet([context('https://openai.com/news/rss.xml', rss)], {'url': target, 'kind': 'news'})
        self.assertEqual(own, result['contexts'][0]['text'])
        self.assertEqual('2026-10-09T07:00:00+00:00', result['news_date_sources'][0]['event_date'])
        understanding.source_documents(result['contexts'])

    def test_embedded_rss_example_in_article_is_not_a_date_hint(self):
        target = 'https://openai.com/index/asana'
        text = 'An article discussing RSS examples: <rss><channel>' + item(target, '2026-10-09') + '</channel></rss>'
        result = packet.build_verify_packet([context(target, text)], {'url': target, 'kind': 'news'})
        self.assertEqual([], result['news_date_sources'])
        self.assertEqual(text, result['contexts'][0]['text'])

    def test_updated_and_feed_refresh_are_never_publication_dates(self):
        target = 'https://openai.com/index/asana'
        own = item(target, 'Fri, 09 Oct 2026 07:00:00 GMT', field='updated')
        rss = '<rss><channel><lastBuildDate>Fri, 09 Oct 2026 07:00:00 GMT</lastBuildDate>' + own + '</channel></rss>'
        result = packet.build_verify_packet([context('https://openai.com/news/rss.xml', rss)], {'url': target, 'kind': 'news'})
        self.assertEqual(own, result['contexts'][0]['text'])
        self.assertEqual([], result['news_date_sources'])

    def test_timestamp_without_timezone_and_date_only_keep_distinct_precision(self):
        self.assertIsNone(packet.parse_publication_date('2026-10-09T07:00:00'))
        self.assertIsNone(packet.parse_publication_date('Fri, 09 Oct 2026 07:00:00'))
        self.assertIsNone(packet.parse_publication_date('Fri, 09 Oct 2026 07:00:00 CST'))
        self.assertEqual({'event_date': '2026-10-09', 'date_precision': 'date', 'timezone': 'unknown'}, packet.parse_publication_date('2026-10-09'))
        self.assertIsNone(packet.parse_publication_date('2026-02-30'))

    def test_explicit_rfc_iso_and_mixed_gmt_timestamps_normalise_without_guessing(self):
        for value in ('Fri, 09 Oct 2026 07:00:00 GMT', '2026-10-09T07:00:00Z', '2026-10-09T07:00:00 GMT'):
            with self.subTest(value=value):
                self.assertEqual({'event_date': '2026-10-09T07:00:00+00:00', 'date_precision': 'timestamp'}, packet.parse_publication_date(value))
        self.assertEqual('2026-10-09T15:00:00+08:00', packet.parse_publication_date('Fri, 09 Oct 2026 15:00:00 +0800')['event_date'])
        self.assertEqual('2026-10-09T15:00:00+08:00', packet.parse_publication_date('2026-10-09T15:00:00+08:00')['event_date'])

    def test_event_url_takes_priority_over_project_discovery_url(self):
        target = 'https://openai.com/index/real-event'
        own = item(target, '2026-10-09')
        rss = '<rss><channel>' + own + '</channel></rss>'
        result = packet.build_verify_packet([context('https://openai.com/news/rss.xml', rss)],
            {'url': 'https://openai.com/index/discovery', 'kind': 'news', 'event': {'url': target}})
        self.assertEqual(target, result['news_date_sources'][0]['event_url'])
        self.assertEqual('unknown', result['news_date_sources'][0]['timezone'])

    def test_conflicting_target_items_do_not_choose_one_publication_date(self):
        target = 'https://openai.com/index/asana'
        rss = '<rss><channel>' + item(target, '2026-10-09') + item(target, '2026-10-10') + '</channel></rss>'
        result = packet.build_verify_packet([context('https://openai.com/news/rss.xml', rss)], {'url': target, 'kind': 'news'})
        self.assertEqual([], result['contexts'])
        self.assertEqual([], result['news_date_sources'])
        self.assertTrue(result['omitted_sources'])

    def test_matching_item_is_never_cut_to_fit_and_loses_no_date_evidence(self):
        target = 'https://openai.com/index/asana'
        rss = '<rss><channel>' + item(target, '2026-10-09') + '</channel></rss>'
        with self.assertRaisesRegex(ValueError, 'matching RSS items exceed'):
            packet.build_verify_packet([context('https://openai.com/news/rss.xml', rss)], {'url': target, 'kind': 'news'}, max_chars=20)

    def test_duplicate_sources_are_deduplicated_without_conflicting_text(self):
        original = context('https://example.com/tool', 'Useful purpose')
        result = packet.build_verify_packet([original, copy.deepcopy(original)], {'url': original['url']})
        self.assertEqual(1, len(result['contexts']))
        with self.assertRaisesRegex(ValueError, 'duplicate source URL'):
            packet.build_verify_packet([original, context(original['url'], 'Different source text')], {'url': original['url']})


if __name__ == '__main__':
    unittest.main()
