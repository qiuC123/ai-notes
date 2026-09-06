from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("chemist_run", ROOT / "experiments/project-chemist/run.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class FakePi:
    def __init__(self, events):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO("".join(json.dumps(e) + "\n" for e in events))
        self.stderr = io.StringIO("")
        self.returncode = None
        self.sent = ""

    def poll(self): return self.returncode
    def wait(self, timeout): return self.returncode
    def terminate(self):
        self.sent = self.stdin.getvalue()
        self.returncode = -15


def startup():
    return [
        {"type": "response", "id": "commands", "success": True, "data": {"commands": [{"name": "chemist-status"}]}},
        {"type": "response", "id": "state", "success": True, "data": {"model": {"id": "fixture", "provider": "no-network"}, "thinkingLevel": "high"}},
        {"type": "extension_ui_request", "method": "notify", "message": "tools=chemist_context,chemist_inventory,chemist_read,chemist_search,chemist_test_selectors,chemist_submit"},
    ]


class ChemistRunnerTests(unittest.TestCase):
    def run_fake(self, events, *, sealed=False, profile="impact", request=None, persistent=False, wrong_session=False):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            if sealed:
                (output / ("decisions.json" if profile == "learning" else "baseline.json")).write_text('{"fixture": true}', encoding="utf-8")
            queue_path = output / "queue.json"
            queue_path.write_text('{}', encoding="utf-8")
            process = FakePi(events)
            request_args = []
            if request is not None:
                request_path = output / "request.json"
                request_path.write_text(json.dumps(request), encoding="utf-8")
                request_args = ["--request-file", str(request_path)]
            if persistent:
                session_path = output / "session.jsonl"
                session_path.write_text('{"type":"session","id":"fixture"}\n', encoding="utf-8")
                request_args += ["--session-file", str(session_path)]
                copied = json.loads(json.dumps(events))
                for event in copied:
                    if event.get("id") == "state":
                        event["data"].update(sessionFile=str(output / "wrong.jsonl") if wrong_session else str(session_path),
                                             sessionId="fixture", messageCount=12)
                process = FakePi(copied)
            with patch.dict(os.environ, {"CHEMIST_RUN_DIR": directory, "CHEMIST_RUN_ID": "fixture", "CHEMIST_INPUT": str(queue_path)}), \
                 patch.object(sys, "argv", ["run.py", "--pi-cli", str(output / "pi.js"), "--profile", profile] + request_args), \
                 patch.object(runner.subprocess, "Popen", return_value=process) as popen, \
                 patch("builtins.print"):
                code = runner.main()
            manifest = json.loads((output / "run-manifest.json").read_text(encoding="utf-8"))
            return code, manifest, process.sent, popen.call_args

    def test_mobile_request_is_bound_and_only_allowed_for_learning(self):
        request = {"question": "mobile-question", "previous_discussion": "earlier-discussion"}
        events = startup()
        events[-1]["message"] = "tools=chemist_context,chemist_read,chemist_submit"
        code, manifest, sent, _ = self.run_fake(events + [{"type": "agent_settled"}], sealed=True,
                                               profile="learning", request=request)
        self.assertEqual(code, 0)
        self.assertIn("request_file_sha256", manifest)
        self.assertIn("mobile-question", sent)
        self.assertIn("earlier-discussion", sent)
        with self.assertRaises(ValueError):
            self.run_fake(events, request=request)
        with self.assertRaises(ValueError):
            self.run_fake(events, profile="learning", request={"question": []})

    def test_learning_profile_uses_new_report_and_three_tools(self):
        events = startup()
        events[-1]["message"] = "tools=chemist_context,chemist_read,chemist_submit"
        code, manifest, sent, call = self.run_fake(events + [{"type": "agent_settled"}], sealed=True, profile="learning")
        self.assertEqual(code, 0)
        self.assertIn("decisions_file_sha256", manifest)
        self.assertEqual(manifest["profile"], "learning")
        self.assertIn("最多一个", sent)
        self.assertTrue(call.args[0][-1].endswith("learning-extension.ts"))

    def test_sealed_result_and_restricted_startup(self):
        events = startup() + [{"type": "turn_start"}, {"type": "tool_execution_end", "toolName": "chemist_submit", "isError": False}, {"type": "agent_settled"}]
        code, manifest, sent, call = self.run_fake(events, sealed=True)
        self.assertEqual(code, 0)
        self.assertEqual(manifest["status"], "sealed")
        self.assertEqual(manifest["model_turns"], 1)
        self.assertEqual(manifest["tool_calls"], 1)
        self.assertIn('"id": "analysis"', sent)
        self.assertIn("--no-context-files", call.args[0])
        self.assertIn("--no-builtin-tools", call.args[0])
        self.assertNotIn("--model", call.args[0])
        self.assertFalse(manifest["gold_sent"])
        self.assertIn("--no-session", call.args[0])

    def test_persistent_mobile_session_is_bound_and_blind_runs_stay_ephemeral(self):
        events = startup()
        events[-1]["message"] = "tools=chemist_context,chemist_read,chemist_submit"
        request = {"question": "continue", "previous_discussion": ""}
        code, manifest, _, call = self.run_fake(events + [{"type": "agent_settled"}], sealed=True,
                                                profile="learning", request=request, persistent=True)
        self.assertEqual(code, 0)
        self.assertEqual(manifest["session"]["previous_message_count"], 12)
        self.assertIn("--session", call.args[0])
        self.assertNotIn("--no-session", call.args[0])
        code, _, sent, _ = self.run_fake(events, profile="learning", request=request,
                                        persistent=True, wrong_session=True)
        self.assertEqual(code, 1)
        self.assertNotIn('"id": "analysis"', sent)
        with self.assertRaises(ValueError):
            self.run_fake(events, persistent=True)

    def test_settled_without_report_is_incomplete(self):
        code, manifest, _, _ = self.run_fake(startup() + [{"type": "agent_settled"}])
        self.assertEqual(code, 2)
        self.assertEqual(manifest["status"], "incomplete")

    def test_missing_extension_never_starts_analysis(self):
        code, manifest, sent, _ = self.run_fake([{"type": "response", "id": "commands", "success": True, "data": {"commands": []}}])
        self.assertEqual(code, 1)
        self.assertEqual(manifest["status"], "failed")
        self.assertNotIn('"id": "analysis"', sent)

    def test_missing_model_never_starts_analysis(self):
        code, manifest, sent, _ = self.run_fake([{"type": "response", "id": "state", "success": True, "data": {}}])
        self.assertEqual(code, 1)
        self.assertIn("no configured model", manifest["error"])
        self.assertNotIn('"id": "analysis"', sent)

    def test_provider_error_is_not_success(self):
        events = startup() + [{"type": "message_end", "message": {"role": "assistant", "errorMessage": "fixture provider failure"}}, {"type": "agent_settled"}]
        code, manifest, _, _ = self.run_fake(events)
        self.assertEqual(code, 2)
        self.assertEqual(manifest["last_model_error"]["code"], "unclassified_error")
        self.assertEqual(manifest["model_errors"], 1)
        self.assertIn("last_model_error_at", manifest)

    def test_error_summary_never_stores_credentials_or_body(self):
        secret = "Bearer sk-fake-secret https://user:password@private.invalid/path?token=private"
        event = {"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3, "delayMs": 2000,
                 "errorMessage": "WebSocket error " + secret, "body": secret, "headers": {"Authorization": secret}}
        summary = runner.event_summary(event)
        self.assertEqual(summary["error"]["code"], "websocket_error")
        self.assertEqual(summary["attempt"], 1)
        self.assertNotIn(secret, json.dumps(summary))
        self.assertNotIn("password", json.dumps(summary))
        self.assertNotIn("headers", summary)
        self.assertNotIn("body", summary)

    def test_retry_success_is_logged_but_not_counted_as_sealed(self):
        events = startup() + [{"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3, "delayMs": 2000, "errorMessage": "WebSocket error"},
                              {"type": "auto_retry_end", "attempt": 1, "success": True}, {"type": "agent_settled"}]
        code, manifest, _, _ = self.run_fake(events)
        self.assertEqual(code, 2)
        self.assertEqual(manifest["retry_starts"], 1)
        self.assertEqual(manifest["retry_ends"], 1)
        self.assertEqual(manifest["transport"], "sse")

    def test_final_retry_failure_stops_without_waiting_for_global_timeout(self):
        code, manifest, _, _ = self.run_fake(startup() + [{"type": "auto_retry_end", "attempt": 3, "success": False, "finalError": "timeout"}])
        self.assertEqual(code, 1)
        self.assertIn("exhausted automatic retries", manifest["error"])

    def test_closed_rpc_stream_fails_explicitly(self):
        code, manifest, _, _ = self.run_fake(startup())
        self.assertEqual(code, 1)
        self.assertIn("Pi exited", manifest["error"])


if __name__ == "__main__":
    unittest.main()
