"""Opt-in selection repairs; frozen contracts and strict evidence stay intact."""
from __future__ import annotations

import copy
import hashlib
import tempfile
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from ai_notes import digest_selection as selection
from ai_notes.digest_output_schema import source_review_schema
from ai_notes.digest_understanding import bind_understanding_review
from tests import test_digest_selection_projection as projection_fixtures
from tests import test_digest_understanding as understanding_fixtures

ROOT = Path(__file__).resolve().parents[1]


class SelectionFixContractTests(unittest.TestCase):
    def setUp(self):
        self.current = selection.load_policy(ROOT)
        self.current.pop('selection_refinement_contract', None)
        self.current.pop('score_input_contract', None)
        self.current.pop('license_review_scope', None)  # Exercise the frozen v12 contract.
        self.legacy = copy.deepcopy(self.current)
        self.legacy.pop('source_reading_contract')
        self.legacy.pop('editorial_scope')
        self.legacy['version'] = 'v11-discovery-introduction-uncalibrated'

    def test_opt_in_contracts_require_known_dependencies(self):
        self.assertEqual(self.current, selection.validate_policy(self.current))
        self.assertEqual(self.legacy, selection.validate_policy(self.legacy))
        for field in ('source_reading_contract', 'editorial_scope'):
            for invalid in (None, {}, 'unknown'):
                policy = copy.deepcopy(self.current)
                policy[field] = invalid
                with self.subTest(field=field, invalid=invalid), self.assertRaises(selection.SelectionError):
                    selection.validate_policy(policy)
            for dependency in ('introduction_contract', 'understanding_contract'):
                policy = copy.deepcopy(self.current)
                policy.pop(dependency)
                with self.subTest(field=field, missing=dependency), self.assertRaises(selection.SelectionError):
                    selection.validate_policy(policy)
        policy = copy.deepcopy(self.current)
        policy.pop('editorial_review_contract')
        with self.assertRaisesRegex(selection.SelectionError, 'source-score'):
            selection.validate_policy(policy)

    def test_old_prompt_bytes_and_authoritative_snapshot_are_not_upgraded(self):
        current_text = (ROOT / selection.PROMPT_PATH).read_text(encoding='utf-8')
        v11 = current_text.partition(selection.SELECTION_FIX_PROMPT_MARKER)[0]
        self.assertEqual(v11, selection.load_prompt(ROOT, self.legacy))
        v10_policy = copy.deepcopy(self.legacy)
        v10_policy.pop('introduction_contract')
        v10 = v11.partition(selection.INTRODUCTION_PROMPT_MARKER)[0]
        self.assertEqual(v10, selection.load_prompt(ROOT, v10_policy))
        older = copy.deepcopy(v10_policy)
        older.pop('understanding_contract')
        base = v10.partition(selection.UNDERSTANDING_PROMPT_MARKER)[0]
        self.assertEqual(base, selection.load_prompt(ROOT, older))
        v12 = current_text.partition(selection.SELECTION_REFINEMENT_PROMPT_MARKER)[0]
        self.assertEqual(v12, selection.load_prompt(ROOT, self.current))
        frozen = {'policy': self.legacy, 'prompt_text': v11,
                  'prompt_hash': hashlib.sha256(v11.encode('utf-8')).hexdigest()}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / selection.PROMPT_PATH
            path.parent.mkdir(parents=True)
            path.write_text('Unrelated later prompt.', encoding='utf-8')
            self.assertEqual(v11, selection.get_prompt(root, frozen))

    def test_scoring_marker_projection_is_policy_owned_and_keeps_weights(self):
        fixture = projection_fixtures.ProjectReadingProjectionTests()
        fixture.setUp()
        fixture.enable()
        fixture.policy.update(copy.deepcopy(self.legacy))
        old = fixture.projected()
        fixture.card['material'].update(source_reading_contract=selection.SOURCE_READING_CONTRACT,
                                        editorial_scope=selection.PUBLIC_REVIEW_SCOPE)
        fixture.card.update(editorial_scope=selection.PUBLIC_REVIEW_SCOPE)
        self.assertEqual(old, fixture.projected())
        fixture.policy.update(copy.deepcopy(self.current))
        new = fixture.projected()
        self.assertEqual(old['card'], new['card'])
        self.assertEqual(selection.SOURCE_READING_CONTRACT, new['policy']['source_reading_contract'])
        self.assertEqual(selection.PUBLIC_REVIEW_SCOPE, new['policy']['editorial_scope'])
        for key in ('profiles', 'thresholds', 'flag_caps', 'defer_flags', 'flag_basis', 'usage_evidence_gaps'):
            self.assertEqual(self.legacy[key], self.current[key])
        assessment = dict(precheck=dict(status='PASS', reasons=['Documented.'], evidence_refs=[fixture.url]),
                          scores={key: dict(score=7, reason='Source-backed.', evidence_refs=[fixture.url])
                                  for key in selection.DIMENSIONS}, flags=[], reason='A useful discovery.')
        self.assertEqual(selection._calculate(self.legacy, fixture.card, assessment),
                         selection._calculate(self.current, fixture.card, assessment))


