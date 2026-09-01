from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aihot.ai_notes import AiNotesPipeline, prune_raw_snapshots
from aihot.release_ledger import ReleaseLedger
from aihot.release_sources import BackfillResult, ReleaseRecord, ReleaseSource


FIXTURES = Path(__file__).parent / "fixtures"


class FixtureFetcher:
    def fetch(self, source: ReleaseSource) -> bytes:
        return (FIXTURES / "mcp_release_final.atom.xml").read_bytes()


class AcceptingReviewer:
    def __init__(self) -> None:
        self.calls = 0

    def review(self, records: list[object]) -> dict[str, object]:
        self.calls += 1
        record = records[0]
        return {
            "decisions": [
                {
                    "release_key": record.release_key,
                    "decision": "accept",
                    "change_types": ["feature"],
                    "substantive_changes": [
                        {
                            "summary_zh": "新增 OAuth 授权服务器发现能力。",
                            "evidence": "Added OAuth authorization server discovery.",
                        }
                    ],
                    "decision_reason": "改变了 MCP 的授权发现能力。",
                    "pending_verification": [],
                }
            ]
        }


def source() -> ReleaseSource:
    return ReleaseSource(
        source_id="mcp_spec",
        repository="modelcontextprotocol/modelcontextprotocol",
        category="agent_mcp",
        feed_url="https://github.com/modelcontextprotocol/modelcontextprotocol/releases.atom",
        accepted_tag_patterns=(r"\d{4}-\d{2}-\d{2}(?:-final)?",),
    )


