from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aihot.cluster import cluster_items
from aihot.models import NormalizedItem
from aihot.pipeline import DailyPipeline, PipelineRunError, SourceSpec, load_pipeline_config
from aihot.score import load_scoring_config, score_event


FIXTURES = Path(__file__).parent / "fixtures"
SCORING = load_scoring_config(ROOT / "config" / "scoring.yaml")


class FixtureFetcher:
    def __init__(self, payloads: dict[str, bytes], failures: set[str] | None = None) -> None:
        self.payloads = payloads
        self.failures = failures or set()
        self.calls: list[str] = []

    def fetch(self, source: SourceSpec) -> bytes:
        self.calls.append(source.source_id)
        if source.source_id in self.failures:
            raise RuntimeError("fixture network down")
        return self.payloads[source.source_id]


def source(
    source_id: str,
    *,
    source_class: str = "primary_official",
    parser: str = "aihot_selected",
    raw_format: str = "json",
    evidence_allowed: bool = True,
) -> SourceSpec:
    return SourceSpec(
        source_id,
        f"https://fixture.test/{source_id}",
        parser,
        raw_format,
        source_class,
        source_id,
        evidence_allowed,
    )


def standard_payloads() -> dict[str, bytes]:
    return {
        "official-one": (FIXTURES / "pipeline_official.json").read_bytes(),
        "open-source-two": (FIXTURES / "pipeline_open_source.json").read_bytes(),
        "research-three": (FIXTURES / "pipeline_research.json").read_bytes(),
    }


def record(
    item_id: str,
    title: str,
    url: str,
    source_id: str,
    source_class: str,
    evidence_allowed: bool = True,
) -> NormalizedItem:
    return NormalizedItem(
        item_id=item_id,
        title=title,
        summary="A useful workflow update.",
        url=url,
        source_id=source_id,
        source_class=source_class,
        owner=source_id,
        published_at="2026-08-27T08:00:00Z",
        discovered_at="2026-08-27T00:00:00Z",
        categories=["agent"],
        entities=[source_id],
        evidence_allowed=evidence_allowed,
        raw_ref="data/raw/2026-08-27/fixture.json",
    )


