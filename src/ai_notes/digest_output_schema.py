"""Closed JSON shape guidance for scoring, not a replacement for review checks.

The ordinary provider API documents JSON-object mode with a schema in the
prompt. It does not promise server-enforced JSON Schema. Quotes, scope and
editorial meaning still require the selection validator and source review.
"""
from __future__ import annotations

import copy


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
