"""Number source passages and bind model-selected IDs to exact source text.

The program owns passage IDs, URLs and quotations. Models still write summaries
and choose evidence; valid IDs do not prove those interpretations correct. When
a field references several passages, those are a combined evidence set, not a
claim that every individual passage independently supports the entire summary.
Binding retains legacy claim shape for existing gates; it does not certify
semantic entailment, publication dates, licences or event identity.
"""
from __future__ import annotations

import copy
import hashlib
import re


TARGET_CHARS = 1800
MAX_CHARS = 2400
_FACT_FIELDS = ('title', 'category', 'summary', 'reason', 'audience',
                'usage_conditions', 'detail', 'retention_reason', 'open_source_status')
_EVIDENCE_FIELDS = ('category', 'summary', 'usage_conditions', 'license')
_KIND_FIELDS = {'project': (), 'update': (), 'reading': ('author', 'original_date'),
                'news': ('event_date',)}


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' must be a nonempty string')
    return value


def _headings(text):
    """Find Markdown heading offsets, excluding heading-like fenced code."""
    found = []
    offset = 0
    fence = None
    previous = None
    for line in text.splitlines(keepends=True):
        content = line.rstrip('\r\n')
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', content)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
            previous = None
        elif marker:
            fence = (marker[1][0], len(marker[1]))
            previous = None
        else:
            heading = re.match(r'^ {0,3}(#{1,6})[ \t]+(.+?)\s*$', content)
            underline = re.fullmatch(r' {0,3}(=+|-+)[ \t]*', content)
            if heading:
                title = re.sub(r'\s+#+\s*$', '', heading[2]).strip()
                if title:
                    found.append((offset, len(heading[1]), title))
                previous = None
            elif underline and previous is not None:
                found.append((previous[0], 1 if underline[1][0] == '=' else 2, previous[1]))
                previous = None
            else:
                previous = (offset, content.strip()) if content.strip() else None
        offset += len(line)
    return found


