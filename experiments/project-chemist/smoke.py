"""Exercise the installed Pi RPC loader and extension command, with no LLM call."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pi-cli", required=True, type=Path, help="Installed Pi dist/bundle/cli.js")
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    repo = here.parents[1]
    with tempfile.TemporaryDirectory(prefix="chemist-smoke-") as directory:
        env = {**os.environ, "PI_OFFLINE": "1", "PI_CODING_AGENT_DIR": str(Path(directory) / "config"),
               "CHEMIST_PYTHON": sys.executable, "CHEMIST_INPUT": str(here.parent / "cross-project-impact-v1/blind-input.json"),
               "CHEMIST_RUN_DIR": directory, "CHEMIST_RUN_ID": "smoke-no-model", "PYTHONPATH": str(repo / "src")}
        process = subprocess.Popen([
            "node", str(args.pi_cli.resolve()), "--mode", "rpc", "--no-session", "--no-approve",
            "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes", "--no-context-files",
            "--no-builtin-tools", "-e", str(here / "extension.ts"),
        ], cwd=directory, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8")
        messages = queue.Queue()

        def consume():
            for line in process.stdout:
                try: messages.put(json.loads(line))
                except ValueError: continue

        reader = threading.Thread(target=consume, daemon=True)
        reader.start()

        def send(payload):
            process.stdin.write(json.dumps(payload) + "\n")
            process.stdin.flush()

        try:
            send({"id": "commands", "type": "get_commands"})
            deadline = time.monotonic() + 40
            notified = responded = False
            while time.monotonic() < deadline:
                try: event = messages.get(timeout=1)
                except queue.Empty:
                    if process.poll() is not None: raise RuntimeError("Pi exited before smoke check completed")
                    continue
                if event.get("type") in {"agent_start", "message_start"}:
                    raise RuntimeError("Unexpected model turn in a no-model smoke test")
                if event.get("id") == "commands":
                    commands = event.get("data", {}).get("commands", [])
                    if not event.get("success") or not any(c["name"] == "chemist-status" for c in commands):
                        raise RuntimeError(f"Chemist extension failed to load: {event}")
                    send({"id": "status", "type": "prompt", "message": "/chemist-status"})
                if event.get("id") == "status":
                    if not event.get("success"): raise RuntimeError(f"Status command failed: {event}")
                    responded = True
                if event.get("type") == "extension_ui_request" and event.get("method") == "notify":
                    message = event.get("message", "")
                    expected = {"chemist_context", "chemist_inventory", "chemist_read", "chemist_search", "chemist_test_selectors", "chemist_submit"}
                    if "tools=" in message and set(message.split("tools=", 1)[1].split(",")) == expected:
                        notified = True
                if responded and notified:
                    print(json.dumps({"ok": True, "extension_loaded": True, "active_tools": 6, "model_calls": 0, "pinned_preflight": True}))
                    return 0
            raise RuntimeError("Timed out waiting for Pi extension status")
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdin.close()
            reader.join(timeout=2)
            process.stdout.close()


if __name__ == "__main__":
    raise SystemExit(main())
