"""Bind a small source understanding card without certifying its meaning.

Source scope, passage IDs and evidence text are owned by the program. The model
still interprets purpose, operations and conditions; closed references do not
prove that interpretation. These helpers do not score or change old reviews.
"""
from __future__ import annotations

import copy
import hashlib
import re

from .digest_passages import bind_review


SOURCE_REVIEW_CONTRACT = 'passages.understood.v1'
SOURCE_SCOPE_VERSION = 'digest-source-scope.v1'
UNDERSTANDING_VERSION = 'digest-understanding.v1'
CONDITION_KINDS = ('mandatory', 'optional', 'availability', 'pricing', 'compatibility')
_RAW_FIELDS = {'purpose', 'input', 'output', 'operations', 'conditions', 'unknowns'}
_CARD_FIELDS = _RAW_FIELDS | {'schema_version', 'proof_map', 'source_documents', 'reading_scope_issues'}
_SCOPE_FIELDS = {'schema_version', 'coverage', 'document_chars', 'supplied_chars', 'ranges',
                 'document_sha256', 'reader', 'commit_sha', 'file_path'}

UNDERSTANDING_PROMPT_SUPPLEMENT = """
Produce the required understanding card before writing source facts. Give one
concrete purpose, the supplied input, the resulting output, and at most four
operations connecting input -> action -> output. Ground each known statement in
the supplied passage IDs. A function name alone is not an explanation. For an
unknown input or output use text=null and passage_ids=[]; use null for unknown
operation inputs/outputs, and describe remaining uncertainties in unknowns.
Do not invent prerequisites to fill gaps. Distinguish mandatory use requirements,
optional features, platform compatibility, availability and pricing. A supported
platform is not automatically a mandatory requirement. Describe the subject of
each condition: an optional module's requirement must not become a whole-product
gate. A promotion's target users and eligible plan are pricing conditions, not
proof that all readers qualify. Reading and news can convey understanding without
installation or an open-source licence; those are not default eligibility gates.
The program's source_documents describes exactly what text was supplied. Treat
complete_text as the complete captured text of that document only, not the entire
repository or all documentation. excerpt and unknown cannot support a claim that
the complete README, licence or document was read. Never generate or overwrite
source coverage, character ranges, hashes, reader identity or commit metadata.
Passage IDs prove the supplied evidence exists; semantic support and the correct
condition classification still require model judgment.
""".strip()


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' must be a nonempty string')
    return value


