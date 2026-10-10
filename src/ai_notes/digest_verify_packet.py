"""Build a bounded, source-owned packet without summarising or inventing evidence.

The caller keeps the original contexts and fetch receipts. This module creates
new contexts from literal character slices and describes only those slices as
supplied. A date hint is a parsing aid linked to the retained publisher feed
item; it is not independent evidence or a replacement for model citations.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import re
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

from . import digest


CONTRACT = 'bounded-originals.v1'
MAX_TEXT_CHARS = 20_000
_SCOPE_VERSION = 'digest-source-scope.v1'
_HASH = re.compile(r'[0-9a-f]{64}')
_ISO = re.compile(r'\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[+-]\d{2}(?::?\d{2})?)')
_UTC_SUFFIX = re.compile(r'(\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)\s+(?:GMT|UTC)', re.IGNORECASE)
_ITEM = re.compile(r'<item\b[^>]*>.*?</item\s*>', re.IGNORECASE | re.DOTALL)


def parse_publication_date(value):
    """Normalise a stated date/zone; a missing or ambiguous zone stays unknown.

    ISO dates retain date-only precision. Timestamps require an explicit ISO
    offset/Z, RFC GMT/UTC/numeric offset, or an ISO clock followed by GMT/UTC.
    The latter is the mixed representation seen in the Sophos model receipt.
    No local timezone, midnight, feed refresh time or discovery time is used.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return {'event_date': date.fromisoformat(value).isoformat(),
                    'date_precision': 'date', 'timezone': 'unknown'}
        if _ISO.fullmatch(value):
            moment = datetime.fromisoformat(value.upper().replace('Z', '+00:00'))
        elif (match := _UTC_SUFFIX.fullmatch(value)):
            moment = datetime.fromisoformat(match[1].upper()).replace(tzinfo=timezone.utc)
        elif re.search(r'\s(?:GMT|UTC|[+-]\d{4})$', value, re.IGNORECASE):
            moment = parsedate_to_datetime(value)
        else:
            return None
        if moment.utcoffset() is None:
            return None
        return {'event_date': moment.isoformat(), 'date_precision': 'timestamp'}
    except (ValueError, TypeError, OverflowError):
        return None


def _publisher(url):
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname:
        return None
    return parsed.hostname.lower().removeprefix('www.')


def _target_url(record):
    event = record.get('event') or {}
    return event.get('url') or record.get('url')


def _rss_context(context):
    # A read RSS excerpt need not contain the final </rss>, so parse its exact
    # complete item fragments rather than requiring the whole feed to close.
    return bool(_rss_opening(context['text']))


def _rss_opening(text):
    # An XML-like example embedded in an article is not a publisher feed.
    return re.match(r'\ufeff?\s*(?:<\?xml\b.*?\?>\s*)?(?:<!--.*?-->\s*)*(?:<!DOCTYPE\b[^>]*>\s*)?(<rss\b[^>]*>)',
                    text, re.IGNORECASE | re.DOTALL)


def _matching_item(context, record):
    target = _target_url(record)
    if not target or _publisher(context['url']) != _publisher(target):
        return None
    matches = []
    opening = _rss_opening(context['text'])
    namespace_attributes = re.findall(r'\bxmlns(?::[A-Za-z_][\w.-]*)?\s*=\s*(?:"[^"]*"|\'[^\']*\')',
                                      opening[1] if opening else '')
    for match in _ITEM.finditer(context['text']):
        quote = match.group(0)
        try:
            # Item fragments may inherit content:/dc: declarations from the
            # retained RSS header. Namespace parsing does not rewrite the
            # literal item quote or its original character coordinates.
            wrapper = '<root ' + ' '.join(namespace_attributes) + '>' + quote + '</root>'
            item = ET.fromstring(wrapper)[0]
        except ET.ParseError:
            continue
        fields = {}
        for child in item:
            name = child.tag.rsplit('}', 1)[-1]
            fields.setdefault(name, ''.join(child.itertext()).strip())
        link = fields.get('link')
        guid = next((child for child in item if child.tag.rsplit('}', 1)[-1] == 'guid'), None)
        if not link and guid is not None:
            if guid.attrib.get('isPermaLink', 'true').lower() == 'true':
                link = fields.get('guid')
        if not link:
            continue
        try:
            linked = urljoin(context['url'], link)
            if digest._event_url(linked) != digest._event_url(target):
                continue
        except digest.DigestError:
            continue
        raw_date = fields.get('pubDate')
        parsed_date = parse_publication_date(raw_date)
        matches.append({'range': (match.start(), match.end()), 'quote': quote,
                        'raw_date': raw_date, 'parsed_date': parsed_date,
                        'event_url': target})
    # Duplicate target items with different publication dates are conflicting
    # evidence, not permission to pick a favourable neighbouring timestamp.
    if not matches or len({value['raw_date'] for value in matches}) != 1:
        return None
    return matches[0]


