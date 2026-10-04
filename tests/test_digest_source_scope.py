"""Offline source fixtures; no model call is evidence of semantic correctness."""
from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from ai_notes import digest_pipeline as pipeline


class SourceScopeTests(unittest.TestCase):
    def extract(self, html):
        parser = pipeline._Text()
        parser.feed(html)
        return parser.text()

    def test_footnote_reference_does_not_become_a_product_version(self):
        text = self.extract('''<p>Opal<a href="#footnote-2" id="footnote-source-2"
            aria-label="Jump to link reference 2"><sup>2</sup></a> will turn down.</p>
            <div id="footnote-2">Existing projects remain available.</div>
            <p>Other product version 2 remains supported.</p>''')
        self.assertIn('Opal[footnote: 2] will turn down.', text)
        self.assertNotIn('Opal 2', text)
        self.assertIn('Existing projects remain available.', text)
        self.assertIn('Other product version 2 remains supported.', text)

    def test_unmarked_superscripts_and_normal_links_are_not_removed_as_notes(self):
        text = self.extract('<p>x<sup>2</sup> + y<sup>n + 1</sup>; '
                            'H<sub>2</sub>O; Version <a href="#version-2">2</a>; '
                            '<sup>editorial comment</sup>.</p>')
        self.assertEqual('x[superscript: 2] + y[superscript: n + 1]; '
                         'H[subscript: 2]O; Version 2; [superscript: editorial comment].', text)
        self.assertNotIn('[footnote:', text)

    def test_semantic_footnotes_keep_the_reference_text(self):
        for reference in ('<a role="doc-noteref" href="#note-x">a</a>',
                          '<sup class="footnote-ref">a</sup>',
                          '<a epub:type="noteref" href="#note-x">a</a>',
                          '<sup><a href="#fn:1">a</a></sup>'):
            with self.subTest(reference=reference):
                self.assertIn('[footnote: a]', self.extract('Use' + reference + '.'))

    def test_release_and_installation_headings_keep_their_qualifying_text(self):
        text = self.extract('''<h1>Release <span>1.4.5</span></h1>
            <p>This is the last release supporting Intel Macs.</p>
            <h2>Development branch</h2><p>Version 1.4.6, once released, requires Apple silicon.</p>
            <h2>Desktop installer</h2><p>Download <a href="/downloads">the installer</a>.</p>
            <h2>PyPI installation</h2><p>Run <code>pip install example</code>.</p>
            <h3>Optional GPU acceleration for PyPI</h3><p>CUDA packages are optional.</p>''')
        for part in ('# Release 1.4.5\n', 'last release supporting Intel Macs.',
                     '## Development branch\n', 'Version 1.4.6, once released',
                     '## Desktop installer\n', 'Download the installer.',
                     '## PyPI installation\n', 'Run pip install example.',
                     '### Optional GPU acceleration for PyPI\n', 'CUDA packages are optional.'):
            self.assertIn(part, text)
        self.assertLess(text.index('## Desktop installer'), text.index('## PyPI installation'))

    def test_inline_text_remains_one_verbatim_sentence(self):
        text = self.extract('<p>It <span>supports <b>local</b> files</span> and '
                            '<a href="/manual">documented steps</a>.</p>')
        self.assertEqual('It supports local files and documented steps.', text)

    def test_html_source_extraction_preserves_dates_without_modified_metadata(self):
        html = b'''<meta property="article:published_time" content="2026-09-29T14:00:00Z">
            <h2>New <span>features</span></h2><p>Documented facts.</p>
            <time itemprop="dateModified" datetime="2026-10-02T01:00:00Z">Modified</time>
            <script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-09-29","dateModified":"2026-10-02"}</script>'''
        with patch.object(pipeline.sources, '_payload', return_value=html):
            text = pipeline._source_text(Path('.'), {'content_type': 'text/html'})
        self.assertIn('publication metadata: 2026-09-29T14:00:00Z', text)
        self.assertIn('article datePublished: 2026-09-29', text)
        self.assertRegex(text, r'## New features\n+Documented facts\.')
        self.assertNotIn('2026-10-02', text)
        self.assertNotIn('Modified', text)

    def test_markdown_source_keeps_literal_versions_and_installation_sections(self):
        raw = b'# Release v2\n## PyPI\nOptional GPU: see linked review, not supplied here.\n'
        with patch.object(pipeline.sources, '_payload', return_value=raw):
            text = pipeline._source_text(Path('.'), {'content_type': 'text/plain'})
        self.assertEqual(raw.decode(), text)


if __name__ == '__main__':
    unittest.main()