class InlinePassageIdTests(unittest.TestCase):
    def setUp(self):
        self.fixture = understanding_fixtures.UnderstandingContractTests()
        self.fixture.setUp()

    def schema(self, **options):
        return source_review_schema(record=self.fixture.record, passages=self.fixture.passages,
                                    include_understanding=True, **options)

    def test_default_shape_stays_legacy_and_literal_choices_are_local(self):
        old = self.schema()
        self.assertEqual(old, self.schema(inline_passage_ids=False))
        new = self.schema(inline_passage_ids=True)
        Draft202012Validator.check_schema(new)
        validator = Draft202012Validator(new)
        self.assertTrue(validator.is_valid(self.fixture.output))
        ids = sorted(row['id'] for row in self.fixture.passages)
        expected = {'type': 'string', 'enum': ids}
        self.assertEqual(expected, new['$defs']['evidence']['properties']['summary']['items'])
        self.assertEqual(expected, new['$defs']['understanding']['properties']['purpose']['properties']['passage_ids']['items'])
        self.assertEqual(self.schema(), old)

    def test_null_unknown_cannot_grow_evidence_capacity(self):
        validator = Draft202012Validator(self.schema(inline_passage_ids=True))
        for field in ('input', 'output'):
            raw = copy.deepcopy(self.fixture.output)
            raw['understanding'][field] = dict(text=None, passage_ids=[])
            self.assertTrue(validator.is_valid(raw))
            raw['understanding'][field]['passage_ids'] = [self.fixture.purpose_id]
            with self.subTest(field=field):
                self.assertFalse(validator.is_valid(raw))
                with self.assertRaisesRegex(ValueError, 'unknown'):
                    bind_understanding_review(raw, self.fixture.passages, self.fixture.record, self.fixture.documents)

    def test_expression_duplicate_or_unowned_ids_are_rejected_without_repair(self):
        validator = Draft202012Validator(self.schema(inline_passage_ids=True))
        pid = self.fixture.purpose_id
        for ids in ([pid + ".replace('x', 'y')"], [pid, pid], ['made-up-id']):
            raw = copy.deepcopy(self.fixture.output)
            raw['evidence']['summary'] = ids
            frozen = copy.deepcopy(raw)
            with self.subTest(ids=ids):
                self.assertFalse(validator.is_valid(raw))
                with self.assertRaises(ValueError):
                    bind_understanding_review(raw, self.fixture.passages, self.fixture.record, self.fixture.documents)
                self.assertEqual(frozen, raw)
        with self.assertRaisesRegex(ValueError, 'distinct'):
            source_review_schema(record=self.fixture.record,
                                 passages=self.fixture.passages + [self.fixture.passages[0]], inline_passage_ids=True)


if __name__ == '__main__':
    unittest.main()
