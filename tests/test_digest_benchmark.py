from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ai_notes import digest_benchmark as bench, digest_selection as selection


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.code = Path(__file__).resolve().parents[1]
        self.policy = selection.load_policy(self.code)
        self.protocol = {'policy': self.policy, 'questions': bench.questions(self.policy)}
        self.card = {'candidate_id': 'a', 'title': 'Test', 'canonical_url': 'https://example.com/a',
                     'category': '开源项目', 'profile': 'practical', 'kind': 'project', 'event': None,
                     'evidence_context': [{'url': 'https://example.com/a', 'text': 'Readme', 'fetched_at': '2026-10-03T19:00:00+08:00'}],
                     'pilot': True, 'split': 'dev', 'data_notes': []}
        self.answers = {'category': '开源项目', 'precheck': 'PASS', **{k: '7' for k in bench.DIMENSIONS},
                        **{k: 'no' for k in bench.FLAG_RULES}}

    def freeze(self, cards):
        path = self.root / 'dataset.json'
        path.write_text(json.dumps({'cards': cards}), encoding='utf-8')
        return bench.freeze(self.root / 'experiment', path, self.code)

    def test_input_never_exposes_previous_judgments_or_split(self):
        card = {**self.card, 'reason': 'SELECT THIS', 'score': 100, 'prior_review': 'select'}
        state = bench.state_for(card)
        self.assertNotIn('SELECT THIS', json.dumps(state))
        self.assertEqual({'title', 'canonical_url', 'kind', 'event', 'evidence_context'}, set(state))

    def test_same_typed_answers_produce_same_capped_decision(self):
        answers = {**self.answers, 'unsupported_promotion': 'yes'}
        jev = {'answers': {k: {'choice': v, 'confidence': .8} for k, v in answers.items()}}
        glm_result = bench.normalize(answers, self.protocol, self.card, 'glm')
        jev_result = bench.normalize(jev, self.protocol, self.card, 'jev')
        self.assertEqual('defer', glm_result['decision'])
        self.assertEqual(4, glm_result['effective_scores']['value'])
        self.assertEqual(3, glm_result['effective_scores']['evidence'])
        self.assertEqual(glm_result['score'], jev_result['score'])
        self.assertEqual(glm_result['decision'], jev_result['decision'])

    def test_unknown_is_not_zero_and_missing_original_cannot_pass(self):
        value = bench.normalize({**self.answers, 'evidence': 'UNKNOWN'}, self.protocol, self.card, 'glm')
        self.assertIsNone(value['score'])
        self.assertEqual('UNKNOWN', value['precheck'])
        self.assertEqual('PASS', value['original_precheck'])
        self.assertEqual(['evidence'], value['unknown_dimensions'])
        self.assertEqual('7', value['raw_choices']['value'])
        card = {**self.card, 'evidence_context': []}
        value = bench.normalize(self.answers, self.protocol, card, 'glm')
        self.assertEqual('defer', value['decision'])
        self.assertIsNone(value['raw_scores'])

    def test_extra_answers_and_invalid_scale_are_rejected(self):
        for change in ({'unexpected': 'yes'}, {'value': '11'}, {'value': 7.3}, {'value': True}):
            with self.assertRaises(ValueError):
                bench.normalize({**self.answers, **change}, self.protocol, self.card, 'glm')

    def test_glm_json_integer_and_string_scores_are_losslessly_equivalent(self):
        numbers = {**self.answers, **{k: 7 for k in bench.DIMENSIONS}}
        self.assertEqual(bench.normalize(self.answers, self.protocol, self.card, 'glm'),
                         bench.normalize(numbers, self.protocol, self.card, 'glm'))

    def test_pilot_can_extend_but_not_change_or_leak_across_splits(self):
        first = self.freeze([self.card])
        second = deepcopy(self.card)
        second.update(candidate_id='b', canonical_url='https://example.com/b', pilot=False, split='holdout')
        extended = self.freeze([self.card, second])
        self.assertEqual(first['protocol_hash'], extended['protocol_hash'])
        changed = {**self.card, 'title': 'Changed after seeing scores'}
        with self.assertRaises(ValueError): self.freeze([changed, second])
        second['canonical_url'] = self.card['canonical_url']
        with self.assertRaises(ValueError): self.freeze([self.card, second])

    def test_summary_never_treats_missing_results_as_model_disagreement(self):
        self.freeze([self.card])
        report = bench.summarize(self.root / 'experiment')
        self.assertEqual(0, report['metrics']['paired'])
        self.assertEqual(0, report['metrics']['human_labels'])
        self.assertEqual('not_run', report['rows'][0]['jev']['status'])

    def test_lossless_unwrap_preserves_answers_and_rejects_ambiguity(self):
        expected = set(self.answers)
        cases = [{'answer': self.answers}, {'answer': json.dumps(self.answers)},
                 {**self.answers, 'answer': self.answers}, {k: {'answer': v} for k,v in self.answers.items()}]
        for raw in cases:
            recovered, notes = bench.unwrap_glm(raw, expected)
            self.assertEqual(self.answers, recovered)
            self.assertTrue(notes)
        for raw in ({**self.answers, 'answer': {**self.answers, 'value': '4'}},
                    {'answer': self.answers, 'extra': True}, {'answer': 'not json'}):
            with self.assertRaises(ValueError): bench.unwrap_glm(raw, expected)

    def test_failed_semantic_result_still_counts_usage_and_time(self):
        self.freeze([self.card])
        root = self.root / 'experiment'
        bench._save(root / 'results/glm/a.json', {'status': 'failed', 'elapsed_seconds': 4,
                    'usage': {'prompt_tokens': 123, 'completion_tokens': 9}})
        report = bench.summarize(root)
        self.assertEqual(123, report['metrics']['glm']['input_tokens'])
        self.assertEqual(9, report['metrics']['glm']['output_tokens'])
        self.assertEqual(0, report['metrics']['paired'])

    def test_frozen_cascade_escalates_unknown_low_confidence_and_borderline(self):
        protocol = {**self.protocol, 'routing': {'confidence_threshold': .8, 'margin_to_threshold': 5}}
        answers = {k: {'choice': v, 'confidence': .9} for k,v in self.answers.items()}
        result = bench.normalize({'answers': answers}, protocol, self.card, 'jev')
        self.assertEqual(['near_threshold'], bench.route_to_glm(result, protocol))
        result['score'] = 80
        self.assertEqual([], bench.route_to_glm(result, protocol))
        result['confidence']['value'] = .5
        self.assertEqual(['low_confidence'], bench.route_to_glm(result, protocol))
        result['precheck'] = 'UNKNOWN'
        self.assertIn('unknown', bench.route_to_glm(result, protocol))

    def test_unsafe_ids_and_changed_frozen_data_cannot_escape_or_run(self):
        for ident in ('../elsewhere', '/absolute', 'C:/absolute', 'a\\b'):
            with self.assertRaises(ValueError): self.freeze([{**self.card, 'candidate_id': ident}])
        self.freeze([self.card])
        path = self.root / 'experiment/frozen.json'
        frozen = json.loads(path.read_text(encoding='utf-8'))
        frozen['dataset']['cards'][0]['title'] = 'Changed'
        path.write_text(json.dumps(frozen), encoding='utf-8')
        with self.assertRaises(ValueError): bench.summarize(path.parent)

    def test_failed_json_usage_is_read_from_exact_receipt_without_network(self):
        folder = self.root / 'glm/data/weekly_digest'
        folder.mkdir(parents=True)
        key = 'a' * 64
        with sqlite3.connect(folder / 'runtime.sqlite3') as con:
            con.execute('CREATE TABLE requests(request_id TEXT, usage TEXT, error TEXT, stage TEXT, budget_key TEXT, status TEXT, output TEXT)')
            con.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?)', (key, json.dumps({'prompt_tokens': 500}),
                'HTTPStatusError:402', 'benchmark-choice.v1', 'glm-jev-compare.v1', 'failed', None))
        con.close()
        receipt = bench._failed_glm_receipt(self.root, ValueError('failed: ' + key))
        self.assertEqual(500, receipt['usage']['prompt_tokens'])
        self.assertEqual(key, receipt['receipt_id'])
        self.assertEqual({}, bench._failed_glm_receipt(self.root, ValueError('failed: ' + 'b' * 64)))
        self.freeze([self.card])
        frozen = json.loads((self.root / 'experiment/frozen.json').read_text(encoding='utf-8'))
        bench._save(self.root / 'frozen.json', frozen)
        result = {'status': 'failed', **receipt}
        path = self.root / 'results/glm/a.json'
        bench._save(path, result)
        bench.inspect_receipts(self.root)
        self.assertEqual(result, json.loads(path.read_text(encoding='utf-8')))


if __name__ == '__main__':
    unittest.main()
