from __future__ import annotations

import sys
import tempfile
import unittest
import json
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aihot.release_ledger import ReleaseLedger, detect_atom_gap
from aihot.release_sources import GitHubReleaseRestBackfiller, ReleaseRecord, ReleaseSource


def release() -> ReleaseRecord:
    return ReleaseRecord(
        release_key="openai/codex@rust-v0.151.0",
        source_id="openai_codex",
        repository="openai/codex",
        release_tag="rust-v0.151.0",
        title="0.151.0",
        release_notes_html="<p>Added MCP interception.</p>",
        release_notes_text="Added MCP interception.",
        url="https://github.com/openai/codex/releases/tag/rust-v0.151.0",
        published_at="2026-08-29T09:57:02Z",
        discovered_at="2026-09-01T00:00:00Z",
        raw_ref="data/raw/2026-09-01/openai_codex.xml",
    )


class ReleaseLedgerTests(unittest.TestCase):
    def test_release_is_processed_once_until_policy_version_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release-ledger.jsonl"
            ledger = ReleaseLedger(path)
            self.assertTrue(ledger.needs_processing(release(), policy_version="1"))

            ledger.record(release(), status="accepted", policy_version="1", reasons=[])
            reloaded = ReleaseLedger(path)

            self.assertFalse(reloaded.needs_processing(release(), policy_version="1"))
            self.assertTrue(reloaded.needs_processing(release(), policy_version="2"))
            self.assertEqual("accepted", reloaded.get(release().release_key)["status"])

    def test_ledger_preserves_first_discovery_raw_reference_and_status_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = ReleaseLedger(Path(directory) / "ledger.jsonl")
            ledger.record(release(), status="pending_review", policy_version="1", reasons=[])
            ledger.record(release(), status="accepted", policy_version="1", reasons=[])
            entry = ledger.get(release().release_key)

        self.assertEqual("2026-09-01T00:00:00Z", entry["first_discovered_at"])
        self.assertEqual(release().raw_ref, entry["raw_ref"])
        self.assertEqual(["pending_review", "accepted"], [item["status"] for item in entry["history"]])
        self.assertTrue(entry["last_processed_at"])

    def test_feed_without_ledger_overlap_is_a_known_gap(self) -> None:
        current = [
            release(),
            replace(
                release(),
                release_key="openai/codex@rust-v0.150.1",
                release_tag="rust-v0.150.1",
                published_at="2026-08-28T09:00:00Z",
            ),
        ]

        gap = detect_atom_gap(
            current,
            known_keys={"openai/codex@rust-v0.149.0"},
            last_success_at="2026-08-20T00:00:00Z",
        )

        self.assertTrue(gap.has_gap)
        self.assertEqual("no_ledger_overlap", gap.reason)
        self.assertEqual("2026-08-28T09:00:00Z", gap.oldest_published_at)

    def test_feed_oldest_release_after_last_success_is_gap_even_with_overlap(self) -> None:
        gap = detect_atom_gap(
            [release()],
            known_keys={release().release_key},
            last_success_at="2026-08-20T00:00:00Z",
        )

        self.assertTrue(gap.has_gap)
        self.assertEqual("feed_window_after_last_success", gap.reason)

    def test_rest_backfill_pages_until_known_release_and_returns_missing_records(self) -> None:
        source = ReleaseSource(
            source_id="openai_codex",
            repository="openai/codex",
            category="ai_coding",
            feed_url="https://github.com/openai/codex/releases.atom",
            accepted_tag_patterns=(r"rust-v\d+\.\d+\.\d+",),
        )

        def payload(*tags: str) -> bytes:
            return json.dumps([
                {
                    "tag_name": tag,
                    "name": tag,
                    "body": f"Changes for {tag}",
                    "html_url": f"https://github.com/openai/codex/releases/tag/{tag}",
                    "published_at": f"2026-08-{29-index:02d}T00:00:00Z",
                    "draft": False,
                    "prerelease": False,
                }
                for index, tag in enumerate(tags)
            ]).encode("utf-8")

        class FakeFetcher:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def fetch_url(self, url: str) -> bytes:
                self.calls.append(url)
                return payload("rust-v0.3.0", "rust-v0.2.0") if "page=1" in url else payload("rust-v0.1.0")

        fetcher = FakeFetcher()
        result = GitHubReleaseRestBackfiller(fetcher=fetcher, per_page=2, max_pages=3).fetch_missing(
            source,
            known_keys={"openai/codex@rust-v0.1.0"},
            last_success_at="2026-08-20T00:00:00Z",
            discovered_at="2026-09-01T00:00:00Z",
            raw_ref="data/raw/2026-09-01/openai_codex-backfill.json",
        )

        self.assertTrue(result.closed)
        self.assertEqual(["rust-v0.3.0", "rust-v0.2.0"], [record.release_tag for record in result.records])
        self.assertEqual(2, len(fetcher.calls))

    def test_backtest_with_no_known_keys_is_complete_at_end_of_repository_history(self) -> None:
        source = ReleaseSource(
            source_id="mcp_spec",
            repository="modelcontextprotocol/modelcontextprotocol",
            category="agent_mcp",
            feed_url="https://github.com/modelcontextprotocol/modelcontextprotocol/releases.atom",
            accepted_tag_patterns=(r"\d{4}-\d{2}-\d{2}",),
        )

        class Fetcher:
            def fetch_url(self, url: str) -> bytes:
                return json.dumps([{
                    "tag_name": "2026-07-28",
                    "name": "2026-07-28",
                    "body": "Specification update",
                    "html_url": "https://github.com/modelcontextprotocol/modelcontextprotocol/releases/tag/2026-07-28",
                    "published_at": "2026-07-28T00:00:00Z",
                    "draft": False,
                    "prerelease": False,
                }]).encode("utf-8")

        result = GitHubReleaseRestBackfiller(fetcher=Fetcher(), per_page=100, max_pages=3).fetch_missing(
            source,
            known_keys=set(),
            last_success_at="2026-06-02T00:00:00Z",
            discovered_at="2026-09-01T00:00:00Z",
            raw_ref="data/backtests/mcp.json",
        )

        self.assertTrue(result.closed)
        self.assertEqual(1, len(result.records))

    def test_rest_backfill_reuses_cached_page_without_second_network_call(self) -> None:
        source = ReleaseSource(
            source_id="mcp_spec",
            repository="modelcontextprotocol/modelcontextprotocol",
            category="agent_mcp",
            feed_url="https://github.com/modelcontextprotocol/modelcontextprotocol/releases.atom",
            accepted_tag_patterns=(r"\d{4}-\d{2}-\d{2}",),
        )

        class Fetcher:
            def __init__(self) -> None:
                self.calls = 0

            def fetch_url(self, url: str) -> bytes:
                self.calls += 1
                return b"[]"

        with tempfile.TemporaryDirectory() as directory:
            fetcher = Fetcher()
            backfiller = GitHubReleaseRestBackfiller(
                fetcher=fetcher, per_page=100, max_pages=1, cache_dir=Path(directory)
            )
            kwargs = {
                "known_keys": set(),
                "last_success_at": "2026-06-02T00:00:00Z",
                "discovered_at": "2026-09-01T00:00:00Z",
                "raw_ref": "data/backtests/mcp.json",
            }
            backfiller.fetch_missing(source, **kwargs)
            backfiller.fetch_missing(source, **kwargs)

        self.assertEqual(1, fetcher.calls)


if __name__ == "__main__":
    unittest.main()