def _integer(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(label + ' must be a nonnegative integer')
    return value


def _scope(scope, *, supplied_text=None):
    if not isinstance(scope, dict) or set(scope) != _SCOPE_FIELDS:
        raise ValueError('source_scope requires exactly the program-owned scope fields')
    if scope['schema_version'] != SOURCE_SCOPE_VERSION:
        raise ValueError('unsupported source scope version')
    coverage = scope['coverage']
    if coverage not in ('complete_text', 'excerpt', 'unknown'):
        raise ValueError('unsupported source coverage')
    supplied_chars = _integer(scope['supplied_chars'], 'supplied_chars')
    document_chars = scope['document_chars']
    if document_chars is not None:
        _integer(document_chars, 'document_chars')
        if supplied_chars > document_chars:
            raise ValueError('supplied_chars exceeds document_chars')
    if supplied_text is not None and supplied_chars != len(supplied_text):
        raise ValueError('supplied_chars differs from supplied text character count')
    _text(scope['reader'], 'source reader')
    for field in ('commit_sha', 'file_path'):
        if scope[field] is not None:
            _text(scope[field], field)
    digest = scope['document_sha256']
    if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
        raise ValueError('document_sha256 must be a lowercase SHA-256 or null')
    ranges = scope['ranges']
    if not isinstance(ranges, list):
        raise ValueError('source ranges must be a list')
    previous_end = 0
    covered = 0
    for index, span in enumerate(ranges):
        if not isinstance(span, dict) or set(span) != {'start', 'end'}:
            raise ValueError('source range requires exactly start and end')
        start = _integer(span['start'], 'range start')
        end = _integer(span['end'], 'range end')
        if end <= start or (index and start < previous_end):
            raise ValueError('source ranges must be ordered nonoverlapping nonempty slices')
        if document_chars is not None and end > document_chars:
            raise ValueError('source range exceeds document_chars')
        covered += end - start
        previous_end = end
    # Ranges refer to original document fragments. A compiled excerpt may add
    # separators between fragments; supplied_chars counts those as well.
    if ranges and covered > supplied_chars:
        raise ValueError('source range lengths exceed supplied_chars')
    if coverage == 'excerpt' and supplied_chars and not ranges:
        raise ValueError('excerpt requires ranges for the supplied text')
    if coverage == 'complete_text':
        if document_chars is None or document_chars != supplied_chars:
            raise ValueError('complete_text requires the complete document character count')
        expected_start = 0
        for span in ranges:
            if span['start'] != expected_start:
                raise ValueError('complete_text ranges cannot have gaps')
            expected_start = span['end']
        if expected_start != document_chars:
            raise ValueError('complete_text ranges must cover the entire document')
        if supplied_text is not None and digest is not None:
            actual = hashlib.sha256(supplied_text.encode('utf-8')).hexdigest()
            if digest != actual:
                raise ValueError('complete document_sha256 differs from supplied text')
    return copy.deepcopy(scope)


def source_documents(contexts):
    """Freeze precise program source metadata; absent provenance stays unknown.

    Ranges use Python/Unicode character offsets and half-open [start, end)
    slices in the original document. supplied_chars counts the actual supplied
    text, including separators inserted when compiling disjoint excerpts.
    """
    if not isinstance(contexts, list):
        raise ValueError('contexts must be a list')
    found = {}
    texts = {}
    for context in contexts:
        if not isinstance(context, dict):
            raise ValueError('invalid source context')
        url = _text(context.get('url'), 'source url')
        text = context.get('text')
        if not isinstance(text, str):
            raise ValueError('source text must be a string')
        scope = context.get('source_scope')
        if scope is None:
            scope = dict(schema_version=SOURCE_SCOPE_VERSION, coverage='unknown', document_chars=None,
                         supplied_chars=len(text), ranges=[], document_sha256=None, reader='unknown',
                         commit_sha=None, file_path=None)
        scope = _scope(scope, supplied_text=text)
        document = dict(evidence_url=url, scope=scope)
        if url in found and (texts[url] != text or found[url] != document):
            raise ValueError('duplicate source URL has different text or scope')
        found[url], texts[url] = document, text
    return list(found.values())


def _documents(documents):
    if not isinstance(documents, list):
        raise ValueError('source_documents must be a list')
    result = []
    seen = set()
    for document in documents:
        if not isinstance(document, dict) or set(document) != {'evidence_url', 'scope'}:
            raise ValueError('invalid source document fields')
        url = _text(document['evidence_url'], 'source document URL')
        if url in seen:
            raise ValueError('duplicate source document URL')
        seen.add(url)
        result.append(dict(evidence_url=url, scope=_scope(document['scope'])))
    return result


def understanding_schema(*, allow_unknown_conditions=False):
    """Schema fragment using the enclosing review's shared passage ID enum."""
    if type(allow_unknown_conditions) is not bool:
        raise ValueError('allow_unknown_conditions must be a boolean')
    condition_kinds = CONDITION_KINDS + (('unknown',) if allow_unknown_conditions else ())
    text = {'$ref': '#/$defs/nonempty_text'}
    nullable = {'anyOf': [dict(text), {'type': 'null'}]}

    def obj(properties):
        return {'type': 'object', 'properties': properties, 'required': list(properties),
                'additionalProperties': False}

    def refs(minimum=1):
        return {'type': 'array', 'items': {'$ref': '#/$defs/passage_id'},
                'minItems': minimum, 'uniqueItems': True}

    known = obj({'text': dict(text), 'passage_ids': refs()})
    unknown = obj({'text': {'type': 'null'}, 'passage_ids': dict(refs(0), maxItems=0)})
    return obj({
        'purpose': known,
        'input': {'anyOf': [copy.deepcopy(known), copy.deepcopy(unknown)]},
        'output': {'anyOf': [copy.deepcopy(known), copy.deepcopy(unknown)]},
        'operations': {'type': 'array', 'maxItems': 4, 'items': obj({
            'input': copy.deepcopy(nullable), 'action': dict(text),
            'output': copy.deepcopy(nullable), 'passage_ids': refs()})},
        'conditions': {'type': 'array', 'maxItems': 8, 'items': obj({
            'subject': dict(text), 'kind': {'enum': list(condition_kinds)},
            'text': dict(text), 'passage_ids': refs()})},
        'unknowns': {'type': 'array', 'items': dict(text), 'uniqueItems': True},
    })


def _raw_understanding(value, available_ids, *, allow_unknown_conditions=False):
    if not isinstance(value, dict) or set(value) != _RAW_FIELDS:
        raise ValueError('understanding requires exactly purpose, input, output, operations, conditions, unknowns')
    referenced = set()

    def references(ids, label, *, unknown=False):
        if not isinstance(ids, list) or (not unknown and not ids):
            raise ValueError(label + ' requires passage IDs')
        if unknown and ids:
            raise ValueError('unknown ' + label + ' cannot fabricate evidence')
        seen = set()
        for passage_id in ids:
            _text(passage_id, label + ' passage ID')
            if passage_id not in available_ids or passage_id in seen:
                raise ValueError('unknown or repeated passage ID in ' + label)
            seen.add(passage_id)
            referenced.add(passage_id)

    for field in ('purpose', 'input', 'output'):
        item = value[field]
        if not isinstance(item, dict) or set(item) != {'text', 'passage_ids'}:
            raise ValueError('invalid understanding.' + field + ' fields')
        unknown = field != 'purpose' and item['text'] is None
        if not unknown:
            _text(item['text'], 'understanding.' + field + '.text')
        references(item['passage_ids'], 'understanding.' + field, unknown=unknown)
    operations = value['operations']
    if not isinstance(operations, list) or len(operations) > 4:
        raise ValueError('understanding operations must be a list of at most four items')
    for item in operations:
        if not isinstance(item, dict) or set(item) != {'input', 'action', 'output', 'passage_ids'}:
            raise ValueError('invalid understanding operation fields')
        _text(item['action'], 'operation action')
        for field in ('input', 'output'):
            if item[field] is not None:
                _text(item[field], 'operation ' + field)
        references(item['passage_ids'], 'understanding operation')
    conditions = value['conditions']
    if not isinstance(conditions, list) or len(conditions) > 8:
        raise ValueError('understanding conditions must be a list of at most eight items')
    for item in conditions:
        if not isinstance(item, dict) or set(item) != {'subject', 'kind', 'text', 'passage_ids'}:
            raise ValueError('invalid understanding condition fields')
        _text(item['subject'], 'condition subject')
        _text(item['text'], 'condition text')
        condition_kinds = CONDITION_KINDS + (('unknown',) if allow_unknown_conditions else ())
        if item['kind'] not in condition_kinds:
            raise ValueError('unsupported condition kind')
        references(item['passage_ids'], 'understanding condition')
    unknowns = value['unknowns']
    if not isinstance(unknowns, list):
        raise ValueError('understanding unknowns must be a list')
    for item in unknowns:
        _text(item, 'understanding unknown')
    if len(set(unknowns)) != len(unknowns):
        raise ValueError('understanding unknowns cannot repeat')
    return referenced


_FULL_READ = re.compile(
    r'\b(?:read|reviewed|checked)\s+(?:the\s+)?(?:full|entire|complete)\s+'
    r'(?:licen[cs]e|document|readme|source|text)\b|'
    r'(?:已(?:经)?(?:完整)?(?:阅读|读)|完整(?:地)?阅读|通读|读完(?:了)?|阅读完(?:了)?)'
    r'[^。；;\n]{0,12}(?:全文|完整|全部|文档|文書|许可证|許可證|licen[cs]e|readme)', re.IGNORECASE)
_NOT_A_READ_CLAIM = re.compile(
    r'\b(?:not|never|unknown|should|must|need|please|before)\b|未|尚未|没(?:有)?|需要|须|需|应|請|请',
    re.IGNORECASE)


def reading_scope_issues(understanding, documents=None):
    """Report literal full-reading claims linked to a known excerpt only.

    This small audit neither validates all prose nor infers semantic support.
    Instructions to read a document and explicitly negated claims are excluded.
    """
    if documents is None:
        documents = understanding.get('source_documents', [])
    scopes = {item['evidence_url']: item['scope'] for item in documents}
    proofs = understanding.get('proof_map', {})
    statements = [(field, understanding[field]['text'], understanding[field]['passage_ids'])
                  for field in ('purpose', 'input', 'output')]
    statements.extend(('operations.' + str(index) + '.' + field, item[field], item['passage_ids'])
                      for index, item in enumerate(understanding['operations'])
                      for field in ('input', 'action', 'output'))
    statements.extend(('conditions.' + str(index) + '.text', item['text'], item['passage_ids'])
                      for index, item in enumerate(understanding['conditions']))
    issues = []
    for field, text, ids in statements:
        if text is None or not _FULL_READ.search(text) or _NOT_A_READ_CLAIM.search(text):
            continue
        urls = {proofs[pid]['evidence_url'] for pid in ids if pid in proofs}
        # One complete or unknown cited source prevents a definite excerpt-only
        # mismatch. The audit does not infer which source entails the prose.
        if urls and all(scopes.get(url, {}).get('coverage') == 'excerpt' for url in urls):
            for url in sorted(urls):
                issues.append(dict(code='reading_scope_overclaim', field=field, claim=text,
                                   evidence_url=url, coverage='excerpt'))
    return issues


_LICENSE_WORD = re.compile(r'licen[cs]e|许可证|許可證', re.IGNORECASE)
_FULL_TEXT_WORD = re.compile(r'全文|完整(?:文本|的)?|\b(?:full|entire|complete)\b', re.IGNORECASE)
_SOURCE_ASSERTION = re.compile(
    r'根据|依据|從|从|确认|確定|确定|显示|表明|写明|明确|证实|可见|核对|已|'
    r'(?:为|是|包含|提供|含)[^。；;\n]{0,20}(?:全文|完整文本)|'
    r'\b(?:according to|based on|confirmed|confirms|shows|states|establishes|reviewed|read)\b',
    re.IGNORECASE)


def prose_scope_issues(facts, documents, claims=None):
    """Audit explicit prose assertions that rely on an entire supplied source.

    Licensing assertions use the licence claims' own URLs. Other full-reading
    assertions use the fact field's claims, or summary claims when the field has
    none. An unrelated excerpt cannot override a complete licence source. Mere
    mentions, reading instructions, negation, and unknown scope are not evidence
    of a deterministic mismatch. Model interpretation still needs review.
    """
    if not isinstance(facts, dict):
        raise ValueError('facts for scope audit must be an object')
    documents = _documents(documents)
    scopes = {item['evidence_url']: item['scope'] for item in documents}
    if claims is None:
        claims = facts.get('claims', [])
    if not isinstance(claims, list):
        raise ValueError('claims for scope audit must be a list')
    by_field = {}
    for claim in claims:
        if not isinstance(claim, dict):
            raise ValueError('invalid claim for scope audit')
        field = _text(claim.get('field'), 'scope audit claim field')
        url = _text(claim.get('evidence_url'), 'scope audit claim URL')
        by_field.setdefault(field, set()).add(url)

    def statements(value, path=''):
        # Source quotes and program metadata are evidence, not model assertions.
        excluded = {'claims', 'proof_map', 'source_documents', 'understanding', 'quote',
                    'evidence_refs', 'evidence_urls', 'passage_ids'}
        for key, item in value.items():
            if key in excluded:
                continue
            label = path + '.' + key if path else key
            if isinstance(item, str):
                yield label, item
            elif isinstance(item, dict):
                yield from statements(item, label)

    issues = []
    for field, text in statements(facts):
        if _NOT_A_READ_CLAIM.search(text):
            continue
        license_assertion = bool(_LICENSE_WORD.search(text) and _FULL_TEXT_WORD.search(text)
                                 and _SOURCE_ASSERTION.search(text))
        if not license_assertion and not _FULL_READ.search(text):
            continue
        if license_assertion or _LICENSE_WORD.search(text):
            urls = by_field.get('license', set())
            # A README can state a licence name but cannot establish that the
            # actual licence file was supplied in full. Prefer the explicitly
            # named file, otherwise actual licence files, for this narrow audit.
            licence_files = {url for url in urls if re.search(
                r'/(?:licen[cs]e|copying)(?:[._-][^/?#]*)?(?:[?#].*)?$', url, re.IGNORECASE)}
            named = {url for url in licence_files if re.search(
                r'(?<![\w])' + re.escape(url.split('/')[-1].split('?')[0]) + r'(?![\w])', text, re.IGNORECASE)}
            urls = named or licence_files or urls
        else:
            urls = by_field.get(field, by_field.get('summary', set()))
        if urls and all(scopes.get(url, {}).get('coverage') == 'excerpt' for url in urls):
            for url in sorted(urls):
                issues.append(dict(code='reading_scope_overclaim', field=field, claim=text,
                                   evidence_url=url, coverage='excerpt'))
    return issues


def validate_understanding(card, *, allow_unknown_conditions=False):
    """Validate a canonical bound card and return an independent deep copy.

    References must close over program-bound proof_map and document metadata.
    This validates structure and provenance consistency, not semantic meaning.
    """
    if type(allow_unknown_conditions) is not bool:
        raise ValueError('allow_unknown_conditions must be a boolean')
    if not isinstance(card, dict) or set(card) != _CARD_FIELDS:
        raise ValueError('invalid canonical understanding card fields')
    if card['schema_version'] != UNDERSTANDING_VERSION:
        raise ValueError('unsupported understanding card version')
    documents = _documents(card['source_documents'])
    urls = {document['evidence_url'] for document in documents}
    proofs = card['proof_map']
    if not isinstance(proofs, dict):
        raise ValueError('understanding proof_map must be an object')
    for passage_id, proof in proofs.items():
        _text(passage_id, 'proof passage ID')
        if not isinstance(proof, dict) or set(proof) != {'evidence_url', 'quote', 'heading_path'}:
            raise ValueError('invalid understanding proof fields')
        _text(proof['evidence_url'], 'proof evidence_url')
        _text(proof['quote'], 'proof quote')
        if proof['evidence_url'] not in urls:
            raise ValueError('understanding proof lacks a source document')
        if not isinstance(proof['heading_path'], list):
            raise ValueError('proof heading_path must be a list')
        for heading in proof['heading_path']:
            _text(heading, 'proof heading')
    referenced = _raw_understanding({key: card[key] for key in _RAW_FIELDS}, set(proofs),
                                   allow_unknown_conditions=allow_unknown_conditions)
    if referenced != set(proofs):
        raise ValueError('understanding proof_map must contain exactly the referenced passage IDs')
    issues = reading_scope_issues(card, documents)
    if card['reading_scope_issues'] != issues:
        raise ValueError('reading_scope_issues differs from the program scope audit')
    return copy.deepcopy(card)


def bind_understanding_review(output, passages, record, documents, *, include_discovery=False,
                              nonlicensing=False, allow_unknown_conditions=False):
    """Use the old binder, then attach a frozen evidence-linked fact card."""
    if type(nonlicensing) is not bool or type(allow_unknown_conditions) is not bool:
        raise ValueError('understanding contract options must be booleans')
    if not isinstance(output, dict) or set(output) != {'qualified', 'reason', 'facts', 'evidence', 'understanding'}:
        raise ValueError('understood review requires exactly qualified, reason, facts, evidence, understanding')
    documents = _documents(documents)
    legacy = {key: output[key] for key in ('qualified', 'reason', 'facts', 'evidence')}
    bound = bind_review(legacy, passages, record, include_discovery=include_discovery,
                        **({'nonlicensing': True} if nonlicensing else {}))
    if not bound['qualified']:
        if output['understanding'] is not None:
            raise ValueError('unqualified review requires null understanding')
        return bound
    by_id = {passage['id']: passage for passage in passages}
    referenced = _raw_understanding(output['understanding'], set(by_id),
                                   allow_unknown_conditions=allow_unknown_conditions)
    card = copy.deepcopy(output['understanding'])
    card.update(schema_version=UNDERSTANDING_VERSION,
                proof_map={passage_id: {key: copy.deepcopy(by_id[passage_id][key])
                           for key in ('evidence_url', 'quote', 'heading_path')}
                           for passage_id in sorted(referenced)},
                source_documents=documents)
    card['reading_scope_issues'] = reading_scope_issues(card, documents)
    bound['facts']['understanding'] = validate_understanding(
        card, allow_unknown_conditions=allow_unknown_conditions)
    return bound
