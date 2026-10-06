from __future__ import annotations

import copy
import unittest

from ai_notes import digest_editorial as editorial
from ai_notes import digest_reason_review as reasons
from ai_notes import digest_reason_statements as statements


class LicenseReviewScopeTests(unittest.TestCase):
    def setUp(self):
        self.url = 'https://example.test/source'
        self.contexts = [{'url': self.url, 'text': (
            '# Purpose\nOrganizes local files on Windows.\n\n'
            '# Licence\nMIT applies to files outside the application directory.\n')}]
        self.arguments = dict(
            record={'url': self.url, 'kind': 'project'},
            facts={'title': '文件工具', 'category': '开源项目', 'summary': '整理本地文件。',
                   'supported_systems': 'Windows'},
            assessment={'scores': {'value': {'score': 7,
                'reason': 'MIT 覆盖所有组件。它能整理文件。', 'evidence_refs': [self.url]}},
                'reason': '值得发现的文件工具。'},
            contexts=self.contexts, ranking_type='daily', review_scope='public-introduction.v1',
            introduction_contract='discovery.v1')

    def material(self, scoped=False):
        options = {'license_review_scope': editorial.LICENSE_REVIEW_SCOPE} if scoped else {}
        return editorial.build_review_input(**self.arguments, **options)

    def test_scope_is_explicit_material_data_without_changing_original_prose(self):
        before = copy.deepcopy(self.arguments)
        legacy = self.material()
        scoped = self.material(True)
        self.assertEqual('excluded.v1', scoped.pop('license_review_scope'))
        self.assertEqual(legacy, scoped)
        self.assertEqual(before, self.arguments)
        self.assertNotIn('license_review_scope', legacy)

    def test_only_known_explicit_scope_is_accepted(self):
        for scope in ('unknown', '', 0, {}, []):
            with self.subTest(scope=scope), self.assertRaisesRegex(ValueError, 'licence review scope'):
                editorial.build_review_input(**self.arguments, license_review_scope=scope)
        self.assertEqual('', editorial.license_review_supplement(None))

    def test_unmarked_reason_prompts_are_exactly_unchanged(self):
        unit = reasons.build_reason_units(self.material())[0]
        self.assertEqual(reasons.REASON_REVIEW_PROMPT, reasons.review_prompt(unit))
        self.assertEqual(statements.REASON_REVIEW_PROMPT, statements.review_prompt(unit))
        self.assertIn('licence', reasons.review_prompt(unit))

    def test_scope_reaches_each_frozen_unit_without_rewriting_own_refs(self):
        material = self.material(True)
        before = copy.deepcopy(material)
        for module in (reasons, statements):
            legacy = module.build_reason_units(self.material())
            scoped = module.build_reason_units(material)
            self.assertEqual(len(legacy), len(scoped))
            for old, new in zip(legacy, scoped):
                with self.subTest(contract=module.CONTRACT, field=new['field']):
                    self.assertEqual('excluded.v1', new.pop('license_review_scope'))
                    self.assertEqual(old, new)
        self.assertEqual(before, material)

    def test_new_prompt_scope_preserves_checks_for_mixed_facts_and_no_new_claims(self):
        for module in (reasons, statements):
            unit = module.build_reason_units(self.material(True))[0]
            prompt = module.review_prompt(unit)
            self.assertEqual(module.REASON_REVIEW_PROMPT + '\n' + editorial.LICENSE_REVIEW_SUPPLEMENT, prompt)
            self.assertIn('mixed statement', prompt)
            self.assertIn('not wholly exempt', prompt)
            self.assertIn('do not describe excluded licensing assertions as verified', prompt)
            self.assertIn('Do not add licensing or commercial-use conclusions', prompt)

    def test_unknown_or_null_material_and_unit_marker_never_silently_bypasses_review(self):
        for scope in (None, 'unknown', '', {}):
            for module in (reasons, statements):
                with self.subTest(scope=scope, contract=module.CONTRACT):
                    material = self.material()
                    material['license_review_scope'] = scope
                    with self.assertRaisesRegex(ValueError, 'licence review scope'):
                        module.build_reason_units(material)
                    unit = module.build_reason_units(self.material())[0]
                    unit['license_review_scope'] = scope
                    with self.assertRaisesRegex(ValueError, 'licence review scope'):
                        module.review_prompt(unit)

    def test_scope_keeps_schema_and_untouched_mixed_fact_defer(self):
        for module in (reasons, statements):
            unit = module.build_reason_units(self.material(True))[0]
            legacy = module.build_reason_units(self.material())[0]
            self.assertEqual(module.review_schema(legacy), module.review_schema(unit))
            checks = [{'status': 'editorial_judgment', 'passage_ids': [],
                       'reason': '许可解释不在本轮核对范围，未确认许可结论。'},
                      {'status': 'not_supported', 'passage_ids': [],
                       'reason': '其余功能主张需要自身原文依据。'}]
            if module is statements:
                for check, statement in zip(checks, unit['statements']):
                    check['statement_id'] = statement['id']
            else:
                checks[0]['text'] = 'MIT 覆盖所有组件。'
                checks[1]['text'] = '它能整理文件。'
            raw = {'verdict': 'defer', 'reason': '功能主张仍须核对。', 'checks': checks}
            before = copy.deepcopy(raw)
            self.assertEqual(raw, module.validate_review(raw, unit))
            self.assertEqual(before, raw)
            raw['verdict'] = 'accept'
            with self.assertRaises(ValueError):
                module.validate_review(raw, unit)

    def test_scope_does_not_allow_skipping_program_bound_statements(self):
        unit = statements.build_reason_units(self.material(True))[0]
        raw = {'verdict': 'accept', 'reason': '许可不核对。', 'checks': [{
            'statement_id': unit['statements'][0]['id'], 'status': 'editorial_judgment',
            'passage_ids': [], 'reason': '许可解释不在核对范围。'}]}
        with self.assertRaises(ValueError):
            statements.validate_review(raw, unit)


if __name__ == '__main__':
    unittest.main()
