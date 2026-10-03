from __future__ import annotations

import json
import base64
import contextlib
import io
import os
import subprocess
from contextlib import closing
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ai_notes import digest, digest_sources as sources


class DigestSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime.now(digest.BEIJING) - timedelta(minutes=5)
        self.clock = patch.object(sources, '_now', return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def configure(self, entries):
        p = self.root / 'config/digest_sources.json'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({'schema_version': 'digest-sources.v1', 'sources': entries}), encoding='utf-8')

    def feed(self, links):
        date = self.now.isoformat()
        return '<feed xmlns="http://www.w3.org/2005/Atom">' + ''.join(
            f'<entry><title>Useful tool {i}</title><link href="{url}"/><published>{date}</published><summary>Concrete workflow {i}</summary></entry>'
            for i, url in enumerate(links)) + '</feed>'

    def client(self, handler):
        value = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(value.close)
        return value

    def test_collection_default_discovered_and_idempotent_saved_run(self):
        self.configure([{'id': 'feed', 'type': 'rss', 'url': 'https://example.com/feed', 'weekdays': list(range(7))}])
        client = self.client(lambda request: httpx.Response(200, text=self.feed(['https://example.com/post']), headers={'etag': 'v1'}))
        result = sources.collect(self.root, client=client, run_id='one')
        batch = json.loads((self.root / result['batch_path']).read_text(encoding='utf-8'))
        self.assertEqual('ok', result['status'])
        self.assertTrue(result['ingest']['read_back_confirmed'])
        item = batch['candidates'][0]
        self.assertEqual('discovered', item['evidence_status'])
        self.assertIsNone(item['verified_at'])
        bad = self.client(lambda request: self.fail('saved run must not use network'))
        replay = sources.collect(self.root, client=bad, run_id='one')
        self.assertEqual('unchanged', replay['ingest']['status'])
        self.assertEqual(result['collected_at'], replay['collected_at'])
        with closing(sqlite3.connect(self.root / digest.DB_PATH)) as db:
            self.assertEqual(1, db.execute('select count(*) from batches').fetchone()[0])

    def test_known_candidates_do_not_use_new_budget(self):
        self.configure([{'id': 'feed', 'type': 'rss', 'url': 'https://example.com/feed'}])
        first = self.client(lambda request: httpx.Response(200, text=self.feed(['https://example.com/old'])))
        sources.collect(self.root, client=first, run_id='old')
        second = self.client(lambda request: httpx.Response(200, text=self.feed(['https://example.com/old', 'https://example.com/new', 'https://example.com/extra'])))
        result = sources.collect(self.root, client=second, run_id='new', limit=1)
        self.assertEqual(1, result['new_candidates'])
        self.assertEqual(1, result['known_observations'])
        self.assertEqual(2, result['ingest']['observations'])

    def test_304_keeps_original_fetch_timestamp_and_checks_cached_hash(self):
        client = self.client(lambda request: httpx.Response(200, text='real evidence', headers={'etag': 'abc'}))
        first = sources.fetch(self.root, 'https://example.com/a', client=client)
        def unchanged(request):
            self.assertEqual('abc', request.headers['if-none-match'])
            return httpx.Response(304)
        with patch.object(sources, '_now', return_value=self.now + timedelta(minutes=1)):
            second = sources.fetch(self.root, 'https://example.com/a', client=self.client(unchanged))
        self.assertEqual(first['fetched_at'], second['fetched_at'])
        self.assertNotEqual(first['checked_at'], second['checked_at'])
        self.assertEqual('revalidated', second['cache_status'])
        (self.root / first['body_path']).write_text('tampered', encoding='utf-8')
        with self.assertRaises(sources.SourceError):
            sources.fetch(self.root, 'https://example.com/a', client=self.client(unchanged))

    def test_failed_and_empty_sources_save_legal_batch(self):
        self.configure([{'id': 'broken', 'type': 'rss', 'url': 'https://example.com/broken'}, {'id': 'empty', 'type': 'rss', 'url': 'https://example.com/empty'}])
        def handler(request):
            if request.url.path == '/broken':
                raise httpx.ReadTimeout('timeout')
            return httpx.Response(200, text='<rss><channel/></rss>')
        result = sources.collect(self.root, client=self.client(handler), run_id='failed')
        self.assertEqual('partial', result['status'])
        self.assertEqual(['failed', 'empty'], [s['status'] for s in result['source_results']])
        self.assertEqual(0, result['ingest']['observations'])
        self.assertTrue(result['ingest']['read_back_confirmed'])

    def test_future_feed_date_not_accepted_and_unknown_date_kept_unknown(self):
        self.configure([{'id': 'feed', 'type': 'rss', 'url': 'https://example.com/feed'}])
        content = '<rss><channel><item><title>Undated</title><link>https://example.com/unknown</link></item><item><title>Future</title><link>https://example.com/future</link><pubDate>Wed, 01 Jan 2099 10:00:00 GMT</pubDate></item></channel></rss>'
        result = sources.collect(self.root, client=self.client(lambda _: httpx.Response(200, text=content)), run_id='dates')
        batch = json.loads((self.root / result['batch_path']).read_text(encoding='utf-8'))
        self.assertEqual(1, len(batch['candidates']))
        self.assertIsNone(batch['candidates'][0]['published_at'])
        self.assertEqual(1, result['source_results'][0]['future_items'])

    def test_hn_source_time_is_not_project_first_release(self):
        self.configure([{'id': 'hn', 'type': 'show_hn', 'url': 'https://hacker-news.firebaseio.com/v0/showstories.json'}])
        def handler(request):
            value = [123] if request.url.path.endswith('showstories.json') else {'id': 123, 'title': 'Show HN: A tool', 'url': 'https://github.com/Example/Tool.git', 'time': int(self.now.timestamp()), 'type': 'story'}
            return httpx.Response(200, json=value)
        result = sources.collect(self.root, client=self.client(handler), run_id='hn')
        batch = json.loads((self.root / result['batch_path']).read_text(encoding='utf-8'))
        self.assertEqual('https://github.com/example/tool', batch['candidates'][0]['url'])
        self.assertIsNone(batch['candidates'][0]['published_at'])
        self.assertEqual('project', batch['candidates'][0]['kind'])

    def test_huggingface_models_and_spaces_are_discovery_only(self):
        self.configure([{'id': 'models', 'type': 'hf_models', 'url': 'https://huggingface.co/api/models'}, {'id': 'spaces', 'type': 'hf_spaces', 'url': 'https://huggingface.co/api/spaces'}])
        result = sources.collect(self.root, client=self.client(lambda _: httpx.Response(200, json=[{'id': 'author/demo', 'lastModified': self.now.isoformat(), 'createdAt': '2024-01-01T00:00:00Z'}])), run_id='hf')
        batch = json.loads((self.root / result['batch_path']).read_text(encoding='utf-8'))
        self.assertEqual(2, len(batch['candidates']))
        for item in batch['candidates']:
            self.assertIsNone(item['published_at'])
            self.assertEqual('discovered', item['evidence_status'])
            self.assertEqual('project', item['kind'])

    def test_redirect_private_url_and_oversized_payload_rejected(self):
        redirect = self.client(lambda _: httpx.Response(302, headers={'location': 'http://127.0.0.1/private'}))
        with self.assertRaises(sources.SourceError):
            sources.fetch(self.root, 'https://example.com/a', client=redirect)
        big = self.client(lambda _: httpx.Response(200, content=b'x' * 100))
        with self.assertRaises(sources.SourceError):
            sources.fetch(self.root, 'https://example.com/a', client=big, max_bytes=30)

    def test_failed_run_allows_bounded_seven_day_retry(self):
        self.configure([{'id': 'feed', 'type': 'rss', 'url': 'https://example.com/feed'}])
        def failed(_):
            raise httpx.ReadTimeout('unavailable')
        first = sources.collect(self.root, client=self.client(failed), run_id='fail')
        self.assertEqual('failed', first['status'])
        content = self.feed(['https://example.com/old']).replace(self.now.isoformat(), (self.now - timedelta(days=3)).isoformat())
        retry = sources.collect(self.root, client=self.client(lambda _: httpx.Response(200, text=content)), run_id='retry')
        self.assertEqual(1, retry['new_candidates'])
        self.assertEqual(168, retry['source_results'][0]['lookback_hours'])
        normal = sources.collect(self.root, client=self.client(lambda _: httpx.Response(200, text=content)), run_id='normal')
        self.assertEqual(48, normal['source_results'][0]['lookback_hours'])
        self.assertEqual(0, normal['ingest']['observations'])

    def test_github_readme_pins_commit_and_retains_raw_receipts(self):
        commit = 'a' * 40
        def handler(request):
            if '/commits/' in request.url.path:
                value = {'sha': commit}
            elif request.url.path.endswith('/readme'):
                self.assertEqual(commit, request.url.params['ref'])
                value = {'encoding': 'base64', 'content': base64.b64encode(b'# Original\n').decode(), 'path': 'README.md'}
            else:
                value = {'default_branch': 'main'}
            return httpx.Response(200, json=value)
        result = sources.read_github(self.root, 'https://github.com/Example/Tool', client=self.client(handler))
        self.assertEqual(commit, result['commit_sha'])
        self.assertEqual('# Original\n', (self.root / result['document_path']).read_text())
        self.assertEqual(3, len(result['fetches']))
        self.assertEqual('discovered', result['evidence_status'])

    def test_offline_fixture_cli_does_not_need_live_network(self):
        self.configure([{'id': 'hf-blog', 'type': 'rss', 'url': 'https://huggingface.co/blog/feed.xml'}])
        fixture = Path(__file__).parent / 'fixtures/digest_sources/hf-blog.json'
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = sources.main(['collect', '--root', str(self.root), '--source', 'hf-blog', '--fixture', str(fixture), '--run-id', 'fixture'])
        self.assertEqual(0, code)
        self.assertTrue(json.loads(stdout.getvalue())['ingest']['read_back_confirmed'])

    def test_source_rotation_is_not_starved_by_first_source(self):
        self.configure([{'id': 'first', 'type': 'rss', 'url': 'https://example.com/one'}, {'id': 'second', 'type': 'rss', 'url': 'https://example.com/two'}])
        def handler(request):
            return httpx.Response(200, text=self.feed(['https://example.com/' + request.url.path.strip('/') + str(i) for i in range(3)]))
        result = sources.collect(self.root, client=self.client(handler), run_id='fair', limit=2)
        self.assertEqual([1, 1], [row['accepted_observations'] for row in result['source_results']])

    def test_new_discovery_from_cached_feed_cannot_backdate_discovery(self):
        self.configure([{'id': 'feed', 'type': 'rss', 'url': 'https://example.com/feed'}])
        content = self.feed(['https://example.com/tool'])
        sources.fetch(self.root, 'https://example.com/feed', client=self.client(lambda _: httpx.Response(200, text=content, headers={'etag': 'old'})))
        later = self.now + timedelta(minutes=1)
        with patch.object(sources, '_now', return_value=later):
            result = sources.collect(self.root, client=self.client(lambda _: httpx.Response(304)), run_id='cache-discovery')
        batch = json.loads((self.root / result['batch_path']).read_text(encoding='utf-8'))
        item = batch['candidates'][0]
        self.assertEqual(later.isoformat(), item['discovered_at'])
        self.assertEqual(self.now.isoformat(), item['source_observation']['fetched_at'])
        self.assertIsNone(item['verified_at'])

    def test_opaque_rss_guid_is_not_invented_as_article_url(self):
        self.configure([{'id': 'feed', 'type': 'rss', 'url': 'https://example.com/feed'}])
        raw = '<rss><channel><item><title>No URL</title><guid isPermaLink="false">opaque-id-123</guid></item></channel></rss>'
        result = sources.collect(self.root, client=self.client(lambda _: httpx.Response(200, text=raw)), run_id='opaque')
        self.assertEqual(0, result['new_candidates'])
        self.assertEqual('empty', result['status'])

    def test_hard_process_exit_releases_lock_and_replays_saved_batch(self):
        self.configure([{'id': 'feed', 'type': 'rss', 'url': 'https://example.com/feed'}])
        first = sources.collect(self.root, client=self.client(lambda _: httpx.Response(200, text=self.feed(['https://example.com/a']))), run_id='crash')
        script = 'import os,sys; from pathlib import Path; from ai_notes.digest_sources import _run_lock\nwith _run_lock(Path(sys.argv[1])):\n os._exit(17)\n'
        env = dict(os.environ)
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
        folder = self.root / sources.SOURCE_DIR / 'runs/crash'
        child = subprocess.run([sys.executable, '-c', script, str(folder)], env=env, capture_output=True, timeout=15)
        self.assertEqual(17, child.returncode, child.stderr.decode(errors='replace'))
        self.assertTrue((folder / '.lock').exists())
        replay = sources.collect(self.root, client=self.client(lambda _: self.fail('replay must not fetch')), run_id='crash')
        self.assertEqual(first['collected_at'], replay['collected_at'])
        self.assertEqual('unchanged', replay['ingest']['status'])

    def test_live_process_lock_cannot_be_stolen(self):
        folder = self.root / sources.SOURCE_DIR / 'runs/active'
        with sources._run_lock(folder):
            with self.assertRaises(sources.SourceError):
                sources.collect(self.root, run_id='active')


if __name__ == '__main__':
    unittest.main()
