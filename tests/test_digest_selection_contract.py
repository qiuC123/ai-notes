"""Offline contract checks; no model calls and no writes to production data."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import unittest

from ai_notes import digest_selection as selection


class AssessmentContractTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.policy = selection.load_policy(self.root)
        self.url = "https://example.com/source"
        self.source = ("Acme announced reusable instructions and a migration for saved instructions. "
                       "Acme claims tasks are twice as fast, without offering measurements. "
                       "This patch fixes one typo.")
        self.card = {
            "candidate_id": "fixture", "canonical_url": self.url, "profile": "news", "ranking_type": "daily",
            "material": {"url": self.url, "title": "Acme instructions update", "category": "AI 应用", "kind": "news",
                         "summary": "A reusable-instructions announcement.", "evidence_status": "discovered",
                         "verification_level": "documented", "verified_at": None, "reason": "Ledger discovery reason",
                         "event": {"id": "ledger-event-id", "url": self.url, "occurred_at": "2026-10-01T12:00:00+08:00", "type": "news"}},
            "evidence_context": [{"url": self.url, "text": self.source, "fetched_at": "2026-10-04T12:00:00+08:00"}],
            "eligibility": {"state": "available", "reasons": []}, "input_hash": "frozen-hash",
            "observation": {"run_id": "internal-run"}, "issue_history": [{"decision": "select"}],
        }
        self.assessment = {
            "precheck": {"status": "PASS", "reasons": ["The supplied announcement supports a reader-facing change."], "evidence_refs": [self.url]},
            "scores": {name: {"score": 8, "reason": "The supplied source supports this dimension.", "evidence_refs": [self.url]} for name in selection.DIMENSIONS},
            "flags": [], "reason": "The event facts are supported; the advertised speed improvement remains untested.",
        }

    def adapt(self, output, policy=None):
        return selection.adapt_assessment(output, policy=policy or self.policy, card=self.card)

    def flag(self, code):
        kinds = {"routine_update": ("limited_increment", "This patch fixes one typo."),
                 "unsupported_promotion": ("unsupported_effect_claim", "Acme claims tasks are twice as fast, without offering measurements.")}
        kind, quote = kinds[code]
        return {"code": code, "reason": "Specific limited change or unsupported effect, not a publication-status judgment.",
                "evidence_refs": [self.url], "basis": {"kind": kind, "claim": quote, "quote": quote, "evidence_url": self.url}}

    def test_projection_omits_gate_history_reviewer_and_prior_editorial_reason(self):
        prepared = {"ranking_type": "daily", "policy": self.policy, "cards": [self.card], "prepare_id": "private-prepare", "blocked_candidates": ["private-block"]}
        before = copy.deepcopy(prepared)
        result = selection.build_scoring_input(prepared, "fixture")
        self.assertEqual({"card", "policy"}, set(result))
        self.assertEqual({"ranking_type", "profile", "material", "evidence_context"}, set(result["card"]))
        encoded = json.dumps(result)
        for field in ("eligibility", "evidence_status", "verification_level", "verified_at", "input_hash", "candidate_id", "prepare_id", "issue_history", "observation", "thresholds", "defer_flags", "reviewer", "Ledger discovery reason"):
            self.assertNotIn(field, encoded)
        self.assertEqual(self.source, result["card"]["evidence_context"][0]["text"])
        self.assertEqual(before, prepared)
        result["policy"]["profiles"]["news"]["value"] = 0
        self.assertEqual(3, prepared["policy"]["profiles"]["news"]["value"])

    def test_exact_assessment_is_unchanged_and_raw_preserved(self):
        result = self.adapt(self.assessment)
        self.assertEqual("accepted", result["status"])
        self.assertEqual([], result["transformations"])
        self.assertEqual(self.assessment, result["raw_output"])
        self.assertEqual(self.assessment, result["assessment"])
        result["assessment"]["scores"]["value"]["score"] = 0
        self.assertEqual(8, result["raw_output"]["scores"]["value"]["score"])
        self.assertEqual(8, self.assessment["scores"]["value"]["score"])

    def test_date_only_event_precision_is_retained_without_inventing_timestamp(self):
        event = {"id": "internal-id", "url": self.url, "type": "news", "occurred_on": "2026-10-01", "date_precision": "date", "timezone": "unknown"}
        self.card["material"]["event"] = event
        prepared = {"ranking_type": "daily", "policy": self.policy, "cards": [self.card]}
        projected = selection.build_scoring_input(prepared, "fixture")["card"]["material"]["event"]
        self.assertEqual({key: value for key, value in event.items() if key != "id"}, projected)
        self.assertNotIn("occurred_at", projected)

    def test_only_nonempty_reasons_string_has_lossless_adaptation(self):
        raw = copy.deepcopy(self.assessment)
        raw["precheck"]["reasons"] = "One complete source-based reason."
        result = self.adapt(raw)
        self.assertEqual("accepted", result["status"])
        self.assertEqual([raw["precheck"]["reasons"]], result["assessment"]["precheck"]["reasons"])
        self.assertEqual(raw, result["raw_output"])
        self.assertEqual(raw["scores"], result["assessment"]["scores"])
        self.assertEqual([{"path": "precheck.reasons", "operation": "wrap_nonempty_string_in_array"}], result["transformations"])

    def test_authority_fields_wrappers_and_ambiguous_pilot_shapes_are_rejected(self):
        bads = [{**self.assessment, key: value} for key, value in (
            ("decision", "select"), ("override_reason", None), ("reviewer", {"model": "invented"}),
            ("prepare_id", None), ("total_score", 80), ("null", self.assessment["scores"]["interest"]))]
        bads += [{"assessment": self.assessment}, json.dumps(self.assessment), [self.assessment]]
        alias = copy.deepcopy(self.assessment)
        alias["scores"]["news_usability"] = alias["scores"].pop("usability")
        bads.append(alias)
        extra = copy.deepcopy(self.assessment)
        extra["scores"]["evidence"]["metadata"] = None
        bads.append(extra)
        for raw in bads:
            with self.subTest(raw=raw):
                result = self.adapt(raw)
                self.assertEqual("rejected", result["status"])
                self.assertIsNone(result["assessment"])
                self.assertEqual(raw, result["raw_output"])
                self.assertTrue(result["error"])

    def test_missing_reasons_scores_and_wrong_types_are_not_guessed(self):
        bads = []
        for path, value in (("scores", None), ("reason", ""), ("flags", None)):
            bads.append({**self.assessment, path: value})
        for value in ("", [], None):
            raw = copy.deepcopy(self.assessment); raw["precheck"]["reasons"] = value; bads.append(raw)
        for value in (True, 7.5, "8"):
            raw = copy.deepcopy(self.assessment); raw["scores"]["value"]["score"] = value; bads.append(raw)
        missing = copy.deepcopy(self.assessment); del missing["scores"]["interest"]; bads.append(missing)
        for raw in bads:
            with self.subTest(raw=raw):
                self.assertEqual("rejected", self.adapt(raw)["status"])

    def test_oversized_output_is_rejected_once_without_repair(self):
        raw = {**self.assessment, "reason": "x" * selection.MAX_ASSESSMENT_BYTES}
        result = self.adapt(raw)
        self.assertEqual("rejected", result["status"])
        self.assertIn("65536", result["error"])
        self.assertEqual([], result["transformations"])
        self.assertEqual(raw, result["raw_output"])

    def test_news_event_facts_need_no_independent_effect_trial_or_automatic_cap(self):
        result = self.adapt(self.assessment)
        self.assertEqual("accepted", result["status"])
        computed = selection._validate_assessment(self.policy, self.card, result["assessment"])
        self.assertEqual(80, computed["total_score"])
        self.assertEqual([], computed["applied_caps"])
        self.assertEqual(self.assessment["scores"], result["assessment"]["scores"])

    def test_sensitive_flags_require_typed_verbatim_source_basis(self):
        for code in ("routine_update", "unsupported_promotion"):
            good = copy.deepcopy(self.assessment); good["flags"] = [self.flag(code)]
            self.assertEqual("accepted", self.adapt(good)["status"])
            for problem in ("missing", "invented_quote", "other_url", "wrong_kind", "empty_claim"):
                raw = copy.deepcopy(good)
                basis = raw["flags"][0]["basis"]
                if problem == "missing": del raw["flags"][0]["basis"]
                elif problem == "invented_quote": basis["quote"] = "This wording is not present in the original."
                elif problem == "other_url": basis["evidence_url"] = "https://example.com/unread"
                elif problem == "wrong_kind": basis["kind"] = "no_independent_tests"
                else: basis["claim"] = ""
                with self.subTest(code=code, problem=problem):
                    self.assertEqual("rejected", self.adapt(raw)["status"])

    def test_old_frozen_policy_accepts_its_original_flag_contract(self):
        legacy = copy.deepcopy(self.policy); legacy.pop("flag_basis"); legacy["version"] = "v2-reader-fit-uncalibrated"
        raw = copy.deepcopy(self.assessment)
        raw["flags"] = [{"code": "routine_update", "reason": "Original v2 judgment.", "evidence_refs": [self.url]}]
        old = self.adapt(raw, legacy)
        self.assertEqual("accepted", old["status"])
        self.assertEqual(3, selection._validate_assessment(legacy, self.card, old["assessment"])["effective_scores"]["novelty"])
        self.assertEqual("rejected", self.adapt(raw)["status"])

    def test_prompt_has_one_executable_example_and_no_full_review_output_path(self):
        text = (self.root / selection.PROMPT_PATH).read_text(encoding="utf-8")
        examples = re.findall(r"```json\s*(.*?)\s*```", text, re.S)
        self.assertEqual(1, len(examples))
        self.assertEqual("accepted", self.adapt(json.loads(examples[0]))["status"])
        self.assertNotIn("完整 review", text)
        self.assertNotIn("digest-selection.review.v1", text)

    def test_saved_pilot_receipts_remain_auditable_rejections_not_silent_repairs(self):
        paths = sorted((self.root / "work/digest-reader-pilot-20261004/scores").glob("*.json"))
        if not paths:
            self.skipTest("Local saved pilot receipts are not part of the repository checkout")
        seen = 0
        for path in paths:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            raw = receipt["receipt"]["output"]
            # Isolate the actual shape regression from unrelated URL mismatches.
            # These synthetic contexts do not certify any source or model claim.
            refs = set()
            def collect_refs(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key == "evidence_refs" and isinstance(item, list):
                            refs.update(url for url in item if isinstance(url, str))
                        else:
                            collect_refs(item)
                elif isinstance(value, list):
                    for item in value:
                        collect_refs(item)
            collect_refs(raw)
            card = copy.deepcopy(self.card)
            card["evidence_context"] = [{"url": url, "text": "Structure-only test context, not evidence verification."} for url in refs]
            with self.subTest(candidate=receipt["title"]):
                result = selection.adapt_assessment(raw, policy=self.policy, card=card)
                self.assertEqual("rejected", result["status"])
                self.assertEqual(raw, result["raw_output"])
                self.assertIsNone(result["assessment"])
                self.assertIn("requires exactly", result["error"])
                self.assertNotIn("absent", result["error"])
            seen += 1
        self.assertEqual(12, seen)


if __name__ == "__main__":
    unittest.main()