class SafetyContractTests(unittest.TestCase):
    def make_pipeline(
        self,
        root: Path,
        source_specs: list[SourceSpec],
        payloads: dict[str, bytes],
        failures: set[str] | None = None,
    ) -> DailyPipeline:
        return DailyPipeline(
            root=root,
            sources=source_specs,
            scoring_config=SCORING,
            fetcher=FixtureFetcher(payloads, failures),
        )

    def test_aggregator_only_cluster_remains_discovery_only_and_is_penalized(self) -> None:
        event = cluster_items(
            [
                record("a", "Agent workflow update", "https://example.test/a", "aihot-selected", "aggregator"),
                record("b", "Agent workflow update", "https://example.test/b", "aihot-topics", "aggregator"),
            ],
            title_threshold=0.48,
            hours_window=72,
        )[0]

        result = score_event(event, SCORING, as_of="2026-08-27")

        self.assertEqual("discovery_only", event.evidence_status)
        self.assertIsNone(event.primary_source_url)
        self.assertEqual(-20, result.penalties["discovery_only"])

    def test_evidence_disallowed_primary_class_item_cannot_become_primary(self) -> None:
        event = cluster_items(
            [record("a", "Official agent update", "https://example.test/a", "untrusted-primary", "primary_official", False)],
            title_threshold=0.48,
            hours_window=72,
        )[0]

        self.assertIsNone(event.primary_source_url)
        self.assertEqual("insufficient", event.evidence_status)

    def test_error_shaped_json_and_html_are_manifest_failures_not_successes(self) -> None:
        source_specs = [
            source("official-one"),
            source("open-source-two", source_class="open_source"),
            source("research-three", source_class="research"),
            source("invalid-json"),
            source("invalid-html", parser="rss", raw_format="xml"),
        ]
        payloads = {
            **standard_payloads(),
            "invalid-json": b'{"error":"upstream unavailable"}',
            "invalid-html": b"<html><body>temporary error</body></html>",
        }
        with tempfile.TemporaryDirectory() as directory:
            result = self.make_pipeline(Path(directory), source_specs, payloads).run("2026-08-27", top=3)
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(["official-one", "open-source-two", "research-three"], manifest["successful_sources"])
        self.assertEqual({"invalid-json", "invalid-html"}, {entry["source_id"] for entry in manifest["failed_sources"]})

    def test_failed_rerun_invalidates_old_candidate_and_event_artifacts(self) -> None:
        source_specs = [
            source("official-one"),
            source("open-source-two", source_class="open_source"),
            source("research-three", source_class="research"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_pipeline(root, source_specs, standard_payloads()).run("2026-08-27", top=3)
            failed_pipeline = self.make_pipeline(
                root,
                source_specs,
                standard_payloads(),
                failures={"open-source-two", "research-three"},
            )
            with self.assertRaises(PipelineRunError):
                failed_pipeline.run("2026-08-27", top=3, refresh=True)
            events = json.loads((root / "data" / "events" / "2026-08-27" / "events.json").read_text(encoding="utf-8"))
            candidates = json.loads((root / "outputs" / "2026-08-27" / "top-candidates.json").read_text(encoding="utf-8"))
            markdown = (root / "outputs" / "2026-08-27" / "top-candidates.md").read_text(encoding="utf-8")

        self.assertEqual("failed", events["exit_status"])
        self.assertEqual([], events["events"])
        self.assertEqual("failed", candidates["exit_status"])
        self.assertEqual([], candidates["candidates"])
        self.assertIn("本次运行失败", markdown)

    def test_requested_and_actual_top_counts_are_consistent_when_fewer_events_exist(self) -> None:
        source_specs = [source("one"), source("two", source_class="open_source"), source("three", source_class="research")]
        shared = (FIXTURES / "pipeline_official.json").read_bytes()
        payloads = {source_spec.source_id: shared for source_spec in source_specs}
        with tempfile.TemporaryDirectory() as directory:
            result = self.make_pipeline(Path(directory), source_specs, payloads).run("2026-08-27", top=3)
            candidates = json.loads(result.top_candidates_path.read_text(encoding="utf-8"))
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(3, candidates["requested_top_n"])
        self.assertEqual(1, candidates["top_n"])
        self.assertEqual(candidates["top_n"], candidates["candidate_count"])
        self.assertEqual(candidates["top_n"], manifest["top_n"])

    def test_aggregator_original_and_discovery_links_keep_provenance_and_markdown_reasons(self) -> None:
        source_specs = [
            source("aihot-one", source_class="aggregator", evidence_allowed=False),
            source("aihot-two", source_class="aggregator", evidence_allowed=False),
            source("aihot-three", source_class="aggregator", evidence_allowed=False),
        ]
        payload = (FIXTURES / "aihot_selected.json").read_bytes()
        payloads = {source_spec.source_id: payload for source_spec in source_specs}
        with tempfile.TemporaryDirectory() as directory:
            result = self.make_pipeline(Path(directory), source_specs, payloads).run("2026-08-27", top=3)
            candidate = json.loads(result.top_candidates_path.read_text(encoding="utf-8"))["candidates"][0]
            markdown = result.markdown_path.read_text(encoding="utf-8")

        links = {(link["url"], link["provenance"]) for link in candidate["source_links"]}
        self.assertEqual("discovery_only", candidate["evidence_status"])
        self.assertIn(("https://openai.com/index/responses-api-update", "original_link_from_aggregator"), links)
        self.assertIn(("https://aihot.virxact.com/items/selected-1", "discovery_link"), links)
        self.assertIn("惩罚原因：", markdown)
        self.assertIn("仅有聚合发现信号", markdown)

    def test_all_unlinked_candidates_fail_closed_instead_of_emitting_empty_source_urls(self) -> None:
        source_specs = [source("one"), source("two", source_class="open_source"), source("three", source_class="research")]
        unlinked = (FIXTURES / "pipeline_no_link.json").read_bytes()
        payloads = {source_spec.source_id: unlinked for source_spec in source_specs}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(PipelineRunError):
                self.make_pipeline(root, source_specs, payloads).run("2026-08-27", top=3)
            candidates = json.loads((root / "outputs" / "2026-08-27" / "top-candidates.json").read_text(encoding="utf-8"))

        self.assertEqual([], candidates["candidates"])

    def test_clustering_settings_are_loaded_from_config_not_hard_coded(self) -> None:
        source_yaml = """
sources:
  - id: one
    url: https://fixture.test/one
    parser: aihot_selected
    format: json
    source_class: primary_official
    owner: One
    evidence_allowed: true
clustering:
  title_threshold: 0.77
  hours_window: 36
"""
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "sources.yaml"
            config_path.write_text(source_yaml, encoding="utf-8")
            _, clustering = load_pipeline_config(config_path)

        self.assertEqual(0.77, clustering["title_threshold"])
        self.assertEqual(36, clustering["hours_window"])

    def test_unapproved_prior_candidate_does_not_count_as_a_recent_decision(self) -> None:
        source_specs = [
            source("official-one"),
            source("open-source-two", source_class="open_source"),
            source("research-three", source_class="research"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_pipeline(root, source_specs, standard_payloads()).run("2026-08-26", top=3)
            current = self.make_pipeline(root, source_specs, standard_payloads()).run("2026-08-27", top=3)
            penalties = json.loads(current.top_candidates_path.read_text(encoding="utf-8"))["candidates"][0]["penalties"]

        self.assertNotIn("recent_decision_similarity", penalties)

    def test_aggregator_only_url_is_labeled_as_discovery_not_original(self) -> None:
        source_specs = [
            source("aihot-one", source_class="aggregator", evidence_allowed=False),
            source("aihot-two", source_class="aggregator", evidence_allowed=False),
            source("aihot-three", source_class="aggregator", evidence_allowed=False),
        ]
        payload = (FIXTURES / "aihot_discovery_only.json").read_bytes()
        payloads = {source_spec.source_id: payload for source_spec in source_specs}
        with tempfile.TemporaryDirectory() as directory:
            result = self.make_pipeline(Path(directory), source_specs, payloads).run("2026-08-27", top=3)
            links = json.loads(result.top_candidates_path.read_text(encoding="utf-8"))["candidates"][0]["source_links"]

        self.assertEqual({"discovery_link"}, {link["provenance"] for link in links})

    def test_source_links_are_sorted_by_every_stable_field(self) -> None:
        source_specs = [
            source("aihot-one", source_class="aggregator", evidence_allowed=False),
            source("aihot-two", source_class="aggregator", evidence_allowed=False),
            source("aihot-three", source_class="aggregator", evidence_allowed=False),
        ]
        payload = (FIXTURES / "aihot_selected.json").read_bytes()
        payloads = {source_spec.source_id: payload for source_spec in source_specs}
        with tempfile.TemporaryDirectory() as directory:
            result = self.make_pipeline(Path(directory), source_specs, payloads).run("2026-08-27", top=3)
            links = json.loads(result.top_candidates_path.read_text(encoding="utf-8"))["candidates"][0]["source_links"]

        self.assertEqual(
            links,
            sorted(links, key=lambda link: (link["url"], link["source_id"], link["source_class"], link["provenance"])),
        )

    def test_successful_same_date_rerun_replays_the_real_raw_snapshot_without_fetching(self) -> None:
        source_specs = [
            source("official-one"),
            source("open-source-two", source_class="open_source"),
            source("research-three", source_class="research"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_fetcher = FixtureFetcher(standard_payloads())
            first_pipeline = DailyPipeline(root=root, sources=source_specs, scoring_config=SCORING, fetcher=first_fetcher)
            first = first_pipeline.run("2026-08-27", top=3, refresh=True)
            first_artifacts = [path.read_bytes() for path in (first.events_path, first.top_candidates_path, first.markdown_path)]
            replay_fetcher = FixtureFetcher(standard_payloads(), failures={source_spec.source_id for source_spec in source_specs})
            replay_pipeline = DailyPipeline(root=root, sources=source_specs, scoring_config=SCORING, fetcher=replay_fetcher)
            second = replay_pipeline.run("2026-08-27", top=3)
            second_artifacts = [path.read_bytes() for path in (second.events_path, second.top_candidates_path, second.markdown_path)]
            manifest = json.loads(second.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(source_specs and [source_spec.source_id for source_spec in source_specs], first_fetcher.calls)
        self.assertEqual([], replay_fetcher.calls)
        self.assertEqual(first_artifacts, second_artifacts)
        self.assertEqual([source_spec.source_id for source_spec in source_specs], manifest["reused_raw_sources"])


if __name__ == "__main__":
    unittest.main()
