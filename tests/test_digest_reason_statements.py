from __future__ import annotations

import copy
import json
import unittest

from jsonschema import Draft202012Validator

from ai_notes import digest_reason_review as original
from ai_notes import digest_reason_statements as review
from tests.test_digest_reason_review import material_fixture


class StatementBindingTests(unittest.TestCase):
    def test_v2_reuses_exact_v1_units_and_sources_without_mutating_input(self):
        material = material_fixture()
        before = copy.deepcopy(material)
        old = original.build_reason_units(material)
        new = review.build_reason_units(material)
        self.assertEqual('own-refs.v2', review.CONTRACT)
        self.assertEqual(13, review.MAX_REASON_UNITS)
        self.assertEqual(old, [{key: value for key, value in unit.items() if key != 'statements'} for unit in new])
        self.assertEqual(before, material)
        for unit in new:
            self.assertEqual(unit['text'], ''.join(statement['text'] for statement in unit['statements']))
            self.assertEqual(['s' + str(index) for index in range(len(unit['statements']))],
                             [statement['id'] for statement in unit['statements']])
        new[0]['statements'][0]['text'] = 'Returned copy only.'
        new[0]['passages'][0]['quote'] = 'Returned copy only.'
        self.assertEqual(before, material)

    def test_chinese_sentences_preserve_punctuation_and_all_whitespace(self):
        text = ' \r\n支持 Windows；  \t可搜索本地文件。\r\n  \n结果保存在目录中！  '
        statements = review._split_statements(text)
        self.assertEqual([
            {'id': 's0', 'text': ' \r\n支持 Windows；  \t'},
            {'id': 's1', 'text': '可搜索本地文件。\r\n  \n'},
            {'id': 's2', 'text': '结果保存在目录中！  '},
        ], statements)
        self.assertEqual(text, ''.join(statement['text'] for statement in statements))
        self.assertTrue(all(statement['text'].strip() for statement in statements))

    def test_versions_urls_and_file_extensions_are_not_period_sentence_boundaries(self):
        text = '版本 v1.2；详情 https://example.test/a.md。 看结果.'
        statements = review._split_statements(text)
        self.assertEqual(['版本 v1.2；', '详情 https://example.test/a.md。 ', '看结果.'],
                         [statement['text'] for statement in statements])
        self.assertEqual(text, ''.join(statement['text'] for statement in statements))

    def test_english_repeated_statements_have_distinct_program_bound_ids(self):
        text = 'Repeated. Repeated.'
        statements = review._split_statements(text)
        self.assertEqual([{'id': 's0', 'text': 'Repeated. '}, {'id': 's1', 'text': 'Repeated.'}], statements)
        self.assertEqual(text, ''.join(statement['text'] for statement in statements))

    def test_closing_quotes_and_consecutive_punctuation_stay_with_their_sentence(self):
        for text, expected in (
            ('“有用！！”  下一句？', ['“有用！！”  ', '下一句？']),
            ('He said "done."  Next.', ['He said "done."  ', 'Next.']),
            ('开始... 接着；结束。', ['开始... ', '接着；', '结束。']),
        ):
            with self.subTest(text=text):
                statements = review._split_statements(text)
                self.assertEqual(expected, [statement['text'] for statement in statements])
                self.assertEqual(text, ''.join(statement['text'] for statement in statements))

    def test_unicode_symbols_linebreaks_and_no_terminal_text_are_kept_exactly(self):
        for text in ('📁 文件 → 标签\n🤖 多代理协作', '\t仅发现项目、不要求马上实践  ',
                     'A\r\n\r\n B\n C', 'e\u0301 与 👩\u200d💻；保留 Unicode。'):
            with self.subTest(text=text):
                statements = review._split_statements(text)
                self.assertEqual(text, ''.join(statement['text'] for statement in statements))
                self.assertTrue(all(statement['text'].strip() for statement in statements))

    def test_statement_ids_are_stable_after_sorted_json_roundtrip(self):
        material = material_fixture()
        direct = review.build_reason_units(material)
        restored = review.build_reason_units(json.loads(json.dumps(material, sort_keys=True, ensure_ascii=False)))
        self.assertEqual(direct, restored)
        self.assertEqual(direct, review.build_reason_units(material))

    def test_invalid_original_text_cannot_create_empty_statement_units(self):
        for text in (None, '', ' \t\r\n', 1):
            with self.subTest(text=text), self.assertRaises(ValueError):
                review._split_statements(text)


