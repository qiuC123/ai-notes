import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import subprocess
import os
from unittest.mock import patch

from test_learning_review import ROOT, decisions, write_prepared_run

sys.path.insert(0, str(ROOT / "experiments/project-chemist"))
import mobile_core as core
import mobile_analysis as analysis
import mobile


def event(mid="m1", text="https://github.com/example/project", user="u1", chat="c1", kind="p2p"):
    return {"sender": {"sender_type": "user", "sender_id": {"open_id": user}},
            "message": {"message_id": mid, "chat_id": chat, "chat_type": kind,
                        "message_type": "text", "content": json.dumps({"text": text})}}


class MobileQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.store = core.Store(self.path)
        self.code = "abcd1234"

    def pair(self):
        self.store.receive(event("pair", "配对 " + self.code), self.code)

    def test_requires_pairing_and_ignores_other_people_groups_and_bots(self):
        self.store.receive(event(), self.code)
        self.assertEqual(self.store.status()["jobs"], {})
        self.pair()
        self.store.receive(event(user="stranger"), self.code)
        self.store.receive(event(kind="group"), self.code)
        bot = event()
        bot["sender"]["sender_type"] = "app"
        self.store.receive(bot, self.code)
        self.assertEqual(self.store.status()["jobs"], {})
        self.store.receive(event(), self.code)
        self.assertEqual(self.store.status()["jobs"], {"queued": 1})

    def test_concurrent_event_redelivery_is_exactly_one_job(self):
        self.pair()
        threads = [threading.Thread(target=self.store.receive, args=(event(), self.code)) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(self.store.status()["jobs"], {"queued": 1})
        self.assertEqual(self.store.status()["pending_replies"], 2)

    def test_followup_binds_at_receipt_and_new_link_starts_new_context(self):
        self.pair()
        for mid, text in (("one", "https://github.com/example/one"), ("two", "解释成本"),
                          ("three", "https://github.com/example/two"), ("four", "如何使用")):
            self.store.receive(event(mid, text), self.code)
        jobs = [self.store.claim() for _ in range(4)]
        self.assertEqual(jobs[1]["parent_id"], jobs[0]["id"])
        self.assertEqual(jobs[1]["url"], jobs[0]["url"])
        self.assertIsNone(jobs[2]["parent_id"])
        self.assertEqual(jobs[3]["parent_id"], jobs[2]["id"])

    def test_chat_context_is_not_shared_and_invalid_urls_never_run(self):
        self.pair()
        for i, text in enumerate(("解释成本", "https://example.com/x", "https://github.com/a/b https://github.com/a/c",
                                  "https://github.com:443/a/b", "a" * 1801)):
            self.store.receive(event(str(i), text), self.code)
        self.assertEqual(self.store.status()["jobs"], {})
        self.store.receive(event("ok"), self.code)
        self.store.receive(event("other", "继续", chat="c2"), self.code)
        self.assertEqual(self.store.status()["jobs"], {"queued": 1})

    def test_bare_link_with_chinese_question_starts_analysis_and_followup(self):
        self.pair()
        self.store.receive(event("bare", "github.com/gastownhall/gastown，介绍一下这个项目"), self.code)
        self.store.receive(event("follow", "核心机制呢？"), self.code)
        first, second = self.store.claim(), self.store.claim()
        self.assertEqual(first["url"], "https://github.com/gastownhall/gastown")
        self.assertIn("介绍一下", first["question"])
        self.assertEqual(second["parent_id"], first["id"])

    def test_link_wrappers_repeated_display_links_and_host_boundaries(self):
        expected = "https://github.com/gastownhall/gastown"
        for text in ("github.com/gastownhall/gastown", "看看github.com/gastownhall/gastown，介绍一下",
                     "（github.com/gastownhall/gastown）", "www.github.com/gastownhall/gastown",
                     "http://github.com/gastownhall/gastown", "[项目](" + expected + ")",
                     "[github.com/gastownhall/gastown](" + expected + ")"):
            with self.subTest(text=text):
                self.assertEqual(core.project_url(text), expected)
        for text in ("https://evil.test/github.com/a/b", "https://github.com.evil.test/a/b",
                     "https://evil.test/?next=github.com/a/b", "github.com/a/b github.com/a/c"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                core.project_url(text)
        for text in ("evilgithub.com/a/b", "https://evil.test@github.com/a/b"):
            with self.subTest(text=text):
                if text.startswith("https:"):
                    with self.assertRaises(ValueError): core.project_url(text)
                else:
                    self.assertIsNone(core.project_url(text))

    def test_restart_recovers_jobs_and_persisted_outbox_without_replaying_finished_job(self):
        self.pair()
        self.store.receive(event(), self.code)
        job = self.store.claim()
        fresh = core.Store(self.path)
        fresh.recover()
        self.assertEqual(fresh.claim()["id"], job["id"])
        fresh.complete(job, {"queue_path": "q", "learning_root": "r", "report": "完成", "result_path": "d"})
        fresh.recover()
        self.assertIsNone(fresh.claim())
        fresh.receive(event(), self.code)
        self.assertEqual(fresh.status()["jobs"], {"done": 1})

    def test_persistent_session_survives_restart_failure_and_topic_reset(self):
        self.pair()
        self.store.receive(event(), self.code)
        first = self.store.claim()
        self.store.complete(first, {"queue_path": "q", "learning_root": "r", "report": "ok",
                                    "result_path": "d", "session_file": "first.jsonl"})
        self.store = core.Store(self.path)
        self.store.receive(event("second", "继续"), self.code)
        second = self.store.claim()
        self.assertEqual(self.store.context_parent(second)["session_file"], "first.jsonl")
        self.store.fail(second, "interrupted")
        self.store.receive(event("third", "再试一次"), self.code)
        third = self.store.claim()
        self.assertEqual(self.store.context_parent(third)["id"], first["id"])
        self.store.receive(event("reset", "新话题"), self.code)
        # Completing an old in-flight job must not undo a topic reset.
        self.store.complete(third, {"queue_path": "q", "learning_root": "r", "report": "ok",
                                    "result_path": "d", "session_file": "third.jsonl"})
        self.store.receive(event("orphan", "继续"), self.code)
        self.assertIsNone(self.store.claim())
        self.store.receive(event("new-link"), self.code)
        self.assertIsNone(self.store.claim()["parent_id"])

    def test_existing_database_migration_preserves_jobs(self):
        self.pair()
        self.store.receive(event(), self.code)
        with self.store.connect() as db:
            db.execute("ALTER TABLE jobs DROP COLUMN session_file")
        fresh = core.Store(self.path)
        job = fresh.claim()
        self.assertEqual(job["message_id"], "m1")
        self.assertIsNone(job["session_file"])

    def test_send_retry_keeps_uuid_and_preserves_chunk_order(self):
        with self.store.connect() as db:
            self.store.reply(db, "x", "文" * 4000, "result")
        first = self.store.pending_reply()
        self.store.delivery_failed(first)
        self.assertIsNone(self.store.pending_reply())
        with patch.object(core.time, "time", return_value=first["created"] + 400):
            self.assertEqual(self.store.pending_reply()["id"], first["id"])
        self.store.delivered(first["id"])
        self.assertTrue(self.store.pending_reply()["text"].startswith("(2/3)"))
        self.assertLess(len(first["text"].encode()), 10000)

    def test_failure_is_visible_and_does_not_block_next_job(self):
        self.pair()
        self.store.receive(event(), self.code)
        self.store.receive(event("next", "https://github.com/a/c"), self.code)
        job = self.store.claim()
        self.store.fail(job, "失败")
        self.assertEqual(self.store.get(job["id"])["status"], "failed")
        self.assertIsNotNone(self.store.claim())

    def test_single_instance_lock_releases_without_deleting_file(self):
        lock = self.path.parent / "service.lock"
        with mobile.single_instance(lock):
            with self.assertRaises(RuntimeError):
                with mobile.single_instance(lock): pass
        with mobile.single_instance(lock): pass

    def test_sender_retries_transport_failure_without_rerunning_analysis(self):
        self.pair()
        stop = threading.Event()
        class Api:
            def send(self, item):
                stop.set()
                raise OSError("fixture timeout")
        mobile.sender(self.store, Api(), stop)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT attempts FROM outbox").fetchone()[0], 1)
        self.assertEqual(self.store.status()["jobs"], {})

    def test_exhausted_delivery_is_visible_and_next_message_can_proceed(self):
        self.pair()
        first = self.store.pending_reply()
        for attempt in range(12):
            self.store.delivery_failed(dict(first, attempts=attempt))
        self.assertEqual(self.store.status()["failed_replies"], 1)
        self.store.receive(event("help", "帮助"), self.code)
        self.assertNotEqual(self.store.pending_reply()["id"], first["id"])

    def test_analysis_process_is_terminated_when_containment_closes(self):
        marker = self.path.parent / "finished.txt"
        process = subprocess.Popen([sys.executable, "-c", "import time,sys; from pathlib import Path; time.sleep(60); Path(sys.argv[1]).write_text('finished')", str(marker)],
                                   start_new_session=os.name != "nt")
        try:
            with mobile.process_tree(process):
                self.assertIsNone(process.poll())
            process.wait(timeout=10)
            self.assertFalse(marker.exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)


class MobileAnalysisTests(unittest.TestCase):
    def test_real_sdk_dispatch_and_reply_payload_without_network(self):
        import lark_oapi as lark
        api = mobile.Feishu("cli_fixture", "fixture-secret")
        with tempfile.TemporaryDirectory() as directory:
            store = core.Store(Path(directory) / "state.sqlite3")
            payload = {"schema": "2.0", "header": {"event_type": "im.message.receive_v1", "event_id": "e1"},
                       "event": event("pair", "配对 code")}
            class FakeConnection:
                def __init__(self, *args, event_handler, **kwargs):
                    self.handler = event_handler
                def start(self):
                    self.handler._do_without_validation(json.dumps(payload).encode())
            with patch.object(lark.ws, "Client", FakeConnection):
                api.listen(store, "code")
            self.assertTrue(store.status()["paired"])
            item = store.pending_reply()
            with patch.object(api.client.im.v1.message, "reply") as reply:
                reply.return_value.success.return_value = True
                api.send(item)
                request = reply.call_args.args[0]
                self.assertEqual(request.message_id, "pair")
                self.assertEqual(request.request_body.uuid, item["id"])
                self.assertIn("配对成功", json.loads(request.request_body.content)["text"])

    def test_sampling_is_bounded_and_omits_test_vendor_and_traversal_paths(self):
        queue = {"external_evidence": [{"kind": "tree", "text": "\n".join([
            "tests/test_x.py", "vendor/a.py", "../secret.py", "/etc/test.py", "src/main.py",
            *[f"src/m{i}.py" for i in range(10)]])}]}
        selected = analysis.select_sources(queue)
        self.assertEqual(len(selected), 6)
        self.assertEqual(selected[0], "src/main.py")
        self.assertTrue(all(path.startswith("src/") for path in selected))

    def test_followup_reuses_frozen_queue_validates_report_and_excludes_feishu_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, queue_path = write_prepared_run(root)
            result = decisions(queue_path, project)
            result["connections"][0]["owned_project_problem"]["status"] = "inferred"
            parent = {"queue_path": str(queue_path), "learning_root": str(root), "report": "之前讨论"}
            job = {"id": "abc", "question": "成本？", "url": "https://github.com/a/b"}
            real_run = analysis.subprocess.run

            def fake_run(command, **kwargs):
                if "env" not in kwargs or "CHEMIST_RUN_DIR" not in kwargs["env"]:
                    return real_run(command, **kwargs)
                output = Path(kwargs["env"]["CHEMIST_RUN_DIR"])
                self.assertNotIn("CHEMIST_FEISHU_APP_SECRET", kwargs["env"])
                request = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
                self.assertEqual(request["previous_discussion"], "之前讨论")
                (output / "decisions.json").write_text(json.dumps(result), encoding="utf-8")
                session = Path(command[command.index("--session-file") + 1])
                session.write_text('{"type":"session","id":"fixture"}\n'
                                   '{"type":"message","message":{"role":"assistant"}}\n', encoding="utf-8")
                return type("Completed", (), {"returncode": 0})()

            with patch("learning_worker.OWNED_PATHS", ("README.md",)), \
                 patch.object(analysis, "pi_cli", return_value=root / "pi.js"), \
                 patch.object(analysis.subprocess, "run", side_effect=fake_run), \
                 patch.object(analysis, "prepare_learning") as prepare, \
                 patch.dict(analysis.os.environ, {"CHEMIST_FEISHU_APP_SECRET": "fixture-secret"}):
                response = analysis.analyze(job, parent, root / "state")
            prepare.assert_not_called()
            self.assertIn("Pi 初步分析", response["report"])
            self.assertIn("成本？", response["report"])
            self.assertIn("最多500项", response["report"])
            self.assertEqual(response["queue_path"], str(queue_path))
            self.assertFalse((root / "data/learning/ledger.jsonl").exists())

    def test_session_checkpoint_is_copied_and_failed_attempt_cannot_change_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, queue_path = write_prepared_run(root)
            state = root / "state"
            old = state / "runs" / "old" / "session.jsonl"
            old.parent.mkdir(parents=True)
            original = ('{"type":"session","id":"fixture"}\n'
                        '{"type":"message","message":{"role":"assistant","content":"remember-42"}}\n')
            old.write_text(original, encoding="utf-8")
            parent = {"queue_path": str(queue_path), "learning_root": str(root),
                      "report": "should not be injected", "session_file": str(old)}
            job = {"id": "next", "question": "之前的数字？", "url": "https://github.com/a/b"}
            real_run = analysis.subprocess.run

            def failed_run(command, **kwargs):
                if "env" not in kwargs or "CHEMIST_RUN_DIR" not in kwargs["env"]:
                    return real_run(command, **kwargs)
                session = Path(command[command.index("--session-file") + 1])
                self.assertNotEqual(session, old)
                self.assertEqual(session.read_text(encoding="utf-8"), original)
                request = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
                self.assertEqual(request["previous_discussion"], "")
                session.write_text(original + '{"unfinished":true}\n', encoding="utf-8")
                return type("Completed", (), {"returncode": 1})()

            with patch("learning_worker.OWNED_PATHS", ("README.md",)), \
                 patch.object(analysis, "pi_cli", return_value=root / "pi.js"), \
                 patch.object(analysis.subprocess, "run", side_effect=failed_run):
                with self.assertRaises(RuntimeError):
                    analysis.analyze(job, parent, state)
            self.assertEqual(old.read_text(encoding="utf-8"), original)
            old.write_text(original + '{truncated', encoding="utf-8")
            with self.assertRaises(ValueError):
                analysis.validate_session(old)
