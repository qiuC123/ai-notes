"""Strict declared HTML decoding rejects unsupported bytes without guessing."""
from __future__ import annotations

import codecs
import unittest

from ai_notes import digest_reading as reading
from ai_notes.digest_sources import SourceError


class HTMLDecodingTests(unittest.TestCase):
    def test_bom_has_explicit_priority_and_is_not_supplied_text(self):
        html = '<html><p>剪贴板 “history”</p></html>'
        for bom, encoding in ((codecs.BOM_UTF8, 'utf-8'),
                              (codecs.BOM_UTF16_LE, 'utf-16-le'),
                              (codecs.BOM_UTF16_BE, 'utf-16-be'),
                              (codecs.BOM_UTF32_LE, 'utf-32-le'),
                              (codecs.BOM_UTF32_BE, 'utf-32-be')):
            with self.subTest(encoding=encoding):
                metadata = {}
                self.assertEqual('剪贴板 “history”', reading._page_text(
                    bom + html.encode(encoding), 'text/html; charset=windows-1252', decoding=metadata))
                self.assertEqual(encoding, metadata['encoding'])
                self.assertEqual('bom', metadata['encoding_source'])
                self.assertEqual('python-codecs-strict', metadata['decoder'])

    def test_header_charset_is_used_before_meta_without_fallback(self):
        raw = b'<html><meta charset=utf-8><p>Copy \x93clips\x94.</p></html>'
        metadata = {}
        self.assertEqual('Copy “clips”.', reading._page_text(raw,
            'text/html; charset="windows-1252"', decoding=metadata))
        self.assertEqual('http-header', metadata['encoding_source'])
        with self.assertRaisesRegex(SourceError, 'strict utf-8 decoding failed'):
            reading._page_text(raw, 'text/html; charset=utf-8')

    def test_meta_forms_aliases_and_case_are_read_as_declared(self):
        for declaration in ('<META CHARSET="WINDOWS-1252">',
                            '<meta content="text/html; charset=cp1252" http-equiv="Content-Type">'):
            with self.subTest(declaration=declaration):
                metadata = {}
                text = reading._page_text((declaration + '<p>Copy “clips”.</p>').encode('cp1252'),
                                          'application/octet-stream', decoding=metadata)
                self.assertEqual('Copy “clips”.', text)
                self.assertEqual('cp1252', metadata['encoding'])
                self.assertEqual('html-meta', metadata['encoding_source'])

    def test_default_is_strict_utf8_and_never_guesses_windows_encoding(self):
        metadata = {}
        self.assertEqual('剪贴板', reading._page_text('<p>剪贴板</p>'.encode(), decoding=metadata))
        self.assertEqual('utf-8-default', metadata['encoding_source'])
        with self.assertRaisesRegex(SourceError, 'strict utf-8 decoding failed'):
            reading._page_text(b'<p>\x93clipboard\x94</p>')

    def test_invalid_declared_encoding_is_not_silently_replaced(self):
        for declaration in ('not-a-real-charset', 'utf-7', 'unicode_escape', 'base64_codec'):
            with self.subTest(declaration=declaration), self.assertRaisesRegex(SourceError, 'HTML charset'):
                reading._page_text(('<meta charset="' + declaration + '"><p>Plain text.</p>').encode())
        with self.assertRaisesRegex(SourceError, 'strict cp1252 decoding failed'):
            reading._page_text(b'<meta charset=windows-1252><p>\x81</p>')

    def test_conflicting_meta_is_rejected_and_comment_or_script_is_not_a_declaration(self):
        with self.assertRaisesRegex(SourceError, 'conflicting HTML meta'):
            reading._page_text(b'<meta charset=utf-8><meta charset=windows-1252><p>Text.</p>')
        for fake in (b'<!-- <meta charset=windows-1252> -->',
                     b'<script>"<meta charset=windows-1252>"</script>',
                     b'<html><body><meta charset=windows-1252>'):
            with self.subTest(fake=fake), self.assertRaisesRegex(SourceError, 'strict utf-8 decoding failed'):
                reading._page_text(fake + b'<p>\x93clips\x94</p>')

    def test_charset_scan_and_document_size_are_bounded(self):
        with self.assertRaisesRegex(SourceError, 'strict utf-8 decoding failed'):
            reading._page_text(b'<html><!--' + b' ' * reading.HTML_CHARSET_SCAN_BYTES +
                               b'--><meta charset=windows-1252><p>\x93clips\x94</p>')
        with self.assertRaisesRegex(SourceError, 'byte budget'):
            reading._page_text(b'<p>' + b'a' * reading.MAX_DOCUMENT_BYTES + b'</p>')

    def test_binary_invalid_media_and_control_bytes_cannot_become_html_evidence(self):
        for raw, content_type in ((b'%PDF-1.4\n<p>Text.</p>', 'text/html'),
                                  (b'PK\x03\x04<html>', 'application/octet-stream'),
                                  (b'<html><p>\x00Text.</p>', 'text/html'),
                                  (b'<meta charset=iso-8859-1><p>\x92</p>', 'text/html'),
                                  (b'<p>Plain text.</p>', 'application/pdf'),
                                  (b'An arbitrary UTF-8 binary header', 'text/html')):
            with self.subTest(raw=raw, content_type=content_type), self.assertRaises(SourceError):
                reading._page_text(raw, content_type)


if __name__ == '__main__':
    unittest.main()
