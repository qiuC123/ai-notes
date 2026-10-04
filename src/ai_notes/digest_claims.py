"""Quoted source claims shared by extraction and frozen scoring material.

This checks structure and literal source association, not whether a model's
interpretation is correct. Enum classifications remain model judgements.
"""
from __future__ import annotations

import copy


SCOPE_FIELDS = frozenset(('version', 'platform', 'host_architecture',
                          'build_architecture', 'installation_path',
                          'requirement', 'conditions', 'evidence_kind'))
SCOPE_TEXT_FIELDS = SCOPE_FIELDS - {'requirement', 'conditions', 'evidence_kind'}
REQUIREMENTS = frozenset(('required', 'optional', 'not_applicable', 'unknown'))
EVIDENCE_KINDS = frozenset(('documentation', 'maintenance_record', 'usage_report',
                           'media_link', 'announcement', 'unknown'))


def validate_claims(claims, contexts, *, require_scope=False):
    """Return an unmodified deep copy after mechanical quotation checks.

    Legacy four-field claims are valid unless a fresh extraction specifically
    requires usage scopes. Missing scope is never filled in or inferred.
    """
    originals = {context['url']: context['text'] for context in contexts}
    if not isinstance(claims, list) or not claims:
        raise ValueError('source claims required')
    base = {'field', 'text', 'evidence_url', 'quote'}
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) not in (base, base | {'scope'}):
            raise ValueError('invalid source claim')
        if any(not isinstance(claim[key], str) or not claim[key].strip() for key in base):
            raise ValueError('empty source claim')
        if claim['evidence_url'] not in originals or claim['quote'] not in originals[claim['evidence_url']]:
            raise ValueError('claim quote not found in supplied original')
        if 'scope' not in claim:
            if require_scope and claim['field'] == 'usage_conditions':
                raise ValueError('usage_conditions source claim requires scope')
            continue
        scope = claim['scope']
        if not isinstance(scope, dict) or set(scope) != SCOPE_FIELDS:
            raise ValueError('invalid source claim scope fields')
        for key in SCOPE_TEXT_FIELDS:
            value = scope[key]
            if value is not None and (not isinstance(value, str) or not value.strip() or value not in claim['quote']):
                raise ValueError('scope ' + key + ' must be a literal span in claim quote or null')
        for key, choices in (('requirement', REQUIREMENTS), ('evidence_kind', EVIDENCE_KINDS)):
            if not isinstance(scope[key], str) or scope[key] not in choices:
                raise ValueError('invalid scope ' + key)
        conditions = scope['conditions']
        if not isinstance(conditions, list) or any(
                not isinstance(value, str) or not value.strip() or value not in claim['quote'] for value in conditions):
            raise ValueError('scope conditions must be literal spans in claim quote')
    return copy.deepcopy(claims)