class StatementReviewValidationTests(unittest.TestCase):
    def setUp(self):
        self.material = material_fixture()
        self.unit = next(unit for unit in review.build_reason_units(self.material)
                         if unit['field'] == 'selection_basis.scores.value.reason')
        self.passage_id = self.unit['passages'][0]['id']
        self.assertEqual(2, len(self.unit['statements']))

    def output(self, *, verdict='accept', statuses=None):
        statuses = ['supported'] * len(self.unit['statements']) if statuses is None else statuses
        return {'verdict': verdict, 'reason': '核对原始主张与自身来源。', 'checks': [
            {'statement_id': statement['id'], 'status': status,
             'passage_ids': [self.passage_id] if status in ('supported', 'scope_conflict') else [],
             'reason': '保留来源中的实际主体与范围。'}
            for statement, status in zip(self.unit['statements'], statuses)]}

    def reject_unchanged(self, raw, unit=None):
        before = copy.deepcopy(raw)
        with self.assertRaises(ValueError):
            review.validate_review(raw, self.unit if unit is None else unit)
        self.assertEqual(before, raw)

    def test_closed_schema_uses_statement_ids_and_requires_no_text_echo(self):
        schema = review.review_schema(self.unit)
        Draft202012Validator.check_schema(schema)
        self.assertEqual({'statement_id', 'status', 'passage_ids', 'reason'},
                         set(schema['properties']['checks']['items']['properties']))
        raw = self.output()
        before = copy.deepcopy(raw)
        checked = review.validate_review(raw, self.unit)
        self.assertEqual(raw, checked)
        self.assertEqual(before, raw)
        self.assertTrue(all('text' not in check for check in checked['checks']))
        self.assertNotIn('statements', checked)
        checked['checks'][0]['passage_ids'].clear()
        self.assertEqual(before, raw)

    def test_every_program_bound_id_must_be_reported_exactly_once(self):
        missing = self.output()
        missing['checks'].pop()
        self.reject_unchanged(missing)
        duplicate = self.output(statuses=['supported', 'editorial_judgment'])
        duplicate['checks'][1]['statement_id'] = duplicate['checks'][0]['statement_id']
        self.assertTrue(Draft202012Validator(review.review_schema(self.unit)).is_valid(duplicate))
        self.reject_unchanged(duplicate)
        additional = self.output()
        additional['checks'].append(copy.deepcopy(additional['checks'][0]))
        self.reject_unchanged(additional)

    def test_wrong_statement_or_another_source_passage_id_is_rejected(self):
        raw = self.output()
        raw['checks'][0]['statement_id'] = 'invented-statement'
        self.reject_unchanged(raw)
        foreign = next(p['id'] for p in self.material['passages']
                       if p['evidence_url'] not in self.unit['evidence_refs'])
        raw = self.output()
        raw['checks'][0]['passage_ids'] = [foreign]
        self.reject_unchanged(raw)

    def test_supported_and_scope_conflict_need_own_source_ids(self):
        for status, verdict in (('supported', 'accept'), ('scope_conflict', 'defer')):
            raw = self.output(verdict=verdict, statuses=[status, 'editorial_judgment'])
            raw['checks'][0]['passage_ids'] = []
            self.reject_unchanged(raw)

    def test_negative_check_requires_defer_and_defer_requires_a_negative(self):
        for statuses, verdict in ((['not_supported', 'supported'], 'accept'),
                                  (['scope_conflict', 'editorial_judgment'], 'accept'),
                                  (['supported', 'supported'], 'defer'),
                                  (['editorial_judgment', 'supported'], 'defer')):
            self.reject_unchanged(self.output(verdict=verdict, statuses=statuses))

    def test_editorial_judgment_and_source_linked_negative_shapes_are_allowed(self):
        for statuses, verdict in ((['editorial_judgment', 'supported'], 'accept'),
                                  (['not_supported', 'supported'], 'defer'),
                                  (['scope_conflict', 'editorial_judgment'], 'defer')):
            raw = self.output(verdict=verdict, statuses=statuses)
            self.assertEqual(raw, review.validate_review(raw, self.unit))

    def test_echo_additional_keys_missing_keys_and_wrong_types_are_not_repaired(self):
        for mutate in (
            lambda raw: raw['checks'][0].update(text=self.unit['statements'][0]['text']),
            lambda raw: raw.update(adjusted_score=90),
            lambda raw: raw['checks'][0].pop('reason'),
            lambda raw: raw['checks'][0].update(statement_id=0),
            lambda raw: raw['checks'][0].update(status='unverified'),
            lambda raw: raw['checks'][0].update(passage_ids=[self.passage_id, self.passage_id]),
        ):
            raw = self.output()
            mutate(raw)
            self.reject_unchanged(raw)

    def test_check_order_may_differ_without_inventing_text_or_missing_a_statement(self):
        raw = self.output()
        raw['checks'].reverse()
        self.assertEqual(raw, review.validate_review(raw, self.unit))

    def test_corrupted_or_incomplete_program_binding_fails_before_review(self):
        variants = []
        missing = copy.deepcopy(self.unit)
        missing['statements'].pop()
        variants.append(missing)
        changed = copy.deepcopy(self.unit)
        changed['statements'][0]['text'] = 'Rewritten original statement.'
        variants.append(changed)
        duplicates = copy.deepcopy(self.unit)
        duplicates['statements'][1]['id'] = duplicates['statements'][0]['id']
        variants.append(duplicates)
        extra = copy.deepcopy(self.unit)
        extra['statements'][0]['offset'] = 0
        variants.append(extra)
        for unit in variants:
            with self.assertRaises(ValueError):
                review.review_schema(unit)


if __name__ == '__main__':
    unittest.main()
