from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aihot.release_sources import (
    ReleaseRecord,
    ReleaseSource,
    filter_release,
    load_release_sources,
    normalize_evidence_text,
    normalize_html_text,
    parse_release_atom,
)


FIXTURES = Path(__file__).parent / "fixtures"


class ReleaseSourceTests(unittest.TestCase):
    def test_atom_parser_extracts_real_tag_from_entry_id(self) -> None:
        source = ReleaseSource(
            source_id="mcp_spec",
            repository="modelcontextprotocol/modelcontextprotocol",
            category="agent_mcp",
            feed_url="https://github.com/modelcontextprotocol/modelcontextprotocol/releases.atom",
            accepted_tag_patterns=(r"\d{4}-\d{2}-\d{2}(?:-final)?",),
        )

        records = parse_release_atom(
            (FIXTURES / "mcp_release_final.atom.xml").read_text(encoding="utf-8"),
            source=source,
            discovered_at="2026-09-01T00:00:00Z",
            raw_ref="data/raw/2026-09-01/mcp_spec.xml",
        )

        self.assertEqual(1, len(records))
        self.assertEqual("2024-11-05-final", records[0].release_tag)
        self.assertEqual("modelcontextprotocol/modelcontextprotocol@2024-11-05-final", records[0].release_key)
        self.assertIn("Added OAuth authorization server discovery.", records[0].release_notes_text)

    def test_hard_filter_accepts_final_and_rejects_rc_and_alpha(self) -> None:
        source = ReleaseSource(
            source_id="mcp_spec",
            repository="modelcontextprotocol/modelcontextprotocol",
            category="agent_mcp",
            feed_url="https://github.com/modelcontextprotocol/modelcontextprotocol/releases.atom",
            accepted_tag_patterns=(r"\d{4}-\d{2}-\d{2}(?:-final)?",),
        )
        record = parse_release_atom(
            (FIXTURES / "mcp_release_final.atom.xml").read_text(encoding="utf-8"),
            source=source,
            discovered_at="2026-09-01T00:00:00Z",
            raw_ref="data/raw/2026-09-01/mcp_spec.xml",
        )[0]

        accepted = filter_release(record, source)
        rc = filter_release(
            replace(record, release_tag="2026-07-28-RC", release_key=f"{source.repository}@2026-07-28-RC"),
            source,
        )
        alpha = filter_release(
            replace(record, release_tag="2026-07-28-alpha.1", release_key=f"{source.repository}@2026-07-28-alpha.1"),
            source,
        )

        self.assertTrue(accepted.eligible)
        self.assertFalse(rc.eligible)
        self.assertIn("prerelease_tag", rc.reasons)
        self.assertFalse(alpha.eligible)
        self.assertIn("prerelease_tag", alpha.reasons)

    def test_approved_registry_loads_exactly_twelve_trial_sources(self) -> None:
        sources = load_release_sources(ROOT / "config" / "ai_notes_sources.yaml")

        self.assertEqual(12, len(sources))
        self.assertEqual(
            {
                "mcp_spec",
                "openai_agents_python",
                "langgraph",
                "google_adk_python",
                "nous_hermes_agent",
                "anthropic_claude_code",
                "openai_codex",
                "openhands",
                "ollama",
                "vllm",
                "sglang",
                "transformers",
            },
            {source.source_id for source in sources},
        )
        self.assertTrue(all(source.tier == "trial" for source in sources))
        self.assertTrue(all(source.feed_url.endswith("/releases.atom") for source in sources))
        mcp = next(source for source in sources if source.source_id == "mcp_spec")
        self.assertTrue(filter_release(
            parse_release_atom(
                (FIXTURES / "mcp_release_final.atom.xml").read_text(encoding="utf-8"),
                source=mcp,
                discovered_at="2026-09-01T00:00:00Z",
                raw_ref="data/raw/2026-09-01/mcp_spec.xml",
            )[0],
            mcp,
        ).eligible)

    def test_html_normalization_ignores_hidden_nodes_but_plain_evidence_preserves_angle_brackets(self) -> None:
        self.assertEqual("Visible release text", normalize_html_text("<style>hidden</style><p>Visible release text</p>"))
        self.assertEqual("Use <model> literally", normalize_evidence_text("Use <model> literally"))

    def test_registry_rejects_source_ids_that_can_escape_raw_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.yaml"
            path.write_text(
                "sources:\n  - id: ../escape\n    repository: owner/repo\n    category: test\n"
                "    accepted_tag_patterns: ['v\\d+']\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_release_sources(path)

    def test_every_registered_source_declares_feed_policy_and_exclusions_explicitly(self) -> None:
        payload = yaml.safe_load((ROOT / "config" / "ai_notes_sources.yaml").read_text(encoding="utf-8"))

        for item in payload["sources"]:
            self.assertIn("feed_url", item)
            self.assertIn("policy_version", item)
            self.assertIn("excluded_tag_patterns", item)

    def test_all_twelve_sources_have_positive_and_negative_tag_fixtures(self) -> None:
        sources = load_release_sources(ROOT / "config" / "ai_notes_sources.yaml")
        cases = json.loads((FIXTURES / "ai_notes_tag_cases.json").read_text(encoding="utf-8"))

        self.assertEqual({source.source_id for source in sources}, set(cases))
        for source in sources:
            base = ReleaseRecord(
                release_key=f"{source.repository}@fixture",
                source_id=source.source_id,
                repository=source.repository,
                release_tag="fixture",
                title="Fixture release",
                release_notes_html="Release with substantive notes.",
                release_notes_text="Release with substantive notes.",
                url=f"https://github.com/{source.repository}/releases/tag/fixture",
                published_at="2026-09-01T00:00:00Z",
                discovered_at="2026-09-01T01:00:00Z",
                raw_ref=f"data/raw/2026-09-01/{source.source_id}.xml",
            )
            accepted_tag = cases[source.source_id]["accept"]
            rejected_tag = cases[source.source_id]["reject"]
            accepted = replace(base, release_tag=accepted_tag, release_key=f"{source.repository}@{accepted_tag}")
            rejected = replace(base, release_tag=rejected_tag, release_key=f"{source.repository}@{rejected_tag}")

            self.assertTrue(filter_release(accepted, source).eligible, source.source_id)
            self.assertFalse(filter_release(rejected, source).eligible, source.source_id)


if __name__ == "__main__":
    unittest.main()