def _unknown_scope(context, supplied_chars):
    old = context.get('source_scope') or {}
    digest_value = old.get('document_sha256')
    return {'schema_version': _SCOPE_VERSION, 'coverage': 'unknown',
            'document_chars': None, 'supplied_chars': supplied_chars, 'ranges': [],
            'document_sha256': digest_value if isinstance(digest_value, str) and _HASH.fullmatch(digest_value) else None,
            'reader': old.get('reader') if isinstance(old.get('reader'), str) and old['reader'].strip() else 'unknown',
            'commit_sha': old.get('commit_sha'), 'file_path': old.get('file_path')}


def _coordinate_segments(context):
    """Map supplied text to declared original ranges when the recipe is proven.

    Existing compiled excerpts join declared ranges with exactly two newlines.
    Both lengths and separators must match. Any other unexplained gap loses
    coordinate claims rather than assuming an arbitrary original offset.
    """
    old = context.get('source_scope')
    if not isinstance(old, dict) or old.get('schema_version') != _SCOPE_VERSION:
        return None
    if old.get('coverage') not in ('complete_text', 'excerpt'):
        return None
    text = context['text']
    if old.get('supplied_chars') != len(text):
        return None
    ranges = old.get('ranges')
    total = old.get('document_chars')
    if not isinstance(ranges, list) or not ranges or type(total) is not int or total < 0:
        return None
    segments = []
    cursor = 0
    previous_end = 0
    for index, span in enumerate(ranges):
        if not isinstance(span, dict) or set(span) != {'start', 'end'}:
            return None
        start, end = span['start'], span['end']
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= total or (index and start < previous_end):
            return None
        if index:
            if text[cursor:cursor + 2] != '\n\n':
                return None
            cursor += 2
        length = end - start
        segments.append((cursor, cursor + length, start))
        cursor += length
        previous_end = end
    return segments if cursor == len(text) else None


def _merge_ranges(ranges):
    merged = []
    for start, end in sorted(ranges):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _sliced_context(context, ranges):
    ranges = _merge_ranges(ranges)
    text = '\n\n'.join(context['text'][start:end] for start, end in ranges)
    result = copy.deepcopy(context)
    result['text'] = text
    if ranges == [(0, len(context['text']))] and isinstance(context.get('source_scope'), dict):
        # No extraction change: retain the caller's actual known/unknown scope.
        result['source_scope'] = copy.deepcopy(context['source_scope'])
        return result
    segments = _coordinate_segments(context)
    if segments is None:
        result['source_scope'] = _unknown_scope(context, len(text))
        return result
    mapped = []
    for start, end in ranges:
        for supplied_start, supplied_end, original_start in segments:
            left, right = max(start, supplied_start), min(end, supplied_end)
            if right > left:
                mapped.append((original_start + left - supplied_start,
                               original_start + right - supplied_start))
    mapped = _merge_ranges(mapped)
    if not mapped and text:
        result['source_scope'] = _unknown_scope(context, len(text))
        return result
    scope = copy.deepcopy(context['source_scope'])
    scope.update(coverage='excerpt', supplied_chars=len(text),
                 ranges=[{'start': start, 'end': end} for start, end in mapped])
    result['source_scope'] = scope
    return result


def _keywords(record):
    title = str(record.get('title') or '').strip()
    values = [title] if len(title) >= 3 else []
    values.extend(re.findall(r'[A-Za-z][A-Za-z0-9+._-]{2,}', title))
    target = _target_url(record)
    if target:
        part = urlsplit(target).path.rstrip('/').rsplit('/', 1)[-1]
        if len(part) >= 3:
            values.append(part)
    skip = {'the', 'and', 'with', 'for', 'com', 'www', 'index', 'readme', 'html'}
    return list(dict.fromkeys(value for value in values if value.lower() not in skip))[:12]


