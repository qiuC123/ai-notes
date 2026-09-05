"""Run one bounded Pi blind evaluation over RPC; never send gold answers."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time


PROMPT = (
    "分析这次冻结输入里的五个案例。逐案查找双方代码证据和既有测试；不能确定就说明缺口。"
    "完成后通过 chemist_submit 提交报告，不要实施修改。"
    "这是一个全新的独立盲测会话；只使用本扩展提供的冻结输入和证据工具，不访问标准答案。"
)
LEARNING_PROMPT = (
    "学习冻结队列中的外部项目，并对照 Ai Notes 的项目理解与关联发现流程。"
    "只根据双方源码提出最多一个值得尝试的改进；找不到可靠关联就明确说明。"
    "给出证据、成本、不适用条件和最小实验建议，通过 chemist_submit 提交。不要实施建议。"
)


def now():
    return datetime.now(UTC).isoformat()


def safe_error(value):
    """Classify errors without retaining URLs, headers, payloads, or secrets."""
    raw = str(value)
    patterns = [("websocket_error", r"websocket"), ("timeout", r"timed?\s*out|timeout"),
                ("rate_limit", r"429|rate.?limit|quota|usage.limit"),
                ("authentication", r"401|403|unauthori[sz]ed|authentication"),
                ("network_error", r"fetch failed|ECONN|ENOTFOUND|socket|network|connection"),
                ("budget_exhausted", r"budget"), ("runtime_error", r"Pi exited|extension|configured model")]
    code = next((name for name, pattern in patterns if re.search(pattern, raw, re.I)), "unclassified_error")
    return {"code": code, "message_sha256": hashlib.sha256(raw.encode()).hexdigest(), "message_chars": len(raw)}


def event_summary(event):
    kind = event.get("type")
    result = {"event": kind}
    if kind in {"auto_retry_start", "auto_retry_end", "summarization_retry_scheduled", "summarization_retry_attempt_start", "summarization_retry_finished"}:
        for key in ("attempt", "maxAttempts", "delayMs", "success"):
            if type(event.get(key)) in {int, float, bool}:
                result[key] = event[key]
        for key in ("errorMessage", "finalError"):
            if event.get(key): result["error"] = safe_error(event[key])
    if kind == "agent_end": result["willRetry"] = bool(event.get("willRetry"))
    if kind in {"tool_execution_start", "tool_execution_end"}:
        name = event.get("toolName", "")
        result["tool"] = name if name in {"chemist_context", "chemist_inventory", "chemist_read", "chemist_search", "chemist_test_selectors", "chemist_submit"} else "other"
        result["isError"] = bool(event.get("isError"))
    if kind == "message_end":
        message = event.get("message", {})
        result["role"] = message.get("role") if message.get("role") in {"assistant", "user", "toolResult"} else "other"
        if message.get("errorMessage"): result["error"] = safe_error(message["errorMessage"])
        reason = message.get("stopReason")
        if reason in {"stop", "length", "toolUse", "error", "aborted", "pending"}: result["stop_reason"] = reason
    if kind in {"extension_error", "reader_error", "stderr"}: result["error"] = safe_error(event.get("error", ""))
    return result


def write_manifest(path, manifest):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pi-cli", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--transport", choices=("sse", "auto"), default="sse")
    parser.add_argument("--profile", choices=("impact", "learning"), default="impact")
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    learning = args.profile == "learning"
    extension_name = "learning-extension.ts" if learning else "extension.ts"
    worker_name = "learning_worker.py" if learning else "worker.py"
    result_name = "decisions.json" if learning else "baseline.json"
    prompt = LEARNING_PROMPT if learning else PROMPT
    expected_tools = {"chemist_context", "chemist_read", "chemist_submit"} if learning else {
        "chemist_context", "chemist_inventory", "chemist_read", "chemist_search", "chemist_test_selectors", "chemist_submit"}
    output = Path(os.environ["CHEMIST_RUN_DIR"])
    if not output.is_absolute() or not output.is_dir() or not 30 <= args.timeout <= 1800:
        raise ValueError("Expected existing isolated run directory and 30..1800 second timeout")
    manifest = {
        "schema_version": "chemist-run.v1", "run_id": os.environ["CHEMIST_RUN_ID"],
        "started_at": now(), "status": "starting", "model": None,
        "timeout_seconds": args.timeout, "model_turns": 0, "tool_calls": 0, "tool_errors": 0,
        "transport": args.transport, "http_idle_timeout_ms": 120000, "retry_starts": 0,
        "retry_ends": 0, "model_errors": 0, "stderr_events": 0, "last_event_at": None,
        "metadata_source": "runtime", "repository_read_method": "git cat-file",
        "profile": args.profile,
        "prompt": prompt, "gold_sent": False, "context_discovery": False,
        "harness_sha256": {name: hashlib.sha256((here / name).read_bytes()).hexdigest()
                           for name in (extension_name, worker_name, "run.py")},
    }
    if learning:
        manifest["repository_read_method"] = "frozen external queue and fingerprint-checked owned files"
        manifest["queue_file_sha256"] = hashlib.sha256(Path(os.environ["CHEMIST_INPUT"]).read_bytes()).hexdigest()
    with (output / "run-manifest.json").open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    # Trust only this newly generated, isolated project config; never a sample repo.
    config = output / ".pi"
    config.mkdir(exist_ok=False)
    with (config / "settings.json").open("x", encoding="utf-8") as handle:
        json.dump({"transport": args.transport, "httpIdleTimeoutMs": 120000,
                   "retry": {"enabled": True, "maxRetries": 3, "baseDelayMs": 2000,
                             "provider": {"maxRetries": 0, "timeoutMs": 120000, "maxRetryDelayMs": 60000}}}, handle)
    command = ["node", str(args.pi_cli.resolve()), "--mode", "rpc", "--no-session", "--approve",
               "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes", "--no-context-files",
               "--no-builtin-tools", "-e", str(here / extension_name)]
    events = queue.Queue()
    process = subprocess.Popen(command, cwd=output, env=os.environ.copy(), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")

    def consume():
        try:
            for line in process.stdout:
                try: events.put(json.loads(line))
                except ValueError: events.put({"type": "reader_error", "error": "Invalid RPC JSON"})
        except (OSError, UnicodeError) as error:
            events.put({"type": "reader_error", "error": str(error)})
        finally: events.put({"type": "stdout_closed"})

    def consume_stderr():
        for chunk in iter(lambda: process.stderr.readline(8192), ""):
            events.put({"type": "stderr", "error": chunk})

    reader = threading.Thread(target=consume, daemon=True)
    reader.start()
    error_reader = threading.Thread(target=consume_stderr, daemon=True)
    error_reader.start()

    def send(payload):
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()

    code = 1
    final_text = ""
    try:
        send({"id": "commands", "type": "get_commands"})
        send({"id": "state", "type": "get_state"})
        deadline = time.monotonic() + args.timeout
        last_received = last_saved = last_heartbeat = time.monotonic()
        have_model = ready_tools = started = False
        while time.monotonic() < deadline:
            tick = time.monotonic()
            if tick - last_heartbeat >= 15:
                print(json.dumps({"event": "heartbeat", "phase": manifest.get("last_event"),
                                  "quiet_seconds": round(tick - last_received), "tools": manifest["tool_calls"],
                                  "retries": manifest["retry_starts"], "model_errors": manifest["model_errors"]}), flush=True)
                write_manifest(output / "run-manifest.json", manifest)
                last_heartbeat = tick
            try: event = events.get(timeout=1)
            except queue.Empty:
                if process.poll() is not None:
                    raise RuntimeError("Pi exited before settling")
                continue
            kind = event.get("type")
            if kind == "stdout_closed": raise RuntimeError("Pi exited before settling")
            last_received = time.monotonic()
            manifest.update(last_event_at=now(), last_event=kind)
            if kind not in {"message_update", "tool_execution_update", "extension_ui_request", "message_start"}:
                summary = event_summary(event)
                summary["at"] = manifest["last_event_at"]
                with (output / "events.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(summary) + "\n")
            if kind in {"auto_retry_start", "auto_retry_end", "extension_error", "stderr", "reader_error"}:
                if kind == "auto_retry_start": manifest["retry_starts"] += 1
                if kind == "auto_retry_end": manifest["retry_ends"] += 1
                if kind == "stderr": manifest["stderr_events"] += 1
                print(json.dumps(event_summary(event)), flush=True)
            if time.monotonic() - last_saved >= 2:
                write_manifest(output / "run-manifest.json", manifest)
                last_saved = time.monotonic()
            if kind == "response" and event.get("success") is False:
                raise RuntimeError(f"Pi RPC failed: {safe_error(event.get('error', 'unknown error'))['code']}")
            if event.get("id") == "commands":
                if not any(c["name"] == "chemist-status" for c in event.get("data", {}).get("commands", [])):
                    raise RuntimeError("Chemist extension not loaded")
                send({"id": "status", "type": "prompt", "message": "/chemist-status"})
            if event.get("id") == "state":
                data = event.get("data", {})
                model = data.get("model") or {}
                if not model.get("id"):
                    raise RuntimeError("Pi has no configured model")
                manifest["model"] = {"id": model["id"], "provider": model.get("provider"), "thinking_level": data.get("thinkingLevel")}
                have_model = True
            if kind == "extension_ui_request" and event.get("method") == "notify":
                message = event.get("message", "")
                if "tools=" in message and set(message.split("tools=", 1)[1].split(",")) == expected_tools:
                    ready_tools = True
            if ready_tools and have_model and not started:
                started = True
                manifest["status"] = "running"
                print(json.dumps({"event": "started", "model": manifest["model"]}), flush=True)
                send({"id": "analysis", "type": "prompt", "message": prompt})
            if kind == "turn_start":
                manifest["model_turns"] += 1
            if kind == "tool_execution_end":
                manifest["tool_calls"] += 1
                manifest["tool_errors"] += int(bool(event.get("isError")))
                print(json.dumps({"event": "tool", "name": event.get("toolName"),
                                  "count": manifest["tool_calls"], "error": bool(event.get("isError"))}), flush=True)
                if manifest["tool_calls"] >= 120:
                    raise RuntimeError("Tool-call budget reached")
            if kind == "message_end" and event.get("message", {}).get("role") == "assistant":
                message = event["message"]
                final_text = "\n".join(c.get("text", "") for c in message.get("content", []) if c.get("type") == "text")
                if message.get("errorMessage"):
                    manifest["model_errors"] += 1
                    manifest["last_model_error"] = safe_error(message["errorMessage"])
                    manifest["last_model_error_at"] = now()
                    print(json.dumps({"event": "model_error", "at": manifest["last_model_error_at"], "error": manifest["last_model_error"]}), flush=True)
            if kind == "auto_retry_end" and event.get("success") is False:
                raise RuntimeError("Pi exhausted automatic retries")
            if kind in {"extension_error", "reader_error"}:
                raise RuntimeError("Pi extension or RPC reader failed")
            if kind == "agent_settled" and started:
                manifest["status"] = "sealed" if (output / result_name).is_file() else "incomplete"
                code = 0 if manifest["status"] == "sealed" else 2
                break
        else:
            raise TimeoutError("Bounded blind run timed out")
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        manifest.update(status="failed", error=str(error))
    finally:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdin.close()
        reader.join(timeout=2)
        error_reader.join(timeout=2)
        process.stdout.close()
        process.stderr.close()
        manifest["finished_at"] = now()
        manifest["pi_exit_code_after_shutdown"] = process.returncode
        if (output / result_name).exists():
            hash_key = "decisions_file_sha256" if learning else "baseline_file_sha256"
            manifest[hash_key] = hashlib.sha256((output / result_name).read_bytes()).hexdigest()
        write_manifest(output / "run-manifest.json", manifest)
        (output / "final-response.txt").write_text(final_text, encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "run_dir": str(output),
                      "error": manifest.get("error") or manifest.get("last_model_error")}, ensure_ascii=False), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