class AiNotesPipelineTests(unittest.TestCase):
    def test_daily_runs_collect_review_finalize_and_writes_auditable_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reviewer = AcceptingReviewer()
            result = AiNotesPipeline(
                root=root,
                sources=[source()],
                fetcher=FixtureFetcher(),
                reviewer=reviewer,
            ).run("2026-09-01")
            queue = json.loads(result.review_queue_path.read_text(encoding="utf-8"))
            decisions = json.loads(result.review_decisions_path.read_text(encoding="utf-8"))
            accepted = json.loads(result.accepted_information_path.read_text(encoding="utf-8"))
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            markdown = result.markdown_path.read_text(encoding="utf-8")
            ledger = (root / "data" / "releases" / "release-ledger.jsonl").read_text(encoding="utf-8")

        self.assertEqual("success", manifest["status"])
        self.assertFalse(manifest["healthy_empty"])
        self.assertEqual(1, reviewer.calls)
        self.assertEqual(1, len(queue["records"]))
        self.assertEqual("accept", decisions["decisions"][0]["decision"])
        self.assertEqual(1, len(accepted["information"]))
        self.assertIn("新增 OAuth 授权服务器发现能力", markdown)
        self.assertIn('"status": "accepted"', ledger)

    def test_unclosed_atom_gap_preserves_results_but_marks_partial(self) -> None:
        class UnclosedBackfiller:
            def __init__(self) -> None:
                self.calls = 0

            def fetch_missing(self, *args: object, **kwargs: object) -> BackfillResult:
                self.calls += 1
                return BackfillResult(records=(), closed=False, pages_fetched=1)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = ReleaseRecord(
                release_key="modelcontextprotocol/modelcontextprotocol@2024-10-07",
                source_id="mcp_spec",
                repository="modelcontextprotocol/modelcontextprotocol",
                release_tag="2024-10-07",
                title="2024-10-07",
                release_notes_html="<p>Old release.</p>",
                release_notes_text="Old release.",
                url="https://github.com/modelcontextprotocol/modelcontextprotocol/releases/tag/2024-10-07",
                published_at="2026-08-01T00:00:00Z",
                discovered_at="2026-08-20T00:00:00Z",
                raw_ref="data/raw/2026-08-20/mcp_spec.xml",
            )
            ReleaseLedger(root / "data" / "releases" / "release-ledger.jsonl").record(
                old, status="rejected", policy_version="1", reasons=["maintenance_only"]
            )
            state_path = root / "data" / "releases" / "source-state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps({"mcp_spec": {"last_success_at": "2026-08-20T00:00:00Z"}}),
                encoding="utf-8",
            )

            backfiller = UnclosedBackfiller()
            pipeline = AiNotesPipeline(
                root=root,
                sources=[source()],
                fetcher=FixtureFetcher(),
                reviewer=AcceptingReviewer(),
                backfiller=backfiller,
            )
            result = pipeline.run("2026-09-01")
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            accepted = json.loads(result.accepted_information_path.read_text(encoding="utf-8"))
            retry = pipeline.run("2026-09-01")
            retried_accepted = json.loads(retry.accepted_information_path.read_text(encoding="utf-8"))

        self.assertEqual("partial", manifest["status"])
        self.assertFalse(manifest["healthy_empty"])
        self.assertEqual("mcp_spec", manifest["known_gaps"][0]["source_id"])
        self.assertEqual(1, len(accepted["information"]))
        self.assertEqual("partial", retry.status)
        self.assertEqual(2, backfiller.calls)
        self.assertEqual(1, len(retried_accepted["information"]))

    def test_reviewer_exception_keeps_queue_and_returns_review_failed(self) -> None:
        class BrokenReviewer:
            def review(self, records: list[object]) -> dict[str, object]:
                raise RuntimeError("model unavailable")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = AiNotesPipeline(
                root=root,
                sources=[source()],
                fetcher=FixtureFetcher(),
                reviewer=BrokenReviewer(),
            ).run("2026-09-01")
            queue = json.loads(result.review_queue_path.read_text(encoding="utf-8"))
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(1, len(queue["records"]))
        self.assertEqual("review_failed", manifest["status"])
        self.assertFalse(manifest["healthy_empty"])
        self.assertIn("model unavailable", manifest["review_error"])

    def test_review_failed_queue_is_retried_without_changing_policy_version(self) -> None:
        class BrokenReviewer:
            def review(self, records: list[object]) -> dict[str, object]:
                raise RuntimeError("temporary model failure")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failed = AiNotesPipeline(
                root=root, sources=[source()], fetcher=FixtureFetcher(), reviewer=BrokenReviewer()
            ).run("2026-09-01")
            recovered_reviewer = AcceptingReviewer()
            recovered = AiNotesPipeline(
                root=root, sources=[source()], fetcher=FixtureFetcher(), reviewer=recovered_reviewer
            ).run("2026-09-01")
            accepted = json.loads(recovered.accepted_information_path.read_text(encoding="utf-8"))

        self.assertEqual("review_failed", failed.status)
        self.assertEqual("success", recovered.status)
        self.assertEqual(1, recovered_reviewer.calls)
        self.assertEqual(1, len(accepted["information"]))

    def test_security_endpoint_failure_marks_partial_without_losing_release_results(self) -> None:
        class BrokenSecurityFetcher:
            def fetch_security(self, updated_since: str) -> bytes:
                raise RuntimeError("advisory endpoint unavailable")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = AiNotesPipeline(
                root=root,
                sources=[source()],
                fetcher=FixtureFetcher(),
                reviewer=AcceptingReviewer(),
                security_fetcher=BrokenSecurityFetcher(),
            ).run("2026-09-01")
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            accepted = json.loads(result.accepted_information_path.read_text(encoding="utf-8"))

        self.assertEqual("partial", manifest["status"])
        self.assertEqual("github_advisories", manifest["failed_sources"][0]["source_id"])
        self.assertIn("advisory endpoint unavailable", manifest["failed_sources"][0]["error"])
        self.assertEqual(1, len(accepted["information"]))

    def test_security_fetch_uses_last_success_cursor(self) -> None:
        class SecurityFetcher:
            def __init__(self) -> None:
                self.updated_since: str | None = None

            def fetch_security(self, updated_since: str) -> bytes:
                self.updated_since = updated_since
                return b"[]"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "data" / "releases" / "source-state.json"
            state.parent.mkdir(parents=True)
            state.write_text(
                json.dumps({"github_advisories": {"last_success_at": "2026-08-20T00:00:00Z"}}),
                encoding="utf-8",
            )
            security = SecurityFetcher()
            AiNotesPipeline(
                root=root,
                sources=[source()],
                fetcher=FixtureFetcher(),
                reviewer=AcceptingReviewer(),
                security_fetcher=security,
            ).run("2026-09-01")

        self.assertEqual("2026-08-20T00:00:00Z", security.updated_since)

    def test_full_security_page_is_reported_as_unclosed_gap(self) -> None:
        class FullPageSecurityFetcher:
            def fetch_security(self, updated_since: str) -> bytes:
                return json.dumps([{} for _ in range(100)]).encode("utf-8")

        with tempfile.TemporaryDirectory() as directory:
            result = AiNotesPipeline(
                root=Path(directory),
                sources=[source()],
                fetcher=FixtureFetcher(),
                reviewer=AcceptingReviewer(),
                security_fetcher=FullPageSecurityFetcher(),
            ).run("2026-09-01")
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual("partial", result.status)
        self.assertEqual("github_advisories", manifest["known_gaps"][0]["source_id"])
        self.assertEqual("security_page_limit_reached", manifest["known_gaps"][0]["reason"])

    def test_all_release_sources_unavailable_is_failed_not_partial(self) -> None:
        class BrokenFetcher:
            def fetch(self, source: ReleaseSource) -> bytes:
                raise RuntimeError("all feeds unavailable")

        with tempfile.TemporaryDirectory() as directory:
            result = AiNotesPipeline(
                root=Path(directory),
                sources=[source()],
                fetcher=BrokenFetcher(),
                reviewer=AcceptingReviewer(),
            ).run("2026-09-01")
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual("failed", result.status)
        self.assertEqual("failed", manifest["status"])
        self.assertFalse(manifest["healthy_empty"])

    def test_missing_required_historical_baseline_marks_run_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = AiNotesPipeline(
                root=Path(directory),
                sources=[source()],
                fetcher=FixtureFetcher(),
                reviewer=AcceptingReviewer(),
                require_baseline=True,
            ).run("2026-09-01")
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual("partial", result.status)
        self.assertEqual("baseline_not_established", manifest["known_gaps"][0]["reason"])

    def test_withdrawn_security_advisory_is_audited_without_model_review(self) -> None:
        class WithdrawnSecurityFetcher:
            def fetch_security(self, updated_since: str) -> bytes:
                return json.dumps([{
                    "ghsa_id": "GHSA-aaaa-bbbb-cccc",
                    "html_url": "https://github.com/advisories/GHSA-aaaa-bbbb-cccc",
                    "summary": "Withdrawn advisory",
                    "description": "No longer applicable.",
                    "type": "reviewed",
                    "severity": "high",
                    "source_code_location": "https://github.com/modelcontextprotocol/modelcontextprotocol",
                    "published_at": "2026-08-31T00:00:00Z",
                    "updated_at": "2026-09-01T00:00:00Z",
                    "withdrawn_at": "2026-09-01T00:00:00Z",
                    "vulnerabilities": [],
                }]).encode("utf-8")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = ReleaseRecord(
                release_key="github-advisory@GHSA-aaaa-bbbb-cccc",
                source_id="github_advisories",
                repository="modelcontextprotocol/modelcontextprotocol",
                release_tag="GHSA-aaaa-bbbb-cccc",
                title="Original advisory",
                release_notes_html="Original high severity advisory.",
                release_notes_text="Original high severity advisory.",
                url="https://github.com/advisories/GHSA-aaaa-bbbb-cccc",
                published_at="2026-08-31T00:00:00Z",
                discovered_at="2026-08-31T01:00:00Z",
                raw_ref="data/raw/2026-08-31/github_advisories.json",
                item_type="security",
                metadata={"severity": "high", "updated_at": "2026-08-31T01:00:00Z"},
            )
            ReleaseLedger(root / "data" / "releases" / "release-ledger.jsonl").record(
                original, status="accepted", policy_version="1", reasons=[]
            )
            reviewer = AcceptingReviewer()
            result = AiNotesPipeline(
                root=root,
                sources=[source()],
                fetcher=FixtureFetcher(),
                reviewer=reviewer,
                security_fetcher=WithdrawnSecurityFetcher(),
            ).run("2026-09-01")
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            ledger = (root / "data" / "releases" / "release-ledger.jsonl").read_text(encoding="utf-8")

        self.assertEqual("success", result.status)
        self.assertEqual(1, reviewer.calls)
        self.assertEqual("GHSA-aaaa-bbbb-cccc", manifest["security_retractions"][0]["ghsa_id"])
        self.assertIn('"status": "withdrawn"', ledger)

    def test_raw_snapshots_older_than_thirty_days_are_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "data" / "raw" / "2026-07-01"
            recent = root / "data" / "raw" / "2026-08-15"
            old.mkdir(parents=True)
            recent.mkdir(parents=True)
            (old / "feed.xml").write_text("old", encoding="utf-8")
            (recent / "feed.xml").write_text("recent", encoding="utf-8")

            removed = prune_raw_snapshots(root, run_date="2026-09-01", retention_days=30)

            self.assertEqual(["2026-07-01"], removed)
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())

    def test_future_run_date_is_rejected_before_raw_snapshot_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "data" / "raw" / "2026-08-01" / "feed.xml"
            evidence.parent.mkdir(parents=True)
            evidence.write_text("evidence", encoding="utf-8")

            with self.assertRaises(ValueError):
                AiNotesPipeline(
                    root=root, sources=[source()], fetcher=FixtureFetcher(), reviewer=AcceptingReviewer()
                ).run("2099-01-01")

            self.assertTrue(evidence.exists())

    def test_concurrent_run_lock_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock_path = root / "data" / "releases" / "ai-notes.lock"
            lock_path.parent.mkdir(parents=True)
            lock_path.write_text("another process", encoding="utf-8")

            with self.assertRaises(RuntimeError):
                AiNotesPipeline(
                    root=root, sources=[source()], fetcher=FixtureFetcher(), reviewer=AcceptingReviewer()
                ).run("2026-09-01")

    def test_same_date_rerun_preserves_accepted_information_without_reviewing_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reviewer = AcceptingReviewer()
            pipeline = AiNotesPipeline(root=root, sources=[source()], fetcher=FixtureFetcher(), reviewer=reviewer)

            first = pipeline.run("2026-09-01")
            first_bytes = first.accepted_information_path.read_bytes()
            second = pipeline.run("2026-09-01")
            second_bytes = second.accepted_information_path.read_bytes()

        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(1, reviewer.calls)


if __name__ == "__main__":
    unittest.main()
