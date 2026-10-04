"""Closed JSON shape guidance for scoring, not a replacement for review checks.

The ordinary provider API documents JSON-object mode with a schema in the
prompt. It does not promise server-enforced JSON Schema. Quotes, scope and
editorial meaning still require the selection validator and source review.
"""
from __future__ import annotations

import copy

from .digest import CATEGORIES, KINDS


def _object(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def assessment_schema(*, policy: dict, card: dict) -> dict:
    """Describe only allowed model judgments for this frozen policy and card."""
    text = {"type": "string", "minLength": 1, "pattern": r"\S"}
    urls = sorted({item["url"] for item in card["evidence_context"]})
    reference = {"type": "string", "enum": urls} if urls else False

    def refs(minimum=1):
        return {"type": "array", "items": copy.deepcopy(reference), "minItems": minimum}

    score = _object({"score": {"type": "integer", "minimum": 0, "maximum": 10},
                     "reason": copy.deepcopy(text), "evidence_refs": refs()})
    scores = _object({name: copy.deepcopy(score) for name in policy["dimensions"]})
    flags = []
    kind = card["material"]["kind"]
    for code in policy["flag_caps"]:
        kinds = policy.get("flag_kinds", {}).get(code)
        if kinds is not None and kind not in kinds:
            continue
        properties = {"code": {"const": code}, "reason": copy.deepcopy(text), "evidence_refs": refs()}
        basis_kind = policy.get("flag_basis", {}).get(code)
        if basis_kind:
            properties["basis"] = _object({"kind": {"const": basis_kind},
                "claim": copy.deepcopy(text), "quote": copy.deepcopy(text),
                "evidence_url": copy.deepcopy(reference)})
        if code == "insufficient_usage_evidence" and "usage_evidence_gaps" in policy:
            gaps = policy["usage_evidence_gaps"].get(card.get("ranking_type"), [])
            if not gaps:
                continue
            properties["gap"] = {"type": "string", "enum": list(gaps)}
        flags.append(_object(properties))
    schema = _object({
        "precheck": _object({"status": {"enum": ["PASS", "UNKNOWN", "BLOCK"]},
            "reasons": {"type": "array", "items": copy.deepcopy(text), "minItems": 1},
            "evidence_refs": refs(0)}),
        "scores": {"anyOf": [scores, {"type": "null"}]} if urls else {"type": "null"},
        "flags": {"type": "array", "items": {"oneOf": flags} if flags else False},
        "reason": copy.deepcopy(text),
    })
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["allOf"] = [
        {"if": {"properties": {"precheck": {"properties": {"status": {"const": "PASS"}}}}},
         "then": {"properties": {"scores": scores if urls else False}}},
        {"if": {"properties": {"precheck": {"properties": {"status": {"enum": ["PASS", "BLOCK"]}}}}},
         "then": {"properties": {"precheck": {"properties": {"evidence_refs": refs()}}}}},
    ]
    return schema


def source_review_schema(*, record: dict, passages: list[dict]) -> dict:
    """Guide a source review that cites program-owned passage IDs only.

    The caller binds IDs to original URLs, quotes and headings. This schema
    neither asks the model to reproduce those fields nor proves that a cited
    passage entails its summary. Existing fact/date/license checks still apply.
    """
    kind = record['kind']
    if kind not in KINDS:
        raise ValueError('unsupported source review candidate kind')
    ids = [passage['id'] for passage in passages]
    if any(not isinstance(value, str) or not value.strip() for value in ids):
        raise ValueError('source passage IDs must be nonempty strings')
    ids = sorted(set(ids))
    text = {'$ref': '#/$defs/nonempty_text'}
    fact_fields = ('title', 'category', 'summary', 'reason', 'audience',
                   'usage_conditions', 'detail', 'retention_reason', 'open_source_status')
    facts = {name: dict(text) for name in fact_fields}
    facts['category'] = {'type': 'string', 'enum': list(CATEGORIES)}
    facts['open_source_status'] = {'enum': ['confirmed', 'closed', 'unknown']}
    evidence_fields = ['category', 'summary', 'usage_conditions', 'license']
    if kind == 'reading':
        facts.update(author=dict(text), original_date={'type': 'string', 'pattern': r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$'})
        evidence_fields.extend(('author', 'original_date'))
    elif kind == 'news':
        facts['event_date'] = dict(text)
        evidence_fields.append('event_date')
    elif kind == 'update':
        evidence_fields.append('change_note')
    evidence = {name: {'type': 'array', 'items': {'$ref': '#/$defs/passage_id'}, 'uniqueItems': True,
                       'minItems': 0 if name == 'license' else 1} for name in evidence_fields}
    schema = _object({'qualified': {'type': 'boolean'}, 'reason': dict(text),
                      'facts': {'anyOf': [{'$ref': '#/$defs/facts'}, {'type': 'null'}]},
                      'evidence': {'anyOf': [{'$ref': '#/$defs/evidence'}, {'type': 'null'}]}})
    schema['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
    schema['$defs'] = {
        'nonempty_text': {'type': 'string', 'minLength': 1, 'pattern': r'\S'},
        'passage_id': {'type': 'string', 'enum': ids} if ids else False,
        'facts': _object(facts), 'evidence': _object(evidence),
    }
    schema['allOf'] = [{
        'if': {'properties': {'qualified': {'const': True}}},
        'then': {'properties': {'facts': {'$ref': '#/$defs/facts'}, 'evidence': {'$ref': '#/$defs/evidence'}}},
        'else': {'properties': {'facts': {'type': 'null'}, 'evidence': {'type': 'null'}}},
    }]
    return schema