def _section_chunks(text, start, stop):
    while start < stop:
        if stop - start <= MAX_CHARS:
            end = stop
        else:
            window = text[start:start + MAX_CHARS]
            # Prefer paragraph breaks near the target, then ordinary line ends.
            ends = [match.end() for match in re.finditer(r'\r?\n[ \t]*(?:\r?\n)+', window)
                    if match.end() >= TARGET_CHARS // 2]
            if not ends:
                ends = [match.end() for match in re.finditer(r'\n', window)
                        if match.end() >= TARGET_CHARS // 2]
            end = start + (min(ends, key=lambda value: (abs(value - TARGET_CHARS), value))
                           if ends else TARGET_CHARS)
        yield start, end
        start = end


def build_passages(contexts):
    """Split complete sources without dropping body text or inventing quotes.

    Headings start new sections even when that produces shorter passages. Pure
    whitespace may be omitted; every non-whitespace character remains in an
    exact, contiguous source slice of at most MAX_CHARS characters.
    """
    if not isinstance(contexts, list):
        raise ValueError('contexts must be a list')
    sources = {}
    for context in contexts:
        if not isinstance(context, dict):
            raise ValueError('invalid source context')
        url = _text(context.get('url'), 'source url')
        text = context.get('text')
        if not isinstance(text, str):
            raise ValueError('source text must be a string')
        if url in sources and sources[url] != text:
            raise ValueError('duplicate source URL has different text')
        sources[url] = text
    passages = []
    identifiers = set()
    for url, text in sources.items():
        source_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
        headings = _headings(text)
        boundaries = sorted({0, len(text), *(offset for offset, _, _ in headings)})
        active = []
        heading_index = 0
        for section_start, section_stop in zip(boundaries, boundaries[1:]):
            for start, stop in _section_chunks(text, section_start, section_stop):
                while heading_index < len(headings) and headings[heading_index][0] <= start:
                    _, level, title = headings[heading_index]
                    active = [(old_level, old_title) for old_level, old_title in active if old_level < level]
                    active.append((level, title))
                    heading_index += 1
                quote = text[start:stop]
                if not quote.strip():
                    continue
                identity = hashlib.sha256((url + '\0' + source_hash + '\0' + str(start)).encode('utf-8')).hexdigest()
                passage_id = 'p_' + identity[:24]
                if passage_id in identifiers:
                    raise ValueError('passage identifier collision')
                identifiers.add(passage_id)
                passages.append(dict(id=passage_id, evidence_url=url, quote=quote,
                                     heading_path=[title for _, title in active]))
    return passages


def bind_review(output, passages, record, *, include_discovery=False):
    """Bind strict model evidence IDs to program-owned legacy facts/claims.

    Unknown IDs and extra/null fields fail; no old malformed quote is repaired.
    Neither the raw output nor the supplied passage list is modified.
    """
    if not isinstance(output, dict) or set(output) != {'qualified', 'reason', 'facts', 'evidence'}:
        raise ValueError('passage review requires exactly qualified, reason, facts, evidence')
    if type(output['qualified']) is not bool:
        raise ValueError('qualified must be boolean')
    _text(output['reason'], 'review reason')
    if not output['qualified']:
        if output['facts'] is not None or output['evidence'] is not None:
            raise ValueError('unqualified review requires null facts and evidence')
        return {'qualified': False, 'reason': output['reason'], 'facts': None}
    kind = record.get('kind') if isinstance(record, dict) else None
    if kind not in _KIND_FIELDS:
        raise ValueError('unsupported candidate kind')
    fact_fields = _FACT_FIELDS + _KIND_FIELDS[kind] + (('supported_systems',) if include_discovery else ())
    facts = output['facts']
    if not isinstance(facts, dict) or set(facts) != set(fact_fields):
        raise ValueError('invalid passage review facts fields')
    for field in fact_fields:
        if field == 'supported_systems' and facts[field] is None:
            continue
        _text(facts[field], 'facts.' + field)
    evidence_fields = (_EVIDENCE_FIELDS + _KIND_FIELDS[kind] + (('change_note',) if kind == 'update' else ())
                       + (('supported_systems',) if include_discovery else ()))
    evidence = output['evidence']
    if not isinstance(evidence, dict) or set(evidence) != set(evidence_fields):
        raise ValueError('invalid passage review evidence fields')
    if not isinstance(passages, list):
        raise ValueError('passages must be a list')
    by_id = {}
    for passage in passages:
        if not isinstance(passage, dict) or set(passage) != {'id', 'evidence_url', 'quote', 'heading_path'}:
            raise ValueError('invalid bound passage fields')
        for field in ('id', 'evidence_url', 'quote'):
            _text(passage[field], 'passage.' + field)
        if not isinstance(passage['heading_path'], list):
            raise ValueError('passage heading_path must be a list')
        for heading in passage['heading_path']:
            _text(heading, 'passage heading')
        if passage['id'] in by_id:
            raise ValueError('duplicate passage ID')
        by_id[passage['id']] = passage
    claims = []
    urls = []
    for field in evidence_fields:
        ids = evidence[field]
        unknown_systems = field == 'supported_systems' and facts[field] is None
        if not isinstance(ids, list) or (field != 'license' and not unknown_systems and not ids):
            raise ValueError('evidence.' + field + ' requires passage IDs')
        if unknown_systems and ids:
            raise ValueError('null supported_systems requires empty evidence')
        seen = set()
        for passage_id in ids:
            _text(passage_id, 'passage ID')
            if passage_id not in by_id or passage_id in seen:
                raise ValueError('unknown or repeated passage ID in ' + field)
            seen.add(passage_id)
            passage = by_id[passage_id]
            # An update summary describes the complete increment. Every linked
            # passage remains evidence for that summary; selecting the first
            # claim downstream must not replace it with one source paragraph.
            claim_text = facts['summary'] if field == 'change_note' else facts.get(field, passage['quote'])
            claims.append(dict(field=field, text=claim_text,
                               evidence_url=passage['evidence_url'], quote=passage['quote']))
            if passage['evidence_url'] not in urls:
                urls.append(passage['evidence_url'])
    bound = copy.deepcopy(facts)
    bound.update(evidence_urls=urls, claims=claims)
    return {'qualified': True, 'reason': output['reason'], 'facts': bound}
