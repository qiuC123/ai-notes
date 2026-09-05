"""Run one bounded Pi blind evaluation over RPC; never send gold answers."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time


PROMPT = (
    "分析这次冻结输入里的五个案例。逐案查找双方代码证据和既有测试；不能确定就说明缺口。"
    "完成后通过 chemist_submit 提交报告，不要实施修改。"
    "这是一个全新的独立盲测会话；只使用本扩展提供的冻结输入和证据工具，不访问标准答案。"
)


def now():
    return datetime.now(UTC).isoformat()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pi-cli", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    output = Path(os.environ["CHEMIST_RUN_DIR"])
    if not output.is_absolute() or not output.is_dir() or not 30 <= args.timeout <= 1800:
        raise ValueError("Expected existing isolated run directory and 30..1800 second timeout")
    manifest = {
        "schema_version": "chemist-run.v1", "run_id": os.environ["CHEMIST_RUN_ID"],
        "started_at": now(), "status": "starting", "model": None,
        "timeout_seconds": args.timeout, "model_turns": 0, "tool_calls": 0, "tool_errors": 0,
        "prompt": PROMPT, "gold_sent": False, "context_discovery": False,
        "harness_sha256": {name: hashlib.sha256((here / name).read_bytes()).hexdigest()
                           for name in ("extension.ts", "worker.py", "run.py")},
    }
    with (output / "run-manifest.json").open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    command = ["node", str(args.pi_cli.resolve()), "--mode", "rpc", "--no-session", "--no-approve",
               "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes", "--no-context-files",
               "--no-builtin-tools", "-e", str(here / "extension.ts")]
    events = queue.Queue()
    process = subprocess.Popen(command, cwd=output, env=os.environ.copy(), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")

    def consume():
        for line in process.stdout:
            try: events.put(json.loads(line))
            except ValueError: continue

    reader = threading.Thread(target=consume, daemon=True)
    reader.start()

    def send(payload):
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()

    code = 1
    final_text = ""
    try:
        send({"id": "commands", "type": "get_commands"})
        send({"id": "state", "type": "get_state"})
        deadline = time.monotonic() + args.timeout
        have_model = ready_tools = started = False
        while time.monotonic() < deadline:
            try: event = events.get(timeout=1)
            except queue.Empty:
                if process.poll() is not None:
                    raise RuntimeError("Pi exited before settling")
                continue
            kind = event.get("type")
            if kind == "response" and event.get("success") is False:
                raise RuntimeError(f"Pi RPC {event.get('command')} failed: {event.get('error', 'unknown error')}")
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
                expected = {"chemist_context", "chemist_inventory", "chemist_read", "chemist_search", "chemist_test_selectors", "chemist_submit"}
                if "tools=" in message and set(message.split("tools=", 1)[1].split(",")) == expected:
                    ready_tools = True
            if ready_tools and have_model and not started:
                started = True
                manifest["status"] = "running"
                print(json.dumps({"event": "started", "model": manifest["model"]}), flush=True)
                send({"id": "analysis", "type": "prompt", "message": PROMPT})
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
                    manifest["last_model_error"] = message["errorMessage"]
            if kind == "agent_settled" and started:
                manifest["status"] = "sealed" if (output / "baseline.json").is_file() else "incomplete"
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
        process.stdout.close()
        manifest["finished_at"] = now()
        manifest["pi_exit_code_after_shutdown"] = process.returncode
        if (output / "baseline.json").exists():
            manifest["baseline_file_sha256"] = hashlib.sha256((output / "baseline.json").read_bytes()).hexdigest()
        (output / "run-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (output / "final-response.txt").write_text(final_text, encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "run_dir": str(output),
                      "error": manifest.get("error") or manifest.get("last_model_error")}, ensure_ascii=False), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
