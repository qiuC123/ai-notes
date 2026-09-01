from __future__ import annotations

import sys
import tempfile
import unittest
import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aihot.release_sources import BackfillResult, ReleaseRecord, ReleaseSource
from aihot.source_evaluation import HistoricalBacktester, build_trial_evaluation, write_trial_evaluation


class SourceEvaluationTests(unittest.TestCase):
    def test_trial_promotes_useful_reliable_source_and_keeps_low_sample_source_in_trial(self) -> None:
        report = build_trial_evaluation(
            source_ids=["useful", "low-sample", "unreliable"],
            fetch_days={"useful": 13, "low-sample": 14, "unreliable": 12},
            eligible_counts={"useful": 3, "low-sample": 2, "unreliable": 4},
            accepted_counts={"useful": 1, "low-sample": 0, "unreliable": 2},
            prerelease_leaks={"useful": 0, "low-sample": 0, "unreliable": 0},
            complete_evidence_counts={"useful": 1, "low-sample": 0, "unreliable": 2},
            max_latency_hours={"useful": 20.0, "low-sample": 2.0, "unreliable": 8.0},
            trial_days=14,
        )

        decisions = {item["source_id"]: item["decision"] for item in report["sources"]}
        self.assertEqual("promote_core", decisions["useful"])
        self.assertEqual("continue_trial", decisions["low-sample"])
        self.assertEqual("demote_watchlist", decisions["unreliable"])

    def test_trial_evaluation_rejects_non_fourteen_day_window(self) -> None:
        with self.assertRaises(ValueError):
            build_trial_evaluation(
                source_ids=[],
                fetch_days={},
                eligible_counts={},
                accepted_counts={},
                prerelease_leaks={},
                complete_evidence_counts={},
                max_latency_hours={},
                trial_days=1,
            )

    def test_historical_backtest_reviews_every_hard_filter_survivor_and_records_coverage(self) -> None:
        source = ReleaseSource(
            source_id="vllm",
            repository="vllm-project/vllm",
            category="local_inference",
            feed_url="https://github.com/vllm-project/vllm/releases.atom",
            accepted_tag_patterns=(r"v\d+\.\d+\.\d+",),
        )
        record = ReleaseRecord(
            release_key="vllm-project/vllm@v0.28.0",
            source_id="vllm",
            repository="vllm-project/vllm",
            release_tag="v0.28.0",
            title="v0.28.0",
            release_notes_html="<p>Added a new serving backend.</p>",
            release_notes_text="Added a new serving backend.",
            url="https://github.com/vllm-project/vllm/releases/tag/v0.28.0",
            published_at="2026-08-26T00:00:00Z",
            discovered_at="2026-09-01T00:00:00Z",
            raw_ref="data/backtests/vllm.json",
        )

        class Backfiller:
            def fetch_missing(self, *args: object, **kwargs: object) -> BackfillResult:
                return BackfillResult((record,), True, 1)

        class Reviewer:
            def review(self, records: list[ReleaseRecord]) -> dict[str, object]:
                self.count = len(records)
                return {"decisions": [{
                    "release_key": record.release_key,
                    "decision": "accept",
                    "change_types": ["feature"],
                    "substantive_changes": [{"summary_zh": "新增服务后端。", "evidence": "Added a new serving backend."}],
                    "decision_reason": "改变了部署能力。",
                    "pending_verification": [],
                }]}

        reviewer = Reviewer()
        with tempfile.TemporaryDirectory() as directory:
            result = HistoricalBacktester(
                root=Path(directory), sources=[source], backfiller=Backfiller(), reviewer=reviewer
            ).run(as_of="2026-09-01", days=90)

        self.assertEqual(1, reviewer.count)
        self.assertTrue(result["sources"][0]["coverage_complete"])
        self.assertEqual(1, result["sources"][0]["accepted_count"])

    def test_trial_evaluation_is_derived_from_manifests_and_ledger_and_writes_json_and_markdown(self) -> None:
        source = ReleaseSource(
            source_id="vllm",
            repository="vllm-project/vllm",
            category="local_inference",
            feed_url="https://github.com/vllm-project/vllm/releases.atom",
            accepted_tag_patterns=(r"v\d+\.\d+\.\d+",),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            start = date(2026, 8, 19)
            for offset in range(14):
                day = (start + timedelta(days=offset)).isoformat()
                output = root / "outputs" / day
                output.mkdir(parents=True)
                (output / "run-manifest.json").write_text(
                    json.dumps({
                        "successful_sources": ["vllm"],
                        "known_gaps": [{"source_id": "vllm"}] if offset == 0 else [],
                    }),
                    encoding="utf-8",
                )
            ledger_path = root / "data" / "releases" / "release-ledger.jsonl"
            ledger_path.parent.mkdir(parents=True)
            ledger_path.write_text("\n".join([
                json.dumps({
                    "release_key": f"vllm-project/vllm@v0.2.{index}",
                    "source_id": "vllm",
                    "repository": "vllm-project/vllm",
                    "release_tag": f"v0.2.{index}",
                    "published_at": "2026-08-20T00:00:00Z",
                    "discovered_at": "2026-08-20T12:00:00Z",
                    "status": "accepted" if index == 0 else "rejected",
                    "policy_version": "1",
                    "content_hash": str(index),
                    "decision": {
                        "substantive_changes": [{"evidence": "real quote"}] if index == 0 else []
                    },
                })
                for index in range(3)
            ]) + "\n", encoding="utf-8")

            report, json_path, markdown_path = write_trial_evaluation(
                root=root, sources=[source], start_date="2026-08-19", days=14
            )

        self.assertEqual("promote_core", report["sources"][0]["decision"])
        self.assertEqual(13, report["sources"][0]["metrics"]["fetch_days"])
        self.assertTrue(json_path.name.endswith(".json"))
        self.assertTrue(markdown_path.name.endswith(".md"))


if __name__ == "__main__":
    unittest.main()
