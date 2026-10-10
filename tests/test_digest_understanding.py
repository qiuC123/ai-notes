from __future__ import annotations

import copy
import hashlib
import unittest

from jsonschema import Draft202012Validator

from ai_notes.digest_output_schema import source_review_schema
from ai_notes.digest_passages import bind_review, build_passages
from ai_notes.digest_understanding import (
    SOURCE_REVIEW_CONTRACT, SOURCE_SCOPE_VERSION, UNDERSTANDING_VERSION,
    bind_understanding_review, prose_scope_issues, source_documents, validate_understanding,
)


def scope_for(text, *, coverage='complete_text', document_chars=None, ranges=None):
    return dict(schema_version=SOURCE_SCOPE_VERSION, coverage=coverage,
                document_chars=len(text) if document_chars is None else document_chars,
                supplied_chars=len(text), ranges=([dict(start=0, end=len(text))] if text else [])
                if ranges is None else ranges,
                document_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest()
                if coverage == 'complete_text' else None,
                reader='fixture', commit_sha=None, file_path=None)


class SourceScopeTests(unittest.TestCase):
    def test_missing_metadata_is_unknown_without_changing_text_or_passage_ids(self):
        contexts = [dict(url='https://example.com/readme', text='# Usage\n完整说明😀\n')]
        frozen = copy.deepcopy(contexts)
        before = build_passages(contexts)
        documents = source_documents(contexts)
        self.assertEqual('unknown', documents[0]['scope']['coverage'])
        self.assertIsNone(documents[0]['scope']['document_chars'])
        self.assertIsNone(documents[0]['scope']['document_sha256'])
        self.assertEqual(len(contexts[0]['text']), documents[0]['scope']['supplied_chars'])
        self.assertEqual([], documents[0]['scope']['ranges'])
        self.assertEqual(before, build_passages(contexts))
        documents[0]['scope']['ranges'].append(dict(start=0, end=3))
        self.assertEqual(frozen, contexts)

    def test_complete_text_uses_unicode_character_counts_and_decoded_text_hash(self):
        text = '中文😀\r\nReadme'
        context = dict(url='https://example.com/readme', text=text, source_scope=scope_for(text))
        frozen = copy.deepcopy(context)
        document = source_documents([context])[0]
        self.assertEqual(len(text), document['scope']['document_chars'])
        self.assertNotEqual(len(text.encode('utf-8')), document['scope']['supplied_chars'])
        document['scope']['reader'] = 'changed copy'
        self.assertEqual(frozen, context)
        context['source_scope']['document_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'SHA|sha256'):
            source_documents([context])

    def test_disjoint_original_ranges_and_compiled_separators_remain_excerpt(self):
        text = '第一段😀\n\n末尾'
        ranges = [dict(start=0, end=4), dict(start=30, end=32)]
        scope = scope_for(text, coverage='excerpt', document_chars=40, ranges=ranges)
        scope['reader'] = 'compiled-excerpts'
        documents = source_documents([dict(url='https://example.com/doc', text=text, source_scope=scope)])
        self.assertEqual('excerpt', documents[0]['scope']['coverage'])
        self.assertEqual(ranges, documents[0]['scope']['ranges'])
        self.assertEqual(len(text), documents[0]['scope']['supplied_chars'])
        self.assertEqual(6, sum(span['end'] - span['start'] for span in ranges))

    def test_complete_text_cannot_have_gaps_or_unknown_document_length(self):
        for change in (
            lambda scope: scope.update(ranges=[dict(start=0, end=2), dict(start=3, end=5)]),
            lambda scope: scope.update(document_chars=None),
            lambda scope: scope.update(ranges=[]),
        ):
            scope = scope_for('ABCDE')
            change(scope)
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                source_documents([dict(url='https://example.com/doc', text='ABCDE', source_scope=scope)])

    def test_scope_refuses_bad_counts_overlap_bounds_and_extra_model_fields(self):
        changes = (
            lambda scope: scope.update(supplied_chars=4),
            lambda scope: scope.update(document_chars=True),
            lambda scope: scope.update(ranges=[dict(start=0, end=3), dict(start=2, end=4)]),
            lambda scope: scope.update(ranges=[dict(start=0, end=6)]),
            lambda scope: scope.update(ranges=[dict(start=False, end=3)]),
            lambda scope: scope.update(ranges=[dict(start=0, end=0)]),
            lambda scope: scope.update(ranges=[]),
            lambda scope: scope.update(coverage='full_repo'),
            lambda scope: scope.update(reason='Model says complete.'),
        )
        for change in changes:
            scope = scope_for('ABCDE', coverage='excerpt', document_chars=5)
            change(scope)
            context = dict(url='https://example.com/doc', text='ABCDE', source_scope=scope)
            frozen = copy.deepcopy(context)
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                source_documents([context])
            self.assertEqual(frozen, context)

    def test_duplicate_url_requires_same_text_and_metadata(self):
        context = dict(url='https://example.com/doc', text='ABCDE', source_scope=scope_for('ABCDE'))
        self.assertEqual(1, len(source_documents([context, copy.deepcopy(context)])))
        different = copy.deepcopy(context)
        different['source_scope']['reader'] = 'another-reader'
        with self.assertRaisesRegex(ValueError, 'different text or scope'):
            source_documents([context, different])


class UnderstandingContractTests(unittest.TestCase):
    def setUp(self):
        self.url = 'https://example.com/readme'
        self.contexts = [dict(url=self.url, text='# Purpose\nOrganizes local files.\n\n'
            '# Usage\nImports paths and returns folders. GPU acceleration is optional.\n')]
        self.passages = build_passages(self.contexts)
        self.purpose_id, self.usage_id = [passage['id'] for passage in self.passages]
        self.record = dict(kind='project', url=self.url)
        self.legacy = dict(qualified=True, reason='Original text supports the task.',
            facts=dict(title='File organizer', category='开源项目', summary='Organizes files.',
                reason='Reduces repetitive work.', audience='People organizing files.',
                usage_conditions='Import local file paths; GPU acceleration is optional.',
                detail='Returns organized folders.', retention_reason='A reusable local workflow.',
                open_source_status='unknown'),
            evidence=dict(category=[self.purpose_id], summary=[self.purpose_id, self.usage_id],
                          usage_conditions=[self.usage_id], license=[]))
        self.raw = dict(purpose=dict(text='Organizes local files.', passage_ids=[self.purpose_id]),
            input=dict(text='Local file paths.', passage_ids=[self.usage_id]),
            output=dict(text='Organized folders.', passage_ids=[self.usage_id]),
            operations=[dict(input='Local file paths.', action='Organize the files.',
                             output='Folders.', passage_ids=[self.purpose_id, self.usage_id])],
            conditions=[dict(subject='GPU acceleration', kind='optional',
                             text='GPU acceleration is optional.', passage_ids=[self.usage_id])],
            unknowns=['The supported operating systems are not established here.'])
        self.output = dict(self.legacy, understanding=self.raw)
        self.documents = source_documents(self.contexts)

    def bound(self, output=None, documents=None):
        return bind_understanding_review(output or self.output, self.passages, self.record,
                                         self.documents if documents is None else documents)

    def validator(self, *, include_understanding=True):
        schema = source_review_schema(record=self.record, passages=self.passages,
                                      include_understanding=include_understanding)
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)

    def test_old_schema_and_old_bound_review_remain_unchanged(self):
        default = source_review_schema(record=self.record, passages=self.passages)
        explicit = source_review_schema(record=self.record, passages=self.passages, include_understanding=False)
        self.assertEqual(default, explicit)
        self.assertEqual({'qualified', 'reason', 'facts', 'evidence'}, set(default['properties']))
        self.assertNotIn('understanding', default['$defs'])
        self.assertTrue(self.validator(include_understanding=False).is_valid(self.legacy))
        self.assertFalse(self.validator(include_understanding=False).is_valid(self.output))
        legacy_bound = bind_review(self.legacy, self.passages, self.record)
        new_bound = self.bound()
        del new_bound['facts']['understanding']
        self.assertEqual(legacy_bound, new_bound)

    def test_schema_accepts_raw_card_and_only_program_binds_proofs_once(self):
        frozen = copy.deepcopy((self.output, self.passages, self.documents))
        self.assertEqual('passages.understood.v1', SOURCE_REVIEW_CONTRACT)
        self.assertTrue(self.validator().is_valid(self.output))
        card = self.bound()['facts']['understanding']
        self.assertEqual(UNDERSTANDING_VERSION, card['schema_version'])
        self.assertEqual({self.purpose_id, self.usage_id}, set(card['proof_map']))
        for passage in self.passages:
            self.assertEqual({key: passage[key] for key in ('evidence_url', 'quote', 'heading_path')},
                             card['proof_map'][passage['id']])
        self.assertNotIn('quote', card['purpose'])
        self.assertEqual(self.documents, card['source_documents'])
        self.assertEqual([], card['reading_scope_issues'])
        copied = validate_understanding(card)
        copied['proof_map'][self.purpose_id]['quote'] = 'changed copy'
        self.assertNotEqual(copied, card)
        self.assertEqual(frozen, (self.output, self.passages, self.documents))

    def test_unknown_or_another_snapshot_id_is_refused_without_mutation(self):
        foreign = build_passages([dict(url=self.url, text='Another snapshot.')])[0]['id']
        for bad_id in ('invented', foreign):
            output = copy.deepcopy(self.output)
            output['understanding']['conditions'][0]['passage_ids'] = [bad_id]
            frozen = copy.deepcopy(output)
            self.assertFalse(self.validator().is_valid(output))
            with self.assertRaisesRegex(ValueError, 'unknown'):
                self.bound(output)
            self.assertEqual(frozen, output)

    def test_unknown_inputs_or_outputs_cannot_have_invented_evidence(self):
        for field in ('input', 'output'):
            output = copy.deepcopy(self.output)
            output['understanding'][field] = dict(text=None, passage_ids=[])
            self.assertTrue(self.validator().is_valid(output))
            self.assertIsNone(self.bound(output)['facts']['understanding'][field]['text'])
            output['understanding'][field]['passage_ids'] = [self.usage_id]
            self.assertFalse(self.validator().is_valid(output))
            with self.assertRaisesRegex(ValueError, 'cannot fabricate evidence'):
                self.bound(output)

    def test_extra_fields_bad_enums_empty_and_repeated_ids_are_refused(self):
        changes = (
            lambda raw: raw.update(source_documents=[]),
            lambda raw: raw['purpose'].update(quote='Model-generated quote'),
            lambda raw: raw['purpose'].update(text=None),
            lambda raw: raw['input'].update(passage_ids=[]),
            lambda raw: raw['conditions'][0].update(kind='requirement'),
            lambda raw: raw['conditions'][0].update(passage_ids=[self.usage_id, self.usage_id]),
            lambda raw: raw['operations'][0].update(action=' '),
            lambda raw: raw.update(operations=raw['operations'] * 5),
            lambda raw: raw.update(conditions=raw['conditions'] * 9),
        )
        for change in changes:
            output = copy.deepcopy(self.output)
            change(output['understanding'])
            with self.subTest(raw=output['understanding']):
                self.assertFalse(self.validator().is_valid(output))
                with self.assertRaises(ValueError):
                    self.bound(output)

    def test_unqualified_requires_null_understanding_and_retains_legacy_result(self):
        output = dict(qualified=False, reason='No adequate source.', facts=None, evidence=None, understanding=None)
        self.assertTrue(self.validator().is_valid(output))
        self.assertEqual(dict(qualified=False, reason=output['reason'], facts=None), self.bound(output))
        output['understanding'] = self.raw
        self.assertFalse(self.validator().is_valid(output))
        with self.assertRaisesRegex(ValueError, 'null understanding'):
            self.bound(output)

    def test_card_validation_refuses_nonclosed_proofs_documents_or_program_fields(self):
        changes = (
            lambda card: card['proof_map'].update(invented=copy.deepcopy(card['proof_map'][self.usage_id])),
            lambda card: card['proof_map'][self.usage_id].update(evidence_url='https://outside.example/doc'),
            lambda card: card.update(source_documents=[]),
            lambda card: card.update(reading_scope_issues=[{'claim': 'invented program audit'}]),
            lambda card: card.update(schema_version='a-model-version'),
        )
        for change in changes:
            card = self.bound()['facts']['understanding']
            change(card)
            with self.subTest(card=card), self.assertRaises(ValueError):
                validate_understanding(card)

    def test_structure_does_not_certify_semantic_condition_classification(self):
        # A real ID closes the reference; it cannot prove this interpretation.
        output = copy.deepcopy(self.output)
        output['understanding']['conditions'][0].update(kind='mandatory', text='GPU is mandatory.')
        card = self.bound(output)['facts']['understanding']
        self.assertEqual('mandatory', validate_understanding(card)['conditions'][0]['kind'])

    def test_unknown_condition_is_available_only_with_the_fresh_explicit_contract(self):
        contexts = [dict(url='https://example.com/game-help', text=(
            '# Game\nA browser game where players debate an AI judge.\n\n'
            '# Voice\nThe full voice experience requires an extra ~250MB payload.\n'))]
        passages = build_passages(contexts)
        game_id, voice_id = [passage['id'] for passage in passages]
        output = dict(qualified=True, reason='The described game can be introduced.',
            facts=dict(title='Debate game', category='游戏', summary='Debate an AI judge.',
                reason='A documented game concept.', audience='Players.',
                usage_conditions='The full voice experience needs an extra payload.',
                detail='A browser game.', retention_reason='A game design example.', open_source_status='unknown'),
            evidence=dict(category=[game_id], summary=[game_id], usage_conditions=[voice_id], license=[]),
            understanding=dict(purpose=dict(text='Debate an AI judge.', passage_ids=[game_id]),
                input=dict(text=None, passage_ids=[]), output=dict(text=None, passage_ids=[]), operations=[],
                conditions=[dict(subject='Voice payload', kind='unknown',
                    text='The source does not establish whether the basic game can skip the payload.',
                    passage_ids=[voice_id])], unknowns=['Whether basic play requires the voice payload.']))
        documents = source_documents(contexts)
        frozen = copy.deepcopy((output, passages, documents))
        legacy = source_review_schema(record={'kind': 'project'}, passages=passages,
                                     include_understanding=True)
        fresh = source_review_schema(record={'kind': 'project'}, passages=passages,
            include_understanding=True, shared_passage_ids=True, allow_unknown_conditions=True)
        self.assertFalse(Draft202012Validator(legacy).is_valid(output))
        self.assertTrue(Draft202012Validator(fresh).is_valid(output))
        with self.assertRaisesRegex(ValueError, 'condition kind'):
            bind_understanding_review(output, passages, {'kind': 'project'}, documents)
        card = bind_understanding_review(output, passages, {'kind': 'project'}, documents,
                                       allow_unknown_conditions=True)['facts']['understanding']
        self.assertEqual('unknown', card['conditions'][0]['kind'])
        self.assertEqual(passages[1]['quote'], card['proof_map'][voice_id]['quote'])
        self.assertEqual(card, validate_understanding(card, allow_unknown_conditions=True))
        with self.assertRaisesRegex(ValueError, 'condition kind'):
            validate_understanding(card)
        self.assertEqual(frozen, (output, passages, documents))

    def test_fresh_unknown_condition_still_requires_closed_original_source_ids(self):
        for ids in ([], ['invented'], [self.usage_id, self.usage_id]):
            output = copy.deepcopy(self.output)
            output['understanding']['conditions'][0].update(kind='unknown', passage_ids=ids)
            schema = source_review_schema(record=self.record, passages=self.passages,
                include_understanding=True, allow_unknown_conditions=True, shared_passage_ids=True)
            with self.subTest(ids=ids):
                self.assertFalse(Draft202012Validator(schema).is_valid(output))
                with self.assertRaises(ValueError):
                    bind_understanding_review(output, self.passages, self.record, self.documents,
                                              allow_unknown_conditions=True)

    def test_fresh_nonlicensing_binder_keeps_identity_claims_separate_from_license_terms(self):
        for status in ('confirmed', 'closed', 'unknown'):
            output = copy.deepcopy(self.output)
            output['facts'].update(category='MCP 服务与连接器', open_source_status=status)
            output['evidence']['open_source_status'] = [self.purpose_id] if status == 'confirmed' else []
            before = copy.deepcopy(output)
            bound = bind_understanding_review(output, self.passages, self.record, self.documents,
                                             nonlicensing=True)
            claims = bound['facts']['claims']
            self.assertFalse(any(claim['field'] == 'license' for claim in claims))
            identities = [claim for claim in claims if claim['field'] == 'open_source_status']
            self.assertEqual(int(status == 'confirmed'), len(identities))
            if identities:
                self.assertEqual(status, identities[0]['text'])
                self.assertEqual(self.passages[0]['quote'], identities[0]['quote'])
                self.assertEqual(self.url, identities[0]['evidence_url'])
            self.assertEqual(before, output)
            with self.assertRaisesRegex(ValueError, 'evidence fields'):
                self.bound(output)
        output = copy.deepcopy(self.output)
        output['facts']['open_source_status'] = 'confirmed'
        output['evidence'].update(license=[], open_source_status=[])
        with self.assertRaises(ValueError):
            bind_understanding_review(output, self.passages, self.record, self.documents,
                                      nonlicensing=True)
        output['evidence'].update(license=[self.usage_id], open_source_status=[self.purpose_id])
        with self.assertRaises(ValueError):
            bind_understanding_review(output, self.passages, self.record, self.documents,
                                      nonlicensing=True)

    def test_fresh_binding_options_do_not_treat_truthy_values_as_authorization(self):
        for invalid in (1, 'true', None):
            with self.subTest(value=invalid):
                with self.assertRaisesRegex(ValueError, 'boolean'):
                    bind_understanding_review(self.output, self.passages, self.record, self.documents,
                                              allow_unknown_conditions=invalid)
                with self.assertRaisesRegex(ValueError, 'boolean'):
                    bind_understanding_review(self.output, self.passages, self.record, self.documents,
                                              nonlicensing=invalid)

    def test_full_reading_claim_on_excerpt_reports_literal_issue_only(self):
        excerpt_scope = scope_for(self.contexts[0]['text'], coverage='excerpt', document_chars=500,
                                  ranges=[dict(start=50, end=50 + len(self.contexts[0]['text']))])
        documents = source_documents([dict(self.contexts[0], source_scope=excerpt_scope)])
        for claim in ('I read the full LICENSE.', '已读完整 LICENSE。', '已完整阅读全文。'):
            output = copy.deepcopy(self.output)
            output['understanding']['purpose']['text'] = claim
            card = self.bound(output, documents)['facts']['understanding']
            self.assertEqual(1, len(card['reading_scope_issues']))
            issue = card['reading_scope_issues'][0]
            self.assertEqual('reading_scope_overclaim', issue['code'])
            self.assertEqual('purpose', issue['field'])
            self.assertEqual(self.url, issue['evidence_url'])
            self.assertEqual(card, validate_understanding(card))
        for claim in ('I have not read the full LICENSE.', 'Read the full document before use.',
                      '需要完整阅读全文。', '未完整阅读全文。'):
            output = copy.deepcopy(self.output)
            output['understanding']['purpose']['text'] = claim
            self.assertEqual([], self.bound(output, documents)['facts']['understanding']['reading_scope_issues'])

    def test_known_complete_or_unknown_scope_is_not_excerpt_mismatch(self):
        output = copy.deepcopy(self.output)
        output['understanding']['purpose']['text'] = 'I read the full document.'
        for contexts in (self.contexts, [dict(self.contexts[0], source_scope=scope_for(self.contexts[0]['text']))]):
            card = self.bound(output, source_documents(contexts))['facts']['understanding']
            self.assertEqual([], card['reading_scope_issues'])

    def test_prose_license_assertion_uses_license_claim_urls(self):
        license_url = 'https://example.com/LICENSE'
        excerpt = 'GPLv3 License header only.'
        contexts = [dict(self.contexts[0], source_scope=scope_for(self.contexts[0]['text'])),
                    dict(url=license_url, text=excerpt,
                         source_scope=scope_for(excerpt, coverage='excerpt', document_chars=1000))]
        documents = source_documents(contexts)
        facts = dict(detail='根据 GPLv3 许可证全文确认授权条件。',
                     claims=[dict(field='summary', evidence_url=self.url, quote='Organizes local files.'),
                             dict(field='license', evidence_url=license_url, quote=excerpt)])
        before = copy.deepcopy((facts, documents))
        issues = prose_scope_issues(facts, documents)
        self.assertEqual(1, len(issues))
        self.assertEqual('detail', issues[0]['field'])
        self.assertEqual(license_url, issues[0]['evidence_url'])
        self.assertEqual(before, (facts, documents))
        # A complete unrelated README did not certify that the licence was read.
        self.assertEqual('complete_text', documents[0]['scope']['coverage'])
        facts['detail'] = 'LICENSE.txt 为 GPLv3 全文。'
        self.assertEqual(license_url, prose_scope_issues(facts, documents)[0]['evidence_url'])
        facts['claims'].append(dict(field='license', evidence_url=self.url, quote='README identifies GPLv3.'))
        facts['detail']='README 标注许可证，LICENSE 包含 GPL v3 全文。'
        self.assertEqual(license_url, prose_scope_issues(facts, documents)[0]['evidence_url'])

    def test_complete_license_evidence_is_not_blocked_by_other_excerpts(self):
        license_url = 'https://example.com/LICENSE'
        contexts = [dict(self.contexts[0], source_scope=scope_for(self.contexts[0]['text'],
                                                               coverage='excerpt', document_chars=500)),
                    dict(url=license_url, text='A complete license.', source_scope=scope_for('A complete license.'))]
        documents = source_documents(contexts)
        claims = [dict(field='summary', evidence_url=self.url, quote='Organizes files.'),
                  dict(field='license', evidence_url=license_url, quote='A complete license.')]
        facts = dict(detail='I read the full LICENSE.', claims=claims)
        self.assertEqual([], prose_scope_issues(facts, documents))
        excerpt_license_url = license_url + '?fragment'
        more = source_documents([dict(url=excerpt_license_url, text='License header.',
                                     source_scope=scope_for('License header.', coverage='excerpt', document_chars=500))])
        claims.append(dict(field='license', evidence_url=excerpt_license_url, quote='License header.'))
        self.assertEqual([], prose_scope_issues(facts, documents + more))

    def test_prose_audit_ignores_mere_mentions_instructions_negation_and_unknown_scope(self):
        claims = [dict(field='license', evidence_url=self.url, quote='A supplied source.')]
        excerpt = source_documents([dict(self.contexts[0], source_scope=scope_for(self.contexts[0]['text'],
                                                                              coverage='excerpt', document_chars=500))])
        for text in ('GPLv3许可证全文。', '使用前需要阅读GPLv3许可证全文。', '没有阅读完整许可证。',
                     'Read the full license before use.', 'I have not read the full license.'):
            with self.subTest(text=text):
                self.assertEqual([], prose_scope_issues(dict(detail=text, claims=claims), excerpt))
        self.assertEqual([], prose_scope_issues(dict(detail='I read the full license.', claims=claims), self.documents))

    def test_prose_audit_can_read_score_reasons_with_explicit_claim_mapping(self):
        documents = source_documents([dict(self.contexts[0], source_scope=scope_for(self.contexts[0]['text'],
                                                                                coverage='excerpt', document_chars=500))])
        claims = [dict(field='license', evidence_url=self.url, quote='A supplied source.')]
        scores = {'scores': {'usability': {'score': 9, 'reason': '根据 GPLv3 许可证全文确认条件。'}}}
        issues = prose_scope_issues(scores, documents, claims)
        self.assertEqual('scores.usability.reason', issues[0]['field'])


if __name__ == '__main__':
    unittest.main()
