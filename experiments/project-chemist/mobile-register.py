"""Create a separate Feishu app using the official device flow; never print secrets."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import lark_oapi as lark

from ai_notes.storage import write_json_atomic

STATE = Path(__file__).resolve().parents[2] / "work/mobile-chemist"


def main():
    credential = STATE / "feishu-credential.xml"
    if credential.exists():
        raise RuntimeError("Credentials already configured; refusing to create another app")
    STATE.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    timer = threading.Timer(600, stop.set)
    timer.daemon = True
    timer.start()

    def ready(info):
        write_json_atomic(STATE / "registration.json", {"status": "waiting_for_user", "url": info["url"],
                                                       "created": time.time(), "expire_in": info["expire_in"]})
        print("Registration link saved in work/mobile-chemist/registration.json", flush=True)

    try:
        result = lark.register_app(
            on_qr_code=ready, cancel_event=stop, source="ai-notes-mobile",
            app_preset={"name": "项目共学助手", "desc": "手机发送 GitHub 链接，本机返回只读项目共学分析"},
            addons={"preset": False,
                    "scopes": {"tenant": ["im:message.p2p_msg:readonly", "im:message:send_as_bot"]},
                    "events": {"items": {"tenant": ["im.message.receive_v1"]}}},
            create_only=True)
        # Pass secrets only on stdin, never in a command line or unencrypted file.
        script = ("$ErrorActionPreference='Stop'; "
                  "$c=[Console]::In.ReadToEnd() | ConvertFrom-Json; "
                  "$s=ConvertTo-SecureString $c.client_secret -AsPlainText -Force; "
                  "$p=[System.Management.Automation.PSCredential]::new($c.client_id,$s); "
                  "$p | Export-Clixml -LiteralPath $env:CHEMIST_CREDENTIAL_TARGET")
        env = dict(os.environ, CHEMIST_CREDENTIAL_TARGET=str(credential))
        saved = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                               input=json.dumps({"client_id": result["client_id"], "client_secret": result["client_secret"]}),
                               text=True, capture_output=True, env=env)
        if saved.returncode:
            raise RuntimeError("Windows encrypted credential save failed")
        write_json_atomic(STATE / "registration.json", {"status": "registered", "app_id": result["client_id"]})
        print("Independent app registered; credentials saved with Windows DPAPI.", flush=True)
    except Exception as error:
        write_json_atomic(STATE / "registration.json", {"status": "failed", "error_type": type(error).__name__})
        print("Registration stopped: " + type(error).__name__, file=sys.stderr)
        return 1
    finally:
        timer.cancel()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
