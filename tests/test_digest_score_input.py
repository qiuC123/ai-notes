"""Opt-in score guidance and lossless bindings, without semantic certification."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator

from ai_notes import digest_editorial as editorial, digest_runtime as runtime, digest_selection as selection
from ai_notes.digest_output_schema import assessment_schema
from ai_notes.digest_passages import build_passages
from ai_notes.digest_understanding import source_documents
from tests import test_digest_pipeline as pipeline_fixtures


ROOT = Path(__file__).resolve().parents[1]
MARKER = "compact-schema.v1"


class ScoreInputTests(unittest.TestCase):
    def setUp(self):
        self.policy = selection.load_policy(ROOT)
        self.policy.pop("license_review_scope", None)  # Pin this v14 fixture's earlier review scope.
        self.policy["score_input_contract"] = MARKER
        self.readme = "https://example.com/README.md"
        self.install = "https://example.com/install.md"
        self.license = "https://example.com/LICENSE"
        self.metadata = "https://example.com/api/metadata"
        self.contexts = [
            {"url": self.readme,
             "text": "# Notes\n\nOrganise local notes.\n\n# Optional features\n\nSync is optional. A clipper supports Firefox and Chrome.\n"},
            {"url": self.install, "text": "# Desktop\n\nWindows installer is available.\n"},
            {"url": self.license, "text": "Redistribution is permitted.\n"},
            {"url": self.metadata, "text": "Current author-supplied repository description.\n"},
        ]
        self.claims = [
            {"field": "summary", "text": "Organise local notes.",
             "evidence_url": self.readme, "quote": "Organise local notes."},
            {"field": "usage_conditions", "text": "Windows installer is available.",
             "evidence_url": self.install, "quote": "Windows installer is available.",
             "scope": {"version": None, "platform": "Windows", "host_architecture": None,
                       "build_architecture": None, "installation_path": "installer",
                       "requirement": "optional", "conditions": [], "evidence_kind": "documentation"}},
            {"field": "summary", "text": "A clipper supports Firefox and Chrome.",
             "evidence_url": self.readme, "quote": "A clipper supports Firefox and Chrome."},
            {"field": "license", "text": "Redistribution is permitted.",
             "evidence_url": self.license, "quote": "Redistribution is permitted."},
        ]
        passages = build_passages(self.contexts)
        purpose = next(p for p in passages if p["evidence_url"] == self.readme and "Organise" in p["quote"])
        output = next(p for p in passages if p["evidence_url"] == self.readme and "clipper" in p["quote"])
        condition = next(p for p in passages if p["evidence_url"] == self.install)
        proofs = {p["id"]: {key: p[key] for key in ("evidence_url", "quote", "heading_path")}
                  for p in (purpose, output, condition)}
        understanding = {
            "schema_version": "digest-understanding.v1",
            "purpose": {"text": "Organise local notes.", "passage_ids": [purpose["id"]]},
            "input": {"text": None, "passage_ids": []},
            "output": {"text": "Clipped notes.", "passage_ids": [output["id"]]},
            "operations": [],
            "conditions": [{"subject": "Desktop installer", "kind": "compatibility",
                            "text": "Windows", "passage_ids": [condition["id"]]}],
            "unknowns": ["Input details are not described in these excerpts."],
            "proof_map": proofs, "source_documents": source_documents(self.contexts),
            "reading_scope_issues": [],
        }
        self.card = {
            "candidate_id": "fixture", "profile": "practical", "ranking_type": "weekly",
            "material": {"url": self.readme, "title": "Notes", "category": "开源项目",
                         "kind": "project", "summary": "Organise local notes.",
                         "source_urls": [self.readme], "evidence_urls": [c["url"] for c in self.contexts],
                         "published_at": None, "change_note": "", "supported_systems": "Windows",
                         "reason": "Private source-review explanation.", "event": None},
            "evidence_context": self.contexts, "source_claims": self.claims,
            "understanding": understanding, "input_hash": "frozen-original-card",
            "eligibility": {"state": "available"},
        }
        self.prepared = {"policy": self.policy, "cards": [self.card], "ranking_type": "weekly"}

    def projected(self, prepared=None):
        return selection.build_scoring_input(prepared or self.prepared, "fixture")

    def old_preparation(self):
        old = copy.deepcopy(self.prepared)
        old["policy"].pop("score_input_contract")
        old["policy"]["version"] = "v13-evidence-focus-uncalibrated"
        return old

    def assessment(self):
        return {
            "precheck": {"status": "PASS", "reasons": ["A documented local note workflow."],
                         "evidence_refs": [self.readme]},
            "scores": {d: {"score": 6, "reason": "A documented note workflow.",
                            "evidence_refs": [self.readme]} for d in selection.DIMENSIONS},
            "flags": [], "reason": "Consider the documented workflow.",
        }

    def public_review(self, assessment=None, **kwargs):
        return editorial.build_review_input(
            record=self.card["material"], facts=self.card["material"],
            assessment=assessment or self.assessment(), contexts=self.contexts,
            ranking_type="weekly", review_scope="public-introduction.v1",
            introduction_contract="discovery.v1", understanding_contract="project-reading.v1",
            selection_refinement_contract="evidence-focus.v1", **kwargs)

    def test_marker_is_closed_and_omission_keeps_old_contract(self):
        self.assertEqual(MARKER, selection.SCORE_INPUT_CONTRACT)
        self.assertEqual(self.policy, selection.validate_policy(self.policy))
        old = self.old_preparation()
        self.assertEqual(old["policy"], selection.validate_policy(old["policy"]))
        for marker in (None, {}, "", "compact-schema.v2"):
            bad = copy.deepcopy(self.prepared)
            bad["policy"]["score_input_contract"] = marker
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(selection.SelectionError, "unsupported score input"):
                    selection.validate_policy(bad["policy"])
                with self.assertRaisesRegex(selection.SelectionError, "unsupported score input"):
                    self.projected(bad)
                with self.assertRaisesRegex(selection.SelectionError, "unsupported score input"):
                    selection.load_prompt(ROOT, bad["policy"])

    def test_new_marker_requires_explicit_modern_dependencies_without_upgrading_old_policy(self):
        dependencies = ("assessment_contract", "scoring_projection", "selection_refinement_contract",
                        "editorial_review_contract", "understanding_contract", "introduction_contract",
                        "source_reading_contract", "editorial_scope")
        for field in dependencies:
            bad = copy.deepcopy(self.prepared)
            bad["policy"].pop(field)
            with self.subTest(missing=field):
                with self.assertRaises(selection.SelectionError):
                    selection.validate_policy(bad["policy"])
                with self.assertRaises(selection.SelectionError):
                    self.projected(bad)
                with self.assertRaises(selection.SelectionError):
                    selection.load_prompt(ROOT, bad["policy"])
        # Older contracts may legitimately omit either optional projection or
        # refinement. A new dependency cannot silently become their requirement.
        for field in ("scoring_projection", "selection_refinement_contract"):
            old = self.old_preparation()
            old["policy"].pop(field)
            with self.subTest(legacy_missing=field):
                self.assertEqual(old["policy"], selection.validate_policy(old["policy"]))
                projected = self.projected(old)
                self.assertNotIn("output_guidance", projected)
                self.assertNotIn("source_navigation", projected)
                self.assertNotIn(selection.SCORE_INPUT_PROMPT_MARKER, selection.load_prompt(ROOT, old["policy"]))

    def test_new_prompt_suffix_is_explicit_and_missing_supplement_fails(self):
        text = (ROOT / selection.PROMPT_PATH).read_text(encoding="utf-8")
        prefix, marker, suffix = text.partition(selection.SCORE_INPUT_PROMPT_MARKER)
        self.assertTrue(marker)
        old = self.old_preparation()
        self.assertEqual(prefix, selection.load_prompt(ROOT, old["policy"]))
        self.assertEqual(prefix + marker + suffix, selection.load_prompt(ROOT, self.policy))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / selection.PROMPT_PATH
            path.parent.mkdir(parents=True)
            path.write_text(prefix, encoding="utf-8")
            self.assertEqual(prefix, selection.load_prompt(root, old["policy"]))
            with self.assertRaisesRegex(selection.SelectionError, "supplement is unavailable"):
                selection.load_prompt(root, self.policy)

    def test_guidance_has_legal_shapes_but_is_not_evidence_or_scores(self):
        before = copy.deepcopy(self.prepared)
        projected = self.projected()
        guidance = projected["output_guidance"]
        schema = assessment_schema(policy=self.policy, card=self.card)
        self.assertTrue(guidance["structure_only_not_candidate_evidence"])
        self.assertEqual(list(selection.ASSESSMENT_FIELDS), guidance["required_fields"]["top_level"])
        self.assertEqual(["status", "reasons", "evidence_refs"], guidance["required_fields"]["precheck"])
        self.assertEqual(list(selection.DIMENSIONS), guidance["required_fields"]["scores"])
        self.assertEqual(["score", "reason", "evidence_refs"], guidance["required_fields"]["each_score"])
        self.assertTrue(Draft202012Validator(schema).is_valid(guidance["pass_shape_example"]))
        self.assertTrue(Draft202012Validator(schema).is_valid(projected["output_example"]["unknown_example"]))
        self.assertEqual(set(selection.ASSESSMENT_FIELDS), set(guidance["pass_shape_example"]))
        self.assertIn("reason_note", guidance["forbidden_extra_fields"])
        self.assertEqual(before, self.prepared)
        # The example is a shape demonstration, not a source claim to ingest.
        self.assertNotIn("pass_shape_example", projected["card"])
        self.assertEqual(0, guidance["pass_shape_example"]["scores"]["value"]["score"])

    def test_navigation_keeps_each_urls_exact_claim_indexes_and_proof_ids(self):
        before = copy.deepcopy(self.prepared)
        projected = self.projected()
        proofs = projected["card"]["understanding"]["proof_map"]
        expected_indexes = [[0, 2], [1], [3], []]
        for index, navigation in enumerate(projected["source_navigation"]):
            url = self.contexts[index]["url"]
            self.assertEqual({"url", "source_claim_indexes", "understanding_passage_ids"}, set(navigation))
            self.assertEqual(url, navigation["url"])
            self.assertEqual(expected_indexes[index], navigation["source_claim_indexes"])
            self.assertEqual([pid for pid, proof in proofs.items() if proof["evidence_url"] == url],
                             navigation["understanding_passage_ids"])
            for pid in navigation["understanding_passage_ids"]:
                span = proofs[pid]["source_span"]
                context = projected["card"]["evidence_context"][span["context_index"]]
                self.assertEqual(url, context["url"])
                original = self.card["understanding"]["proof_map"][pid]["quote"]
                self.assertEqual(original, context["text"][span["start"]:span["end"]])
        self.assertEqual(before, self.prepared)
        self.assertEqual([{"url": c["url"], "text": c["text"]} for c in self.contexts],
                         projected["card"]["evidence_context"])
        projected["source_navigation"][0]["source_claim_indexes"].clear()
        self.assertEqual(before, self.prepared)

    def test_absent_marker_preserves_explicit_v13_projection_and_fingerprint(self):
        old = self.old_preparation()
        claims = copy.deepcopy(self.claims)
        for claim in claims:
            quote = claim.pop("quote")
            index = next(i for i, c in enumerate(self.contexts) if c["url"] == claim["evidence_url"])
            start = self.contexts[index]["text"].index(quote)
            claim["source_span"] = {"context_index": index, "start": start, "end": start + len(quote)}
        understanding = copy.deepcopy(self.card["understanding"])
        understanding.pop("source_documents")
        for pid, proof in understanding["proof_map"].items():
            quote = proof.pop("quote")
            index = next(i for i, c in enumerate(self.contexts) if c["url"] == proof["evidence_url"])
            start = self.contexts[index]["text"].index(quote)
            proof["source_span"] = {"context_index": index, "start": start, "end": start + len(quote)}
        policy_fields = ("version", "dimensions", "profiles", "category_profiles", "kind_profiles",
                         "flag_caps", "flag_kinds", "flag_basis", "usage_evidence_gaps", "assessment_contract",
                         "scoring_projection", "reader_context", "editorial_position", "understanding_contract",
                         "introduction_contract", "source_reading_contract", "editorial_scope",
                         "selection_refinement_contract")
        expected = {
            "card": {"ranking_type": "weekly", "profile": "practical",
                     "material": {k: v for k, v in self.card["material"].items() if k not in ("reason", "event")},
                     "evidence_context": [{"url": c["url"], "text": c["text"]} for c in self.contexts],
                     "evaluation_target": {"kind": "project", "unit": "whole_project"},
                     "source_claims": claims, "understanding": understanding,
                     "source_documents": self.card["understanding"]["source_documents"]},
            "policy": {k: old["policy"][k] for k in policy_fields if k in old["policy"]},
            "output_example": {
                "purpose": "Output structure only; this is not a judgment or evidence about the candidate. PASS requires all five score dimensions with their own source-supported reasons and evidence_refs.",
                "unknown_example": {
                    "precheck": {"status": "UNKNOWN", "reasons": ["结构示例：此处应写本候选实际缺少的原文依据。"], "evidence_refs": []},
                    "scores": None, "flags": [], "reason": "结构示例，不是对本候选的判断。"}},
        }
        # Candidate/source data cannot opt in to a policy contract.
        old["cards"][0]["material"]["score_input_contract"] = MARKER
        old["cards"][0]["score_input_contract"] = MARKER
        self.assertEqual(expected, self.projected(old))
        self.assertEqual(selection._json(expected), selection._json(self.projected(old)))
        self.assertEqual(selection._hash(expected), selection._hash(self.projected(old)))
        self.assertNotIn("$defs", assessment_schema(policy=old["policy"], card=self.card))
        projected = self.projected()
        for key in ("output_guidance", "source_navigation"):
            projected.pop(key)
        projected["policy"].pop("score_input_contract")
        projected["policy"]["version"] = old["policy"]["version"]
        self.assertEqual(expected, projected)

    def test_unknown_extra_null_field_still_rejected_without_mutation(self):
        raw = self.assessment()
        raw["reason_note"] = None
        before = copy.deepcopy(raw)
        schema = assessment_schema(policy=self.policy, card=self.card)
        self.assertFalse(Draft202012Validator(schema).is_valid(raw))
        result = selection.adapt_assessment(raw, policy=self.policy, card=self.card)
        self.assertEqual("rejected", result["status"])
        self.assertIsNone(result["assessment"])
        self.assertEqual([], result["transformations"])
        self.assertEqual(before, result["raw_output"])
        self.assertEqual(before, raw)

    def test_legal_url_is_not_semantic_support_and_binder_does_not_repair_refs(self):
        raw = self.assessment()
        raw["scores"]["usability"] = {"score": 6, "reason": "The clipper supports Firefox and Chrome.",
                                       "evidence_refs": [self.install]}
        before = copy.deepcopy(raw)
        self.assertNotIn("Firefox", self.contexts[1]["text"])
        self.assertIn("Firefox", self.contexts[0]["text"])
        # This proves structural acceptance only. Semantic review must still
        # reject the unsupported reason; neither schema nor binder does so.
        self.assertTrue(Draft202012Validator(assessment_schema(policy=self.policy, card=self.card)).is_valid(raw))
        result = selection.adapt_assessment(raw, policy=self.policy, card=self.card)
        self.assertEqual("accepted", result["status"])
        self.assertEqual(before, result["assessment"])
        self.assertEqual([], result["transformations"])
        self.assertEqual([self.install], result["assessment"]["scores"]["usability"]["evidence_refs"])
        self.assertEqual(before, raw)

    def test_frozen_v13_prompt_hash_restores_without_current_policy_or_file(self):
        old = self.old_preparation()
        text = selection.load_prompt(ROOT, old["policy"])
        old.update(prompt_text=text, prompt_hash=hashlib.sha256(text.encode("utf-8")).hexdigest())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Persisted text wins even if a new file/policy would now differ.
            prompt_path = root / selection.PROMPT_PATH
            prompt_path.parent.mkdir(parents=True)
            prompt_path.write_text("A later unrelated prompt.", encoding="utf-8")
            self.assertEqual(text, selection.get_prompt(root, old))
            old["prompt_text"] += " altered"
            with self.assertRaisesRegex(selection.SelectionError, "hash does not match"):
                selection.get_prompt(root, old)

    def test_absent_editorial_marker_keeps_exact_v13_public_projection(self):
        assessment = self.assessment()
        expected = {
            "review_scope": "public-introduction.v1", "introduction_contract": "discovery.v1",
            "ranking_type": "weekly", "featured": False,
            "candidate": {"url": self.readme, "kind": "project"},
            "public_fields": {"title": "Notes", "category": "开源项目", "summary": "Organise local notes.",
                              "url": self.readme, "supported_systems": "Windows"},
            "selection_basis": assessment,
            "passages": build_passages(self.contexts), "source_documents": source_documents(self.contexts),
            "evidence_scope": "supplied_text_only_not_full_document_or_software_test",
            "understanding_contract": "project-reading.v1", "selection_refinement_contract": "evidence-focus.v1",
        }
        old = self.public_review(assessment)
        self.assertEqual(expected, old)
        self.assertEqual(selection._json(expected), selection._json(old))
        self.assertEqual(selection._hash(expected), selection._hash(old))
        self.card["material"]["score_input_contract"] = MARKER
        self.assertEqual(expected, self.public_review(assessment))
        new = self.public_review(assessment, score_input_contract=MARKER)
        self.assertEqual(MARKER, new.pop("score_input_contract"))
        new.pop("reason_source_navigation")
        self.assertEqual(expected, new)
        self.assertEqual(editorial.review_schema(old["passages"]), editorial.review_schema(new["passages"]))

    def test_editorial_navigation_does_not_repair_a_reason_citing_wrong_document(self):
        assessment = self.assessment()
        assessment["scores"]["usability"] = {
            "score": 6, "reason": "The clipper supports Firefox and Chrome.", "evidence_refs": [self.install]}
        assessment["flags"] = [{
            "code": "unclear_usage", "reason": "A route condition requires review.",
            "evidence_refs": [self.install, self.readme],
            "basis": {"kind": "usage_path_gap", "claim": "The route needs review.",
                      "quote": "Windows installer is available.", "evidence_url": self.install}}]
        before = copy.deepcopy((assessment, self.contexts))
        result = self.public_review(assessment, score_input_contract=MARKER)
        nav = {entry["field"]: entry["sources"] for entry in result["reason_source_navigation"]}
        expected_fields = {"selection_basis.precheck.reasons", "selection_basis.flags.0.reason"}
        expected_fields.update("selection_basis.scores." + dimension + ".reason" for dimension in selection.DIMENSIONS)
        self.assertEqual(expected_fields, set(nav))
        install_ids = [p["id"] for p in result["passages"] if p["evidence_url"] == self.install]
        readme_ids = [p["id"] for p in result["passages"] if p["evidence_url"] == self.readme]
        self.assertEqual([{"url": self.install, "passage_ids": install_ids}],
                         nav["selection_basis.scores.usability.reason"])
        self.assertEqual([{"url": self.readme, "passage_ids": readme_ids}],
                         nav["selection_basis.precheck.reasons"])
        self.assertEqual([{"url": self.install, "passage_ids": install_ids},
                          {"url": self.readme, "passage_ids": readme_ids}],
                         nav["selection_basis.flags.0.reason"])
        self.assertTrue(set(install_ids).isdisjoint(readme_ids))
        self.assertEqual(assessment, result["selection_basis"])
        self.assertEqual(before, (assessment, self.contexts))
        # Whole-packet facts are available for conflict checks, without being
        # added to the usability reason's cited set or deemed semantic proof.
        self.assertTrue(any("Firefox" in p["quote"] for p in result["passages"]))
        result["reason_source_navigation"][0]["sources"][0]["passage_ids"].clear()
        self.assertEqual(before, (assessment, self.contexts))

    def test_editorial_navigation_marker_is_closed_and_requires_public_scope(self):
        for marker in ("", "compact-schema.v2", {}, 1):
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, "unsupported score input"):
                self.public_review(score_input_contract=marker)
        with self.assertRaisesRegex(ValueError, "requires public introduction"):
            editorial.build_review_input(record=self.card["material"], facts=self.card["material"],
                assessment=self.assessment(), contexts=self.contexts, ranking_type="weekly",
                score_input_contract=MARKER)
        with self.assertRaisesRegex(ValueError, "evidence-focus"):
            editorial.build_review_input(record=self.card["material"], facts=self.card["material"],
                assessment=self.assessment(), contexts=self.contexts, ranking_type="weekly",
                review_scope="public-introduction.v1", introduction_contract="discovery.v1",
                understanding_contract="project-reading.v1", score_input_contract=MARKER)
        old = editorial.build_review_input(record=self.card["material"], facts=self.card["material"],
            assessment=self.assessment(), contexts=self.contexts, ranking_type="weekly",
            review_scope="public-introduction.v1", introduction_contract="discovery.v1",
            understanding_contract="project-reading.v1")
        self.assertNotIn("score_input_contract", old)
        self.assertNotIn("reason_source_navigation", old)

    def test_canonical_policy_pipeline_freezes_new_score_and_review_stages(self):
        fixture = pipeline_fixtures.PipelineTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        (fixture.root / selection.POLICY_PATH).write_text(
            json.dumps(selection.load_policy(ROOT), ensure_ascii=False), encoding="utf-8")
        job, result = fixture.run_job()
        self.assertEqual("completed", result["status"], result)
        self.assertEqual(MARKER, job["checkpoints"]["screen"]["policy_snapshot"]["score_input_contract"])
        self.assertEqual(6, len(fixture.score_inputs))
        self.assertEqual(6, len(fixture.editorial_inputs))
        scores_by_url = {item["card"]["material"]["url"]: item for item in fixture.score_inputs}
        for reviewed in fixture.editorial_inputs:
            scored = scores_by_url[reviewed["candidate"]["url"]]
            self.assertEqual(MARKER, scored["policy"]["score_input_contract"])
            self.assertIn("output_guidance", scored)
            self.assertEqual(fixture.text, "".join(c["text"] for c in scored["card"]["evidence_context"]))
            self.assertEqual(MARKER, reviewed["score_input_contract"])
            self.assertEqual(fixture.text, "".join(p["quote"] for p in reviewed["passages"]))
            self.assertTrue(scored["source_navigation"])
            self.assertTrue(reviewed["reason_source_navigation"])
            cited = {entry["url"] for entry in scored["source_navigation"]}
            self.assertEqual({c["url"] for c in scored["card"]["evidence_context"]}, cited)
            self.assertTrue(all(source["url"] in cited for entry in reviewed["reason_source_navigation"]
                                for source in entry["sources"]))
        with runtime._db(fixture.root, write=False) as connection:
            stages = {row["stage"] for row in connection.execute("SELECT stage FROM requests")}
        self.assertEqual({"screen-v13-evidence-focus", "verify-facts-v13-evidence-focus",
                          "value-score-v14-compact-schema", "editorial-v14-reason-navigation"}, stages)

    def test_score_and_review_navigation_are_stable_after_sorted_json_recovery(self):
        # Persisted dictionaries are key-sorted. Their insertion order must not
        # become a different navigation array or create another paid request.
        prepared = copy.deepcopy(self.prepared)
        proofs = prepared['cards'][0]['understanding']['proof_map']
        prepared['cards'][0]['understanding']['proof_map'] = dict(reversed(list(proofs.items())))
        before = selection.build_scoring_input(prepared, 'fixture')
        recovered = selection.build_scoring_input(json.loads(runtime._json(prepared)), 'fixture')
        self.assertEqual(runtime._json(before), runtime._json(recovered))
        assessment = self.assessment()
        fresh = self.public_review(assessment, score_input_contract=MARKER)
        restored = self.public_review(json.loads(runtime._json(assessment)), score_input_contract=MARKER)
        self.assertEqual(runtime._json(fresh), runtime._json(restored))


if __name__ == "__main__":
    unittest.main()
