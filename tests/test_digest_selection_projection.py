"""Lossless, opt-in model projections; no network, models or database writes."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from ai_notes import digest_selection as selection


class SourceReferenceProjectionTests(unittest.TestCase):
    def setUp(self):
        self.policy = selection.load_policy(Path(__file__).resolve().parents[1])
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


if __name__ == "__main__":
    unittest.main()
