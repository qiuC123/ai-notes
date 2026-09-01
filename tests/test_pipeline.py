from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aihot.pipeline import DailyPipeline, PipelineRunError, SourceSpec
from aihot.score import load_scoring_config


FIXTURES = Path(__file__).parent / "fixtures"


class FixtureFetcher:
    def __init__(self, payloads: dict[str, bytes], failures: set[str] | None = None) -> None:
        self.payloads = payloads
        self.failures = failures or set()

    def fetch(self, source: SourceSpec) -> bytes:
        if source.source_id in self.failures:
            raise RuntimeError("fixture network down")
        return self.payloads[source.source_id]


def sources() -> list[SourceSpec]:
    return [
        SourceSpec("official-one", "https://fixture.test/official", "aihot_selected", "json", "primary_official", "Official", True),
        SourceSpec("open-source-two", "https://fixture.test/open-source", "aihot_selected", "json", "open_source", "Open Source", True),
        SourceSpec("research-three", "https://fixture.test/research", "aihot_selected", "json", "research", "Research", True),
        SourceSpec("broken-four", "https://fixture.test/broken", "aihot_selected", "json", "aggregator", "Broken", False),
    ]


def payloads() -> dict[str, bytes]:
    return {
        "official-one": (FIXTURES / "pipeline_official.json").read_bytes(),
        "open-source-two": (FIXTURES / "pipeline_open_source.json").read_bytes(),
        "research-three": (FIXTURES / "pipeline_research.json").read_bytes(),
    }


class DailyPipelineTests(unittest.TestCase):
    def make_pipeline(self, root: Path, *, failed_sources: set[str] | None = None) -> DailyPipeline:
        return DailyPipeline(
            root=root,
            sources=sources(),
            scoring_config=load_scoring_config(ROOT / "config" / "scoring.yaml"),
            fetcher=FixtureFetcher(payloads(), failed_sources),
        )

    def test_one_source_failure_is_recorded_without_blocking_three_successes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.make_pipeline(root, failed_sources={"broken-four"}).run("2026-08-27", top=3)
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual("success", manifest["exit_status"])
        self.assertEqual(["official-one", "open-source-two", "research-three"], manifest["successful_sources"])
        self.assertEqual("broken-four", manifest["failed_sources"][0]["source_id"])
        self.assertIn("fixture network down", manifest["failed_sources"][0]["error"])

    def test_fewer_than_three_successful_sources_fails_closed_and_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline = self.make_pipeline(root, failed_sources={"research-three", "broken-four"})

            with self.assertRaises(PipelineRunError):
                pipeline.run("2026-08-27", top=3)

            manifest = json.loads((root / "outputs" / "2026-08-27" / "run-manifest.json").read_text(encoding="utf-8"))

        self.assertEqual("failed", manifest["exit_status"])
        self.assertEqual(2, len(manifest["successful_sources"]))
        self.assertEqual(2, len(manifest["failed_sources"]))

    def test_fixture_run_writes_json_markdown_manifest_and_normalized_event_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.make_pipeline(root, failed_sources={"broken-four"}).run("2026-08-27", top=3)
            events = json.loads(result.events_path.read_text(encoding="utf-8"))
            candidates = json.loads(result.top_candidates_path.read_text(encoding="utf-8"))
            markdown = result.markdown_path.read_text(encoding="utf-8")
            raw_snapshot_exists = (root / "data" / "raw" / "2026-08-27" / "official-one.json").exists()

        self.assertEqual(3, len(events["events"]))
        normalized = events["events"][0]["items"][0]
        required_normalized_fields = {
                "item_id",
                "title",
                "summary",
                "url",
                "source_id",
                "source_class",
                "owner",
                "published_at",
                "discovered_at",
                "categories",
                "entities",
                "evidence_allowed",
                "raw_ref",
            }
        self.assertTrue(required_normalized_fields.issubset(normalized))
        self.assertEqual(3, candidates["top_n"])
        self.assertEqual(3, len(candidates["candidates"]))
        self.assertTrue(candidates["candidates"][0]["source_urls"])
        self.assertIn("score_breakdown", candidates["candidates"][0])
        self.assertIn("为什么适合 Amesi", markdown)
        self.assertIn("尚待核验事项", markdown)
        self.assertTrue(raw_snapshot_exists)

    def test_same_date_rerun_overwrites_stably_without_duplicate_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline = self.make_pipeline(root, failed_sources={"broken-four"})
            first = pipeline.run("2026-08-27", top=3)
            first_artifacts = {
                path.name: path.read_bytes()
                for path in (first.events_path, first.top_candidates_path, first.markdown_path)
            }
            second = pipeline.run("2026-08-27", top=3)
            second_events = json.loads(second.events_path.read_text(encoding="utf-8"))
            second_artifacts = {
                path.name: path.read_bytes()
                for path in (second.events_path, second.top_candidates_path, second.markdown_path)
            }

        self.assertEqual(first_artifacts, second_artifacts)
        self.assertEqual(3, len(second_events["events"]))


if __name__ == "__main__":
    unittest.main()
