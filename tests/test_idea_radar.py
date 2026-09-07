import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/idea-radar"))
import radar_analysis as analysis
from radar import RadarStore


def event(mid, text, user="u1"):
    return {"sender": {"sender_type": "user", "sender_id": {"open_id": user}},
            "message": {"message_id": mid, "chat_id": "c1", "chat_type": "p2p", "message_type": "text", "content": json.dumps({"text": text})}}


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RadarStore(Path(self.temp.name) / "inbox.sqlite3")
        self.store.receive(event("pair", "配对 fixture"), "fixture")

    def test_discovery_followup_reset_and_duplicate_delivery(self):
        for mid, text in (("one", "找方向，两周能验证的工具"), ("two", "分析获客成本"),
                          ("three", "找小游戏，解谜方向"), ("three", "找小游戏，解谜方向")):
            self.store.receive(event(mid, text), "fixture")
        jobs = [self.store.claim() for _ in range(3)]
        self.assertIsNone(jobs[0]["parent_id"])
        self.assertEqual(jobs[1]["parent_id"], jobs[0]["id"])
        self.assertIsNone(jobs[2]["parent_id"])
        self.assertIsNone(self.store.claim())
        self.store.receive(event("reset", "新话题"), "fixture")
        self.store.receive(event("four", "我擅长Python，想做一个工具"), "fixture")
        self.assertIsNone(self.store.claim()["parent_id"])

    def test_new_public_link_starts_topic_and_other_user_is_ignored(self):
        self.store.receive(event("one", "https://example.com/product，分析这个方向"), "fixture")
        self.assertEqual(self.store.claim()["url"], "https://example.com/product")
        self.store.receive(event("two", "找方向", user="other"), "fixture")
        self.assertIsNone(self.store.claim())

    def test_public_url_guard_and_multiple_links(self):
        for value in ("http://127.0.0.1/a", "http://localhost/x", "http://10.0.0.1/a", "https://u:p@example.com", "http://foo.local/a", "https://example.com:22/x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                analysis.public_url(value)
        with self.assertRaises(ValueError):
            analysis.extract_url("https://example.com/a https://example.com/b")
        self.assertEqual(analysis.extract_url("github.com/a/b，介绍一下"), "https://github.com/a/b")

    def test_all_collectors_fail_is_failure_not_empty_success(self):
        with patch.object(analysis, "search", side_effect=RuntimeError("offline")), self.assertRaises(RuntimeError):
            analysis.collect("找方向", "radar:discovery")

    def test_partial_collection_preserves_excerpt_and_reports_failures(self):
        source = {"title": "Tool", "url": "https://example.com/tool", "text": "Users ask for exports", "kind": "search_excerpt"}
        with patch.object(analysis, "search", side_effect=[[source], RuntimeError(), []]), patch.object(analysis, "read_page", side_effect=RuntimeError()):
            bundle = analysis.collect("找方向", "radar:discovery")
        self.assertEqual(len(bundle["sources"]), 1)
        self.assertEqual(len(bundle["failures"]), 3)
        self.assertEqual(bundle["sources"][0]["kind"], "search_excerpt")

    def test_search_parses_all_results_and_passes_query_without_shell(self):
        text = "Title: One\nURL: https://example.com/1\nHighlights: First source\n\n---\n\nTitle: Two\nURL: https://example.com/2\nHighlights: Second source"
        result = subprocess.CompletedProcess([], 0, json.dumps({"content": [{"type": "text", "text": text}]}), "")
        query = '工具 " & echo secret'
        with patch.object(analysis.shutil, "which", return_value="C:/npm/mcporter.cmd"), patch.object(Path, "is_file", return_value=True), patch.object(analysis.subprocess, "run", return_value=result) as run:
            items = analysis.search(query)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "One")
        self.assertEqual(json.loads(run.call_args.args[0][5])["query"], query)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_feishu_secrets_are_excluded_from_model_environment(self):
        with patch.dict(os.environ, {"CHEMIST_FEISHU_APP_SECRET": "fixture", "RADAR_FEISHU_APP_SECRET": "fixture"}):
            self.assertNotIn("CHEMIST_FEISHU_APP_SECRET", analysis.clean_env())
            self.assertNotIn("RADAR_FEISHU_APP_SECRET", analysis.clean_env())

    def test_citation_validation_rejects_fabrication_and_missing_signal_evidence(self):
        bundle = {"sources": [{"id": "S1", "text": "Users ask for exports"}]}
        missing = {"status": "not_found", "claim": "未找到", "evidence": []}
        item = {"name": "Tool", "source_id": "S1", "problem": "导出", "audience": "个人", "basis": [{"source_id": "S1", "quote": "Users ask"}],
                "signals": {key: copy.deepcopy(missing) for key in ("discussion", "growth", "payment")},
                "china_hypothesis": "待验证", "validation": "访谈", "decision": "证据不足"}
        report = {"summary": "测试", "candidates": [item], "gaps": []}
        analysis.validate_report(report, bundle)
        item["basis"][0]["quote"] = "invented quote"
        with self.assertRaises(ValueError):
            analysis.validate_report(report, bundle)
        item["basis"][0]["quote"] = "Users ask"
        item["signals"]["payment"]["status"] = "source_claim"
        with self.assertRaises(ValueError):
            analysis.validate_report(report, bundle)

    def test_recovery_keeps_jobs_and_successful_parent(self):
        self.store.receive(event("one", "找方向"), "fixture")
        job = self.store.claim()
        self.store.recover()
        self.assertEqual(self.store.claim()["id"], job["id"])
        self.store.complete(job, {"queue_path": "evidence.json", "learning_root": ".", "report": "结果", "result_path": "report.json"})
        self.store.receive(event("two", "分析第二个"), "fixture")
        followup = self.store.claim()
        self.assertEqual(self.store.context_parent(followup)["id"], job["id"])


if __name__ == "__main__":
    unittest.main()
