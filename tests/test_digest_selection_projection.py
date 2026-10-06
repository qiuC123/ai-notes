"""Lossless, opt-in model projections; no network, models or database writes."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_notes import digest_selection as selection


class SourceReferenceProjectionTests(unittest.TestCase):
    def setUp(self):
        self.policy = selection.load_policy(Path(__file__).resolve().parents[1])
        # These fixtures freeze the pre-understanding projection contract.
        self.policy.pop("understanding_contract", None)
        self.policy.pop("introduction_contract", None)
        self.policy.pop('source_reading_contract', None)
        self.policy.pop('editorial_scope', None)
        self.policy.pop('selection_refinement_contract', None)
        self.url = "https://example.com/guide"
        self.other = "https://example.com/license"
        self.quote = "中文🙂 Windows 10 or later."
        self.text = "# Usage\r\n\r\n" + self.quote + "\r\nA different section.\n" + self.quote
        self.contexts = [
            {"url": self.url, "text": self.text, "fetched_at": "2026-10-04T12:00:00+08:00"},
            {"url": self.other, "text": "License text.\nRedistribution is permitted.", "fetched_at": "2026-10-04T12:00:01+08:00"},
        ]
        scope = dict(version=None, platform="Windows", host_architecture=None,
                     build_architecture=None, installation_path=None, requirement="required",
                     conditions=["10 or later"], evidence_kind="documentation")
        self.claims = [
            dict(field="usage_conditions", text="A Windows desktop route is documented.",
                 evidence_url=self.url, quote=self.quote, scope=scope),
            dict(field="summary", text="A desktop workflow with redistribution conditions.",
                 evidence_url=self.url, quote=self.quote),
            dict(field="summary", text="A desktop workflow with redistribution conditions.",
                 evidence_url=self.other, quote="Redistribution is permitted."),
        ]
        self.card = {
            "candidate_id": "fixture", "profile": "practical", "ranking_type": "weekly",
            "material": {"url": self.url, "title": "Local tool", "category": "开源项目",
                         "kind": "project", "summary": "A local workflow.", "reason": "Internal reason",
                         "source_urls": [self.url], "evidence_urls": [self.url, self.other],
                         "published_at": None, "change_note": "", "event": None},
            "evidence_context": self.contexts, "source_claims": self.claims,
            "input_hash": "frozen-raw-card", "eligibility": {"state": "available"},
        }
        self.prepared = {"policy": self.policy, "cards": [self.card], "ranking_type": "weekly"}

    def projected(self, prepared=None):
        return selection.build_scoring_input(prepared or self.prepared, "fixture")

    def test_originals_and_joint_support_round_trip_without_mutating_frozen_card(self):
        before = copy.deepcopy(self.prepared)
        result = self.projected()
        self.assertEqual("source-refs.v1", result["policy"]["scoring_projection"])
        contexts = result["card"]["evidence_context"]
        self.assertEqual([{"url": c["url"], "text": c["text"]} for c in self.contexts], contexts)
        restored = []
        for model_claim in result["card"]["source_claims"]:
            self.assertNotIn("quote", model_claim)
            raw = copy.deepcopy(model_claim)
            span = raw.pop("source_span")
            context = contexts[span["context_index"]]
            self.assertEqual(raw["evidence_url"], context["url"])
            raw["quote"] = context["text"][span["start"]:span["end"]]
            restored.append(raw)
        self.assertEqual(self.claims, restored)
        self.assertEqual(before, self.prepared)
        self.assertEqual(2, sum(c["field"] == "summary" for c in restored))
        # The copied scopes and source strings must not alias the frozen claims.
        result["card"]["source_claims"][0]["scope"]["conditions"].append("changed")
        self.assertEqual(before, self.prepared)

    def test_duplicate_quotes_and_url_contexts_have_deterministic_exact_spans(self):
        # validate_claims historically binds a URL to its last supplied context.
        last = "A later snapshot.\n" + self.text
        self.contexts.append({"url": self.url, "text": last, "fetched_at": "2026-10-04T12:01:00+08:00"})
        one, two = self.projected(), self.projected()
        self.assertEqual(one, two)
        span = one["card"]["source_claims"][0]["source_span"]
        self.assertEqual({"context_index": 2, "start": last.index(self.quote),
                          "end": last.index(self.quote) + len(self.quote)}, span)
        self.assertEqual(self.quote, last[span["start"]:span["end"]])
        self.assertEqual([self.text, self.contexts[1]["text"], last],
                         [c["text"] for c in one["card"]["evidence_context"]])

    def test_repeated_large_quotes_shrink_input_without_truncating_any_original(self):
        whole = "# Complete supplied source\n" + ("中文🙂 documented usage.\r\n" * 600)
        self.contexts[0]["text"] = whole
        self.card["source_claims"] = [dict(field=name, text="Source-backed " + name,
            evidence_url=self.url, quote=whole) for name in ("category", "summary", "usage_conditions", "license")]
        legacy = copy.deepcopy(self.prepared)
        legacy["policy"].pop("scoring_projection")
        old, new = self.projected(legacy), self.projected()
        encode = lambda data: json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.assertLess(len(encode(new)), len(encode(old)) * 0.4)
        self.assertEqual(old["card"]["evidence_context"], new["card"]["evidence_context"])
        self.assertTrue(all(c["source_span"] == {"context_index": 0, "start": 0, "end": len(whole)}
                            for c in new["card"]["source_claims"]))
        self.assertEqual(whole, self.card["source_claims"][0]["quote"])

    def test_absent_marker_keeps_exact_legacy_fields_values_and_serialized_fingerprint(self):
        legacy = copy.deepcopy(self.prepared)
        legacy["policy"].pop("scoring_projection")
        legacy["policy"].pop("reader_context")
        legacy["policy"].pop("editorial_position")
        # Frozen legacy projection contract, including the exact policy whitelist.
        policy_fields = ("version", "dimensions", "profiles", "category_profiles", "kind_profiles",
                         "flag_caps", "flag_kinds", "flag_basis", "usage_evidence_gaps", "assessment_contract")
        expected = {
            "card": {"ranking_type": "weekly", "profile": "practical",
                     "material": {k: v for k, v in self.card["material"].items() if k not in ("reason", "event")},
                     "evidence_context": [{"url": c["url"], "text": c["text"]} for c in self.contexts],
                     "evaluation_target": {"kind": "project", "unit": "whole_project"},
                     "source_claims": self.claims},
            "policy": {k: legacy["policy"][k] for k in policy_fields if k in legacy["policy"]},
        }
        for scoped in (True, False):
            with self.subTest(scoped=scoped):
                if not scoped:
                    legacy["policy"].pop("assessment_contract")
                    expected["policy"].pop("assessment_contract")
                    expected["card"].pop("evaluation_target")
                    expected["card"].pop("source_claims")
                result = self.projected(legacy)
                self.assertEqual(expected, result)
                self.assertEqual(selection._json(expected), selection._json(result))
                self.assertEqual(selection._hash(expected), selection._hash(result))

    def test_absent_editorial_position_keeps_v8_projection_and_serialized_hash(self):
        legacy = copy.deepcopy(self.prepared)
        legacy["policy"].pop("editorial_position")
        legacy["policy"]["version"] = "v8-reader-context-uncalibrated"
        # Preserve the v8 whitelist explicitly: the new contract cannot silently
        # enter a frozen v8 card through current configuration or candidate data.
        policy_fields = ("version", "dimensions", "profiles", "category_profiles", "kind_profiles",
                         "flag_caps", "flag_kinds", "flag_basis", "usage_evidence_gaps", "assessment_contract",
                         "scoring_projection", "reader_context")
        claims = copy.deepcopy(self.claims)
        for claim in claims:
            quote = claim.pop("quote")
            index = next(i for i,c in enumerate(self.contexts) if c["url"] == claim["evidence_url"])
            start = self.contexts[index]["text"].index(quote)
            claim["source_span"] = {"context_index":index, "start":start, "end":start + len(quote)}
        expected = {
            "card": {"ranking_type":"weekly", "profile":"practical",
                     "material":{k:v for k,v in self.card["material"].items() if k not in ("reason", "event")},
                     "evidence_context":[{"url":c["url"], "text":c["text"]} for c in self.contexts],
                     "evaluation_target":{"kind":"project", "unit":"whole_project"}, "source_claims":claims},
            "policy":{k:legacy["policy"][k] for k in policy_fields if k in legacy["policy"]},
        }
        legacy["cards"][0]["editorial_position"] = {"labels":["B"]}
        legacy["cards"][0]["material"]["editorial_position"] = legacy["cards"][0]["editorial_position"]
        actual = self.projected(legacy)
        self.assertEqual(expected, actual)
        self.assertEqual(selection._json(expected), selection._json(actual))
        self.assertEqual(selection._hash(expected), selection._hash(actual))

    def test_missing_quotes_or_unknown_contract_fail_without_fallback(self):
        for marker in (None, "source-refs.v2", {}):
            bad = copy.deepcopy(self.prepared)
            bad["policy"]["scoring_projection"] = marker
            with self.subTest(marker=marker), self.assertRaisesRegex(selection.SelectionError, "unsupported scoring projection"):
                self.projected(bad)
        for field, value in (("quote", "Unfound quotation"), ("evidence_url", "https://example.com/unread")):
            bad = copy.deepcopy(self.prepared)
            bad["cards"][0]["source_claims"][0][field] = value
            with self.subTest(field=field), self.assertRaises(selection.SelectionError):
                self.projected(bad)
        incompatible = copy.deepcopy(self.prepared)
        incompatible["policy"].pop("assessment_contract")
        with self.assertRaisesRegex(selection.SelectionError, "requires scoped-source"):
            self.projected(incompatible)

    def test_projection_does_not_change_score_arithmetic_or_output_quote_validation(self):
        legacy = copy.deepcopy(self.policy)
        legacy.pop("scoring_projection")
        assessment = {
            "precheck": {"status": "PASS", "reasons": ["Supported use."], "evidence_refs": [self.url]},
            "scores": {d: {"score": 8, "reason": "Documented use.", "evidence_refs": [self.url]}
                       for d in selection.DIMENSIONS},
            "flags": [{"code": "reader_mismatch", "reason": "The cited route has a reader requirement.",
                       "evidence_refs": [self.url], "basis": {"kind": "reader_requirement",
                       "claim": "The route is available on Windows.", "quote": self.quote, "evidence_url": self.url}}],
            "reason": "Review the documented route.",
        }
        old = selection.adapt_assessment(assessment, policy=legacy, card=self.card)
        new = selection.adapt_assessment(assessment, policy=self.policy, card=self.card)
        self.assertEqual("accepted", new["status"])
        self.assertEqual(old, new)
        self.assertEqual(selection._validate_assessment(legacy, self.card, assessment),
                         selection._validate_assessment(self.policy, self.card, assessment))
        basis = assessment["flags"][0]["basis"]
        basis.pop("quote")
        basis["source_span"] = self.projected()["card"]["source_claims"][0]["source_span"]
        self.assertEqual("rejected", selection.adapt_assessment(assessment, policy=self.policy, card=self.card)["status"])


class ProjectReadingProjectionTests(unittest.TestCase):
    projected = SourceReferenceProjectionTests.projected

    def setUp(self):
        SourceReferenceProjectionTests.setUp(self)
        from ai_notes.digest_passages import build_passages
        from ai_notes.digest_understanding import source_documents
        self.passages = build_passages(self.contexts)
        pid = self.passages[0]["id"]
        self.understanding = {
            "schema_version": "digest-understanding.v1",
            "purpose": {"text": "A documented desktop workflow.", "passage_ids": [pid]},
            "input": {"text": "Local files.", "passage_ids": [pid]},
            "output": {"text": "Organised files.", "passage_ids": [pid]},
            "operations": [{"input": "Local files", "action": "Organise", "output": "Organised files", "passage_ids": [pid]}],
            "conditions": [{"subject": "Desktop route", "kind": "compatibility", "text": "Windows 10 or later", "passage_ids": [pid]}],
            "unknowns": [],
            "proof_map": {row["id"]: {key: row[key] for key in ("evidence_url", "quote", "heading_path")} for row in self.passages[:1]},
            "source_documents": source_documents(self.contexts),
            "reading_scope_issues": [],
        }

    def enable(self):
        self.policy["understanding_contract"] = "project-reading.v1"
        self.card["understanding"] = copy.deepcopy(self.understanding)

    def test_opt_in_projects_reversible_understanding_without_repeating_source_text(self):
        self.enable()
        before = copy.deepcopy(self.prepared)
        projected = self.projected()["card"]
        self.assertEqual(self.card["understanding"]["source_documents"], projected["source_documents"])
        self.assertNotIn("source_documents", projected["understanding"])
        self.assertEqual(self.card["understanding"]["operations"], projected["understanding"]["operations"])
        for pid, proof in projected["understanding"]["proof_map"].items():
            span = proof["source_span"]
            context = projected["evidence_context"][span["context_index"]]
            self.assertEqual(self.card["understanding"]["proof_map"][pid]["quote"], context["text"][span["start"]:span["end"]])
            self.assertNotIn("quote", proof)
        projected["understanding"]["operations"][0]["passage_ids"].clear()
        projected["source_documents"][0]["scope"]["coverage"] = "changed"
        self.assertEqual(before, self.prepared)

    def test_absent_understanding_marker_preserves_exact_projection_and_ignores_injected_card(self):
        before = self.projected()
        self.card["understanding"] = {"invalid": "candidate data cannot enable the policy"}
        self.card["source_documents"] = ["untrusted injected metadata"]
        self.assertEqual(selection._json(before), selection._json(self.projected()))

    def test_understanding_span_retains_the_bound_occurrence_when_passage_text_repeats(self):
        from ai_notes.digest_passages import build_passages
        from ai_notes.digest_understanding import source_documents
        section = "# Usage\n" + self.quote + "\n\n"
        self.contexts[0]["text"] = section * 2
        passage = build_passages(self.contexts)[1]
        pid = passage["id"]
        for field in ('purpose', 'input', 'output'):
            self.understanding[field]['passage_ids'] = [pid]
        for field in ('operations', 'conditions'):
            self.understanding[field][0]['passage_ids'] = [pid]
        self.understanding['proof_map'] = {pid: {key: passage[key] for key in ('evidence_url', 'quote', 'heading_path')}}
        self.understanding['source_documents'] = source_documents(self.contexts)
        self.enable()
        proof = self.projected()['card']['understanding']['proof_map'][pid]
        self.assertEqual({'context_index': 0, 'start': len(section), 'end': len(section)*2}, proof['source_span'])

    def test_unknown_contract_missing_card_and_mismatched_passage_fail_closed(self):
        for marker in (None, {}, "project-reading.v2"):
            self.policy["understanding_contract"] = marker
            with self.subTest(marker=marker), self.assertRaisesRegex(selection.SelectionError, "unsupported understanding"):
                self.projected()
            with self.assertRaisesRegex(selection.SelectionError, "unsupported understanding"):
                selection.validate_policy(self.policy)
        self.enable()
        del self.card["understanding"]
        with self.assertRaises(ValueError):
            self.projected()
        self.enable()
        proof = next(iter(self.card["understanding"]["proof_map"].values()))
        proof["quote"] = "Not in any supplied passage."
        with self.assertRaises(ValueError):
            self.projected()

    def test_prompt_opt_in_keeps_legacy_bytes_and_frozen_snapshot_unchanged(self):
        root = Path(__file__).resolve().parents[1]
        file_text = (root / selection.PROMPT_PATH).read_text(encoding="utf-8")
        file_text = file_text.partition(selection.INTRODUCTION_PROMPT_MARKER)[0]
        legacy = file_text.partition(selection.UNDERSTANDING_PROMPT_MARKER)[0]
        self.assertEqual(legacy, selection.load_prompt(root, self.policy))
        self.enable()
        self.assertEqual(file_text, selection.load_prompt(root, self.policy))
        frozen = dict(policy=self.policy, prompt_text=legacy,
                      prompt_hash=__import__('hashlib').sha256(legacy.encode('utf-8')).hexdigest())
        self.assertEqual(legacy, selection.get_prompt(root, frozen))

    def test_prepare_freezes_card_and_exact_ranges_without_aliasing_inputs(self):
        from ai_notes.digest_understanding import source_documents
        # The offsets describe the fetched document, not a claim of reading all of it.
        self.contexts[0]["source_scope"] = dict(schema_version="digest-source-scope.v1", coverage="excerpt",
            document_chars=10000, supplied_chars=len(self.text), ranges=[dict(start=20,end=20 + len(self.text))],
            document_sha256="a" * 64, reader="fixture", commit_sha=None, file_path=None)
        self.understanding["source_documents"] = source_documents(self.contexts)
        self.enable()
        record = dict(self.card["material"], canonical_url=self.url, evidence_status="verified")
        cid = selection.candidate_id(record)
        inputs = {cid: self.understanding}
        before = copy.deepcopy(inputs)
        with tempfile.TemporaryDirectory() as temp, patch.object(selection, "_query", return_value={"candidates": [record]}):
            prepared = selection.prepare(Path(temp), "weekly", "2026-09-28", evidence_context=self.contexts,
                policy_snapshot=self.policy, prompt_snapshot="Frozen fixture prompt", source_understanding=inputs)
            self.assertEqual(before[cid], prepared["cards"][0]["understanding"])
            inputs[cid]["purpose"]["text"] = "Caller mutation"
            saved = selection.load_preparation(Path(temp), prepared["prepare_id"])
            self.assertEqual(before[cid], saved["cards"][0]["understanding"])
            projected = selection.build_scoring_input(saved, cid)
            self.assertEqual([dict(start=20,end=20 + len(self.text))], projected["card"]["source_documents"][0]["scope"]["ranges"])
            self.assertNotIn("source_scope", projected["card"]["evidence_context"][0])
            with self.assertRaisesRegex(selection.SelectionError, "unknown candidate"):
                selection.prepare(Path(temp), "weekly", "2026-09-28", evidence_context=self.contexts,
                    policy_snapshot=self.policy, prompt_snapshot="Frozen fixture prompt", source_understanding={**before, "bad": before[cid]})

    def test_candidate_contexts_isolate_shared_event_sources_and_preserve_each_source_order(self):
        from ai_notes.digest_passages import build_passages
        from ai_notes.digest_understanding import source_documents
        self.enable()
        shared = 'https://example.com/releases/v2'
        event = dict(url=shared, type='release', occurred_at='2026-10-01T12:00:00+08:00')
        news_contexts = [dict(self.contexts[0], url=shared, text='An announcement describes the v2 event.'),
                         dict(self.contexts[1], url='https://example.com/announcement-details')]
        update_contexts = [dict(self.contexts[0], text='The project manual has additional installation conditions.'),
                           dict(self.contexts[0], url=shared, text='The v2 release body as captured for the project update.')]
        union_urls = [row['url'] for row in news_contexts + update_contexts]
        records = [dict(self.card['material'], kind='news', url=shared, canonical_url=shared, event=event,
                        evidence_status='verified', evidence_urls=union_urls),
                   dict(self.card['material'], kind='update', canonical_url=self.url, event=event,
                        evidence_status='verified', evidence_urls=union_urls)]
        ids = [selection.candidate_id(record) for record in records]
        by_id = dict(zip(ids, (news_contexts, update_contexts)))
        understood = {}
        for cid, contexts in by_id.items():
            first = build_passages(contexts)[0]
            card = copy.deepcopy(self.understanding)
            for field in ('purpose', 'input', 'output'):
                card[field]['passage_ids'] = [first['id']]
            for field in ('operations', 'conditions'):
                card[field][0]['passage_ids'] = [first['id']]
            card['proof_map'] = {first['id']: {key: first[key] for key in ('evidence_url', 'quote', 'heading_path')}}
            card['source_documents'] = source_documents(contexts)
            understood[cid] = card
        original = copy.deepcopy(by_id)
        with tempfile.TemporaryDirectory() as temp, patch.object(selection, '_query', return_value={'candidates': records}):
            prepared = selection.prepare(Path(temp), 'weekly', '2026-09-28',
                evidence_context=news_contexts + update_contexts, candidate_contexts=by_id,
                policy_snapshot=self.policy, prompt_snapshot='Frozen fixture prompt', source_understanding=understood)
            for cid in ids:
                card = next(row for row in prepared['cards'] if row['candidate_id'] == cid)
                self.assertEqual(original[cid], card['evidence_context'])
                self.assertEqual(source_documents(original[cid]), card['understanding']['source_documents'])
                self.assertEqual([dict(url=row['url'], text=row['text']) for row in original[cid]],
                                 selection.build_scoring_input(prepared, cid)['card']['evidence_context'])
            by_id[ids[0]][0]['text'] = 'Caller mutation must not affect frozen context.'
            loaded = selection.load_preparation(Path(temp), prepared['prepare_id'])
            self.assertEqual(original[ids[0]], loaded['cards'][0]['evidence_context'])

    def test_candidate_contexts_requires_exact_keys_valid_sources_and_new_contract(self):
        self.enable()
        record = dict(self.card['material'], canonical_url=self.url, evidence_status='verified')
        cid = selection.candidate_id(record)
        with tempfile.TemporaryDirectory() as temp, patch.object(selection, '_query', return_value={'candidates': [record]}):
            def prepare(mapping, policy=None):
                return selection.prepare(Path(temp), 'weekly', '2026-09-28', candidate_contexts=mapping,
                    policy_snapshot=policy or self.policy, prompt_snapshot='Frozen fixture prompt',
                    source_understanding={cid: self.understanding})
            for mapping in ([], {None: self.contexts}, {}, {'unknown': self.contexts},
                            {cid: self.contexts, 'unknown': self.contexts}, {cid: 'not a list'},
                            {cid: [dict(self.contexts[0], unexpected='not allowed')]},
                            {cid: [dict(self.contexts[0], fetched_at='2999-01-01T00:00:00+08:00')]},
                            {cid: [dict(self.contexts[0], source_scope={'coverage': 'complete_text'})]}):
                with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                    prepare(mapping)
            legacy = dict(self.policy)
            legacy.pop('understanding_contract')
            with self.assertRaisesRegex(selection.SelectionError, 'requires project-reading'):
                prepare({cid: self.contexts}, legacy)

    def test_candidate_contexts_does_not_accept_changed_scope_at_the_same_url(self):
        self.enable()
        record = dict(self.card['material'], canonical_url=self.url, evidence_status='verified')
        cid = selection.candidate_id(record)
        changed = copy.deepcopy(self.contexts)
        changed[0]['source_scope'] = dict(schema_version='digest-source-scope.v1', coverage='excerpt',
            document_chars=10000, supplied_chars=len(self.text), ranges=[dict(start=25, end=25 + len(self.text))],
            document_sha256='a' * 64, reader='fixture', commit_sha=None, file_path=None)
        with tempfile.TemporaryDirectory() as temp, patch.object(selection, '_query', return_value={'candidates': [record]}):
            with self.assertRaisesRegex(selection.SelectionError, 'source documents differ'):
                selection.prepare(Path(temp), 'weekly', '2026-09-28', candidate_contexts={cid: changed},
                    policy_snapshot=self.policy, prompt_snapshot='Frozen fixture prompt',
                    source_understanding={cid: self.understanding})


if __name__ == "__main__":
    unittest.main()
