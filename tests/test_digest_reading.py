"""Original-file fixtures exercise the reader without remote candidate code."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import logging
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import httpx

from ai_notes import digest_reading as reading
from ai_notes import digest_sources as sources, digest_selection as selection
from ai_notes.digest_passages import build_passages
from ai_notes.digest_understanding import source_documents


SHA = 'a' * 40
ROOT_URL = 'https://github.com/example/tool'
API = 'https://api.github.com/repos/example/tool'
RAW = 'https://raw.githubusercontent.com/example/tool/' + SHA + '/'


class ProjectReadingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.requests = []
        unavailable = patch.object(reading, 'package_selected_documents', return_value={
            'tool': 'gitingest', 'status': 'unavailable', 'available': False})
        unavailable.start()
        self.addCleanup(unavailable.stop)

    def client(self, files=None, *, readme='# Demo\nA file organizer.\n', tree_truncated=False,
               failed=(), tree_extra=(), redirect=None, expected_ref='main'):
        files = files or {}
        def handle(request):
            url = str(request.url)
            self.requests.append(url)
            if url in failed:
                return httpx.Response(503, text='not available')
            if redirect and url == redirect[0]:
                return httpx.Response(302, headers={'location': redirect[1]})
            if url == API:
                return httpx.Response(200, json={'default_branch': 'main'})
            if '/commits/' in request.url.path:
                self.assertEqual(expected_ref, request.url.path.split('/commits/', 1)[1])
                return httpx.Response(200, json={'sha': SHA})
            if request.url.path.endswith('/readme'):
                self.assertEqual(SHA, request.url.params['ref'])
                return httpx.Response(200, json={'path': 'README.md', 'encoding': 'base64',
                    'content': base64.b64encode(readme.encode()).decode()})
            if '/git/trees/' in request.url.path:
                self.assertEqual('/repos/example/tool/git/trees/' + SHA, request.url.path)
                self.assertEqual('1', request.url.params['recursive'])
                rows = [{'path': path, 'type': 'blob', 'mode': '100644'} for path in files]
                return httpx.Response(200, json={'sha': 'b' * 40, 'tree': rows + list(tree_extra),
                                               'truncated': tree_truncated})
            if url.startswith(RAW):
                path = request.url.path.split('/' + SHA + '/', 1)[1]
                raw = files.get(path, 'Read another version.'.encode())
                return httpx.Response(200, content=raw if isinstance(raw, bytes) else raw.encode())
            self.fail('unexpected remote fetch: ' + url)
        client = httpx.Client(transport=httpx.MockTransport(handle))
        self.addCleanup(client.close)
        return client

    def test_selected_originals_share_commit_and_raw_receipts(self):
        files = {'LICENSE': 'MIT License\nCopyright Authors.\n',
                 'docs/installation.md': 'Windows: download the installer.\n',
                 'docs/usage.md': 'Choose a folder, then organize.\n',
                 'src/main.py': 'raise SystemExit()'}
        packet = reading.read_project(self.root, ROOT_URL, client=self.client(files))
        self.assertEqual('ok', packet['status'])
        self.assertEqual(SHA, packet['commit_sha'])
        self.assertEqual(4, len(packet['contexts']))
        self.assertEqual(4, packet['coverage']['supplemental_requests'])
        self.assertEqual(7, len(packet['receipts']))
        self.assertEqual({'README.md', 'LICENSE', 'docs/installation.md', 'docs/usage.md'},
                         {row['source_scope']['file_path'] for row in packet['contexts']})
        self.assertFalse(any('src/main.py' in url or '/releases/' in url for url in self.requests))
        for context in packet['contexts']:
            scope = context['source_scope']
            self.assertEqual(SHA, scope['commit_sha'])
            self.assertIn('/blob/' + SHA + '/', context['url'])
            self.assertEqual('complete_text', scope['coverage'])
            self.assertEqual(len(context['text']), scope['document_chars'])
            self.assertEqual([{'start': 0, 'end': len(context['text'])}], scope['ranges'])
            fetched_url = API + '/readme?ref=' + SHA if scope['file_path'] == 'README.md' else RAW + scope['file_path']
            receipt = next(row for row in packet['receipts'] if row['url'] == fetched_url)
            self.assertEqual(receipt['fetched_at'], context['fetched_at'])
        for document in packet['documents']:
            raw = (self.root / document['document_path']).read_bytes()
            self.assertEqual(document['document_sha256'], hashlib.sha256(raw).hexdigest())
        for receipt in packet['receipts']:
            saved = json.loads((self.root / receipt['receipt_path']).read_text())
            self.assertEqual('ok', saved['status'])

    def test_missing_tool_does_not_discard_native_contexts(self):
        packet = reading.read_project(self.root, ROOT_URL, client=self.client({'LICENSE': 'MIT License.'}))
        self.assertEqual('unavailable', packet['gitingest']['status'])
        self.assertEqual('ok', packet['status'])
        self.assertEqual(2, len(packet['contexts']))

    def test_revalidated_context_retains_the_original_receipt_fetch_time(self):
        original_time = '2026-10-03T11:00:00+08:00'
        checked_time = '2026-10-04T11:00:00+08:00'
        with patch.object(sources, '_stamp', return_value=original_time):
            first = reading.read_project(self.root, ROOT_URL, client=self.client({'LICENSE': 'MIT License.'}))
        client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(304)))
        self.addCleanup(client.close)
        with patch.object(sources, '_stamp', return_value=checked_time):
            second = reading.read_project(self.root, ROOT_URL, client=client)
        self.assertEqual(first['contexts'], second['contexts'])
        self.assertTrue(all(row['fetched_at'] == original_time for row in second['contexts']))
        self.assertTrue(all(row['cache_status'] == 'revalidated' for row in second['receipts']))
        self.assertTrue(all(row['checked_at'] == checked_time for row in second['receipts']))

    def test_real_reader_packet_can_prepare_a_source_scoped_selection_card(self):
        packet = reading.read_project(self.root, ROOT_URL, client=self.client({
            'LICENSE': 'MIT License.', 'docs/usage.md': 'Choose a folder, then organize.'}))
        contexts = packet['contexts']
        record = dict(url=ROOT_URL, canonical_url=ROOT_URL, title='Fixture file organizer',
                      category='开源项目', kind='project', summary='A file organizer.', reason='A reusable workflow.',
                      source_urls=[ROOT_URL], evidence_urls=[row['url'] for row in contexts],
                      evidence_status='verified', verification_level='documented',
                      verified_at='2026-10-04T11:00:00+08:00', discovered_at='2026-10-04T11:00:00+08:00')
        cid = selection.candidate_id(record)
        purpose_passage = build_passages(contexts)[0]
        understanding = dict(schema_version='digest-understanding.v1',
            purpose=dict(text='A file organizer.', passage_ids=[purpose_passage['id']]),
            input=dict(text=None, passage_ids=[]), output=dict(text=None, passage_ids=[]),
            operations=[], conditions=[], unknowns=['Input and output need further editorial reading.'],
            proof_map={purpose_passage['id']: {key: purpose_passage[key]
                       for key in ('evidence_url', 'quote', 'heading_path')}},
            source_documents=source_documents(contexts), reading_scope_issues=[])
        policy = selection.load_policy(Path(__file__).resolve().parents[1])
        policy['understanding_contract'] = 'project-reading.v1'
        with patch.object(selection, '_query', return_value={'candidates': [record]}):
            prepared = selection.prepare(self.root, 'daily', '2026-10-04', evidence_context=contexts,
                candidate_contexts={cid: contexts}, policy_snapshot=policy,
                prompt_snapshot='Fixture source understanding prompt', source_understanding={cid: understanding})
        self.assertEqual(contexts, prepared['cards'][0]['evidence_context'])
        saved = selection.load_preparation(self.root, prepared['prepare_id'])
        self.assertEqual(contexts, saved['cards'][0]['evidence_context'])
        self.assertEqual(understanding['source_documents'], saved['cards'][0]['understanding']['source_documents'])

    def test_license_fetch_failure_is_unknown_even_when_inventory_names_it(self):
        packet = reading.read_project(self.root, ROOT_URL,
            client=self.client({'LICENSE': 'MIT License.', 'docs/usage.md': 'Use it.'}, failed=(RAW + 'LICENSE',)))
        self.assertEqual('partial', packet['status'])
        self.assertEqual('unknown', packet['coverage']['questions']['license'])
        self.assertEqual('read', packet['coverage']['questions']['usage'])
        self.assertFalse(any(row['source_scope']['file_path'] == 'LICENSE' for row in packet['contexts']))
        failure = next(row for row in packet['documents'] if row['file_path'] == 'LICENSE')
        self.assertEqual('unknown', failure['coverage'])
        failed_receipts = [row for row in packet['receipts'] if row['status'] == 'failed']
        self.assertEqual(1, len(failed_receipts))
        self.assertEqual('failed', json.loads((self.root / failed_receipts[0]['receipt_path']).read_text())['status'])
        self.assertEqual(RAW + 'LICENSE', packet['failures'][0]['url'])

    def test_tree_failure_retains_readme_and_does_not_guess_filenames(self):
        tree_url = API + '/git/trees/' + SHA + '?recursive=1'
        packet = reading.read_project(self.root, ROOT_URL,
            client=self.client({'LICENSE': 'MIT License.'}, failed=(tree_url,)))
        self.assertEqual('partial', packet['status'])
        self.assertEqual('failed', packet['reading_plan']['tree_status'])
        self.assertEqual(1, len(packet['contexts']))
        self.assertFalse(any(url.startswith(RAW) for url in self.requests))

    def test_no_license_in_partial_tree_remains_unknown(self):
        packet = reading.read_project(self.root, ROOT_URL, client=self.client(tree_truncated=True))
        self.assertTrue(packet['coverage']['tree_truncated'])
        self.assertEqual('unknown', packet['coverage']['questions']['license'])
        self.assertNotIn('license_exists', packet)

    def test_document_and_http_budgets_do_not_read_entire_repository(self):
        files = {'LICENSE': 'MIT License.', 'COPYING': 'Conditions.',
                 'docs/install.md': 'Install.', 'docs/setup.md': 'Setup.',
                 'docs/usage.md': 'Usage.', 'docs/guide.md': 'Guide.',
                 'docs/tutorial.md': 'Tutorial.', 'docs/examples.md': 'Examples.'}
        packet = reading.read_project(self.root, ROOT_URL, max_documents=6, client=self.client(files))
        self.assertEqual(6, len(packet['contexts']))
        self.assertEqual(6, packet['coverage']['supplemental_requests'])
        self.assertEqual(1, sum('/git/trees/' in url for url in self.requests))
        self.assertEqual(5, sum(url.startswith(RAW) for url in self.requests))
        self.assertEqual(3, packet['coverage']['document_budget_excluded'])
        purposes = {row['question'] for row in packet['reading_plan']['selected_documents']}
        self.assertEqual({'overview', 'license', 'installation', 'usage'}, purposes)

    def test_question_plan_prefers_a_concrete_quickstart_over_documentation_index(self):
        files = {'docs/index.rst': 'Documentation links.', 'docs/user/quickstart.rst': 'How to send a request.',
                 'docs/user/install.rst': 'Install with pip.', 'LICENSE': 'MIT License.'}
        packet = reading.read_project(self.root, ROOT_URL, client=self.client(files))
        paths = {row['file_path'] for row in packet['reading_plan']['selected_documents']}
        self.assertIn('docs/user/quickstart.rst', paths)
        self.assertNotIn('docs/index.rst', paths)
        self.assertEqual(4, packet['reading_plan']['budget']['supplemental_requests'])
        self.assertEqual([], packet['reading_plan']['failures'])

    def test_real_truncation_and_full_document_scopes_keep_original_offsets(self):
        main = '🙂中文\r\n' + 'X' * 80
        license_text = 'MIT License.'
        packet = reading.read_project(self.root, ROOT_URL, max_chars=40,
                                     client=self.client({'LICENSE': license_text}, readme=main))
        self.assertEqual(40, packet['coverage']['supplied_chars'])
        overview, license_context = packet['contexts']
        self.assertEqual(main[:28], overview['text'])
        self.assertEqual('excerpt', overview['source_scope']['coverage'])
        self.assertEqual(len(main), overview['source_scope']['document_chars'])
        self.assertEqual([{'start': 0, 'end': 28}], overview['source_scope']['ranges'])
        self.assertEqual(hashlib.sha256(main.encode()).hexdigest(), overview['source_scope']['document_sha256'])
        self.assertEqual(license_text, license_context['text'])
        self.assertEqual('complete_text', license_context['source_scope']['coverage'])

    def test_versioned_release_candidate_uses_exact_tag_not_latest(self):
        packet = reading.read_project(self.root, ROOT_URL + '/releases/tag/v1.2.3',
                                     client=self.client({'LICENSE': 'MIT License.'}, expected_ref='v1.2.3'))
        self.assertEqual(SHA, packet['commit_sha'])
        self.assertFalse(any(url == API or '/latest' in url for url in self.requests))
        self.assertEqual(API + '/commits/v1.2.3', self.requests[0])

    def test_unpinned_redirect_cannot_supply_mixed_version_context(self):
        latest = 'https://raw.githubusercontent.com/example/tool/main/LICENSE'
        client = self.client({'LICENSE': 'MIT License.'}, redirect=(RAW + 'LICENSE', latest))
        packet = reading.read_project(self.root, ROOT_URL, client=client)
        self.assertEqual('partial', packet['status'])
        self.assertEqual(1, len(packet['contexts']))
        self.assertEqual('unknown', packet['coverage']['questions']['license'])
        self.assertNotIn(latest, self.requests, 'redirect must not spend a second HTTP slot')

    def test_binary_license_and_symlink_inventory_do_not_become_evidence(self):
        extras = [{'path': 'COPYING', 'type': 'blob', 'mode': '120000'},
                  {'path': '../LICENSE', 'type': 'blob', 'mode': '100644'},
                  {'path': 'LICENSE.png', 'type': 'blob', 'mode': '100644'}]
        packet = reading.read_project(self.root, ROOT_URL, client=self.client({'LICENSE': b'\xff\xfe\x00'}, tree_extra=extras))
        self.assertEqual(1, len(packet['contexts']))
        self.assertEqual('unknown', packet['coverage']['questions']['license'])
        self.assertEqual(1, sum(url.startswith(RAW) for url in self.requests))

    def test_cli_prints_scope_descriptor_without_original_body(self):
        packet = reading.read_project(self.root, ROOT_URL, client=self.client({'LICENSE': 'MIT License.'}))
        output = io.StringIO()
        with patch.object(reading, 'read_project', return_value=packet), redirect_stdout(output):
            code = reading.main(['--root', str(self.root), '--url', ROOT_URL, '--max-documents', '3', '--max-chars', '90'])
        self.assertEqual(0, code)
        parsed = json.loads(output.getvalue())
        self.assertIn('source_scope', parsed['contexts'][0])
        self.assertNotIn('text', parsed['contexts'][0])
        self.assertNotIn('MIT License.', output.getvalue())

    def test_packaged_content_is_saved_as_artifact_and_not_evidence_context(self):
        content = 'PACKAGED ONLY\n'
        packaged = {'status': 'ok', 'tool': 'gitingest', 'available': True, 'version': '0.3.1',
                    'summary': '1 file', 'tree': 'README.md', 'content': content,
                    'content_chars': len(content),
                    'content_sha256': hashlib.sha256(content.encode()).hexdigest()}
        with patch.object(reading, 'package_selected_documents', return_value=packaged):
            packet = reading.read_project(self.root, ROOT_URL, client=self.client())
        descriptor = packet['gitingest']
        self.assertFalse({'content', 'summary', 'tree'} & set(descriptor))
        self.assertEqual(content, (self.root / descriptor['artifact_path']).read_text())
        self.assertFalse(any('PACKAGED ONLY' in row['text'] for row in packet['contexts']))

    def test_invalid_bounds_or_ambiguous_ref_do_not_use_network(self):
        for options in ({'max_documents': 7}, {'max_chars': 0}, {'max_documents': True}):
            with self.assertRaises(sources.SourceError):
                reading.read_project(self.root, ROOT_URL, client=self.client(), **options)
        with self.assertRaises(sources.SourceError):
            reading.read_project(self.root, ROOT_URL + '/blob/main/LICENSE', client=self.client())
        self.assertEqual([], self.requests)


class LocalPackagingTests(unittest.TestCase):
    def test_missing_library_is_reported_without_claiming_it_ran(self):
        with patch.object(reading.importlib.util, 'find_spec', return_value=None):
            packet = reading.package_selected_documents({'README.md': b'Original.'})
        self.assertEqual('unavailable', packet['status'])
        self.assertFalse(packet['available'])
        self.assertNotIn('content', packet)

    def test_only_selected_raw_files_are_passed_to_local_tool_and_cleaned(self):
        folder_seen = []
        originals = {'README.md': b'Original README.', 'docs/usage.md': b'Original usage.'}
        def run(command, **kwargs):
            folder = command[5]
            folder_seen.append(Path(folder))
            self.assertFalse(folder.startswith('http'))
            self.assertEqual(set(originals), {path.relative_to(folder).as_posix() for path in Path(folder).rglob('*') if path.is_file()})
            for name, raw in originals.items():
                self.assertEqual(raw, (Path(folder) / name).read_bytes())
            self.assertIn('include_submodules=False', command[4])
            self.assertIn('output=None', command[4])
            self.assertEqual('ERROR', kwargs['env']['LOG_LEVEL'])
            self.assertEqual(30, kwargs['timeout'])
            from subprocess import CompletedProcess
            return CompletedProcess(command, 0, stdout=json.dumps([
                '2 files', 'README.md\ndocs/usage.md', 'Original README.\nOriginal usage.']))
        with patch.object(reading.importlib.util, 'find_spec', return_value=object()), \
                patch.object(reading.subprocess, 'run', side_effect=run):
            packet = reading.package_selected_documents(originals)
        self.assertEqual('ok', packet['status'])
        self.assertTrue(packet['local_only'])
        self.assertEqual('packaging_only', packet['evidence_role'])
        self.assertEqual(hashlib.sha256(packet['content'].encode()).hexdigest(), packet['content_sha256'])
        self.assertFalse(folder_seen[0].exists())

    def test_tool_failure_is_honest_and_cleans_local_files(self):
        seen = []
        def broken(command, **kwargs):
            seen.append(Path(command[5]))
            raise RuntimeError('tool failed')
        with patch.object(reading.importlib.util, 'find_spec', return_value=object()), \
                patch.object(reading.subprocess, 'run', side_effect=broken):
            packet = reading.package_selected_documents({'README.md': b'Original.'})
        self.assertEqual('failed', packet['status'])
        self.assertTrue(packet['available'])
        self.assertFalse(seen[0].exists())
        self.assertNotIn('content', packet)

    def test_parent_paths_and_git_metadata_cannot_escape_local_document_set(self):
        for path in ('../README.md', '/README.md', '.git/config', 'C:/README.md'):
            with self.assertRaises(sources.SourceError):
                reading.package_selected_documents({path: b'Original.'})

    @unittest.skipUnless(importlib.util.find_spec('gitingest'), 'optional gitingest extra is not installed')
    def test_actual_installed_library_packages_selected_documents(self):
        handlers = list(logging.getLogger().handlers)
        output = io.StringIO()
        with redirect_stdout(output):
            packet = reading.package_selected_documents({
                'README.md': b'# Local fixture\nUse this original fixture.\n',
                'LICENSE': b'MIT License\nCopyright Test Fixture.\n',
                'docs/usage.md': b'# Usage\nSelect a folder.\n'})
        self.assertEqual('ok', packet['status'], packet.get('error'))
        self.assertEqual('0.3.1', packet['version'])
        self.assertIn('Use this original fixture.', packet['content'])
        self.assertIn('Copyright Test Fixture.', packet['content'])
        self.assertIn('Select a folder.', packet['content'])
        self.assertEqual(3, len(packet['documents']))
        self.assertEqual('', output.getvalue())
        self.assertEqual(handlers, logging.getLogger().handlers)


if __name__ == '__main__':
    unittest.main()