def _text_ranges(text, limit, keywords):
    if len(text) <= limit:
        return [(0, len(text))]
    if limit < 4:
        return [(0, limit)]
    prefix = min(2048, limit // 2)
    remaining = limit - prefix - 2
    hits = []
    for keyword in keywords:
        match = re.search(re.escape(keyword), text[prefix:], re.IGNORECASE)
        if match:
            hits.append(prefix + match.start())
    if not hits or remaining <= 0:
        return [(0, limit)]
    centre = min(hits)
    start = max(prefix, min(centre - min(200, remaining // 4), len(text) - remaining))
    if start == prefix:
        return [(0, limit)]
    return [(0, prefix), (start, start + remaining)]


def build_verify_packet(contexts, record, *, max_chars=MAX_TEXT_CHARS):
    """Return literal source excerpts plus linked, source-validated date hints.

    Text space is fairly shared among sources; unused shares are redistributed
    in their original order. Publication RSS items are retained whole and only
    for a link matching the candidate/event on the same publisher hostname.
    Unmatched/cross-publisher feed dates are omitted. The caller must separately
    preflight the full model wire, whose schema/metadata also consume space.
    """
    if not isinstance(contexts, list) or not isinstance(record, dict):
        raise ValueError('contexts and record are required')
    if type(max_chars) is not int or not 1 <= max_chars <= 60_000:
        raise ValueError('source text budget must be an integer in 1..60000')
    unique = []
    by_url = {}
    for context in contexts:
        if not isinstance(context, dict) or not isinstance(context.get('text'), str) or not isinstance(context.get('url'), str):
            raise ValueError('invalid source context')
        digest._url(context['url'], 'source url')
        previous = by_url.get(context['url'])
        if previous is not None:
            if previous['text'] != context['text'] or previous.get('source_scope') != context.get('source_scope'):
                raise ValueError('duplicate source URL has different text or scope')
            continue
        by_url[context['url']] = context
        if context['text'].strip():
            unique.append(context)
    original_chars = sum(len(context['text']) for context in unique)
    prepared = []
    omitted = []
    date_sources = []
    for context in unique:
        if record.get('kind') == 'news' and _rss_context(context):
            item = _matching_item(context, record)
            if item is None:
                omitted.append({'url': context['url'], 'reason': 'no unambiguous same-publisher matching RSS item'})
                continue
            packet_context = _sliced_context(context, [item['range']])
            prepared.append({'original': context, 'context': packet_context,
                             'fixed': True, 'ranges': [item['range']]})
            if item['parsed_date'] is not None:
                date_sources.append({'feed_url': context['url'], 'event_url': item['event_url'],
                    'publication_field': 'pubDate', 'raw_date': item['raw_date'],
                    **item['parsed_date'], 'quote': item['quote'],
                    'source_scope': copy.deepcopy(packet_context['source_scope'])})
        else:
            prepared.append({'original': context, 'context': context, 'fixed': False})
    fixed_chars = sum(len(value['context']['text']) for value in prepared if value['fixed'])
    if fixed_chars > max_chars:
        raise ValueError('matching RSS items exceed the bounded source text budget')
    flexible = [value for value in prepared if not value['fixed']]
    share = (max_chars - fixed_chars) // max(1, len(flexible))
    counts = [min(len(value['context']['text']), share) for value in flexible]
    remaining = max_chars - fixed_chars - sum(counts)
    for index, value in enumerate(flexible):
        extra = min(remaining, len(value['context']['text']) - counts[index])
        counts[index] += extra
        remaining -= extra
    keywords = _keywords(record)
    for value, count in zip(flexible, counts):
        ranges = _text_ranges(value['context']['text'], count, keywords) if count else []
        value.update(context=_sliced_context(value['context'], ranges), ranges=ranges)
    output = [value['context'] for value in prepared if value['context']['text'].strip()]
    supplied = sum(len(context['text']) for context in output)
    if supplied > max_chars:
        raise ValueError('source slice construction exceeded its text budget')
    return {'contract': CONTRACT, 'contexts': output, 'news_date_sources': date_sources,
            'original_text_chars': original_chars, 'supplied_text_chars': supplied,
            'source_slices': [{'url': value['original']['url'],
                'input_text_ranges': [{'start': start, 'end': end} for start, end in value['ranges']],
                'input_text_chars': len(value['original']['text']),
                'supplied_text_chars': len(value['context']['text'])} for value in prepared],
            'omitted_sources': omitted}
