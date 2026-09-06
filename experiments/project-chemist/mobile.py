"""Local Feishu long-connection service. Credentials belong only to the adapter."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time

from ai_notes.storage import write_json_atomic
from mobile_analysis import HERE, REPO, pi_cli
from mobile_core import Store

LOG = logging.getLogger("chemist-mobile")


@contextmanager
def process_tree(process):
    """Windows closes the Job on service exit, terminating its model descendants."""
    if os.name != "nt":
        try:
            yield
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t),
                    ("max_working", ctypes.c_size_t), ("active", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", ctypes.c_uint64 * 6),
                    ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    try:
        if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job, int(process._handle)):
            process.kill()
            process.wait(timeout=10)
            raise OSError("Unable to contain analysis process tree")
        yield
    finally:
        if job:
            kernel.CloseHandle(job)


@contextmanager
def single_instance(path):
    """OS-held lock releases on crashes; no stale PID or lock-file deletion required."""
    handle = path.open("a+b")
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("共学服务已在运行") from None
    try:
        yield
    finally:
        handle.close()


class Feishu:
    def __init__(self, app_id, secret):
        import lark_oapi as lark
        self.lark = lark
        self.app_id, self.secret = app_id, secret
        self.client = lark.Client.builder().app_id(app_id).app_secret(secret).timeout(20).log_level(lark.LogLevel.CRITICAL).build()

    def send(self, item):
        from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
        request = (ReplyMessageRequest.builder().message_id(item["message_id"])
                   .request_body(ReplyMessageRequestBody.builder().msg_type("text")
                                 .content(json.dumps({"text": item["text"]}, ensure_ascii=False))
                                 .uuid(item["id"]).build()).build())
        response = self.client.im.v1.message.reply(request)
        if not response.success():
            raise RuntimeError(f"Feishu reply code={response.code}")

    def listen(self, store, pairing_code):
        def on_message(data):
            event = json.loads(self.lark.JSON.marshal(data))["event"]
            # Do not swallow database failures: SDK must not acknowledge an uncommitted inbox.
            store.receive(event, pairing_code)

        handler = (self.lark.EventDispatcherHandler.builder("", "")
                   .register_p2_im_message_receive_v1(on_message).build())
        client = self.lark.ws.Client(self.app_id, self.secret, event_handler=handler,
                                     log_level=self.lark.LogLevel.CRITICAL)
        client.start()


def execute_job(job, parent, state):
    request_dir = state / "requests"
    request_dir.mkdir(exist_ok=True)
    request_path, result_path = request_dir / (job["id"] + ".json"), request_dir / (job["id"] + ".result.json")
    result_path.unlink(missing_ok=True)
    write_json_atomic(request_path, {"job": job, "parent": parent, "state": str(state)})
    env = {key: value for key, value in os.environ.items() if not key.startswith("CHEMIST_FEISHU_")}
    env.update(PYTHONPATH=str(REPO / "src"), PYTHONIOENCODING="utf-8")
    with (request_dir / (job["id"] + ".log")).open("a", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, str(HERE / "mobile_analysis.py"), str(request_path), str(result_path)],
                                   cwd=REPO, env=env, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                                   start_new_session=os.name != "nt")
        with process_tree(process):
            try:
                code = process.wait(timeout=1200)
            except subprocess.TimeoutExpired:
                raise RuntimeError("超过20分钟，本次分析已停止") from None
    if code != 0 or not result_path.is_file():
        raise RuntimeError("采集、Pi 或证据校验失败（本机 requests 目录有运行记录）")
    return json.loads(result_path.read_text(encoding="utf-8"))


def worker(store, state, stop):
    while not stop.is_set():
        try:
            job = store.claim()
            if job:
                LOG.info("job_started id=%s", job["id"])
                try:
                    result = execute_job(job, store.context_parent(job), state)
                except Exception as error:
                    LOG.error("job_failed id=%s type=%s", job["id"], type(error).__name__)
                    store.fail(job, "采集、模型或引用校验未完成，请检查本机运行记录。")
                else:
                    store.complete(job, result)
                    LOG.info("job_done id=%s", job["id"])
                continue
        except Exception as error:
            LOG.error("worker_error type=%s", type(error).__name__)
        stop.wait(1)


def sender(store, api, stop):
    while not stop.is_set():
        try:
            item = store.pending_reply()
            if item:
                try:
                    api.send(item)
                except Exception as error:
                    LOG.warning("reply_retry id=%s type=%s", item["id"], type(error).__name__)
                    store.delivery_failed(item)
                else:
                    store.delivered(item["id"])
                continue
        except Exception as error:
            LOG.error("sender_error type=%s", type(error).__name__)
        stop.wait(1)


def main():
    parser = argparse.ArgumentParser(description="Feishu mobile co-learning")
    parser.add_argument("command", choices=("init", "doctor", "status", "serve", "check-app"))
    parser.add_argument("--state", type=Path, default=REPO / "work/mobile-chemist")
    args = parser.parse_args()
    state = args.state.resolve()
    config_path = state / "config.json"
    if args.command == "init":
        state.mkdir(parents=True, exist_ok=True)
        if not config_path.exists():
            write_json_atomic(config_path, {"pairing_code": secrets.token_hex(4)})
        print("本机状态目录：" + str(state))
        return 0
    if args.command == "doctor":
        checks = {"python": sys.version.split()[0], "sdk": importlib.metadata.version("lark-oapi"),
                  "pi_cli": str(pi_cli()), "config": config_path.is_file(),
                  "credentials": bool(os.getenv("CHEMIST_FEISHU_APP_ID") and os.getenv("CHEMIST_FEISHU_APP_SECRET"))}
        print(json.dumps(checks, ensure_ascii=False))
        return 0 if checks["config"] and checks["credentials"] else 2
    if not config_path.is_file():
        raise RuntimeError("请先运行 init")
    store = Store(state / "inbox.sqlite3")
    if args.command == "status":
        print(json.dumps(store.status(), ensure_ascii=False))
        return 0
    app_id, secret = os.getenv("CHEMIST_FEISHU_APP_ID"), os.getenv("CHEMIST_FEISHU_APP_SECRET")
    if not app_id or not secret:
        raise RuntimeError("请先通过 mobile-configure.ps1 配置独立机器人的凭证")
    api = Feishu(app_id, secret)
    if args.command == "check-app":
        import httpx
        response = httpx.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                              json={"app_id": app_id, "app_secret": secret}, timeout=20)
        data = response.json()
        print(json.dumps({"http_status": response.status_code, "code": data.get("code")}))
        return 0 if response.status_code == 200 and data.get("code") == 0 else 2
    with single_instance(state / "service.lock"):
        store.recover()
        logging.basicConfig(filename=state / "service.log", level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(message)s")
        stop = threading.Event()
        threading.Thread(target=worker, args=(store, state, stop), daemon=True).start()
        threading.Thread(target=sender, args=(store, api, stop), daemon=True).start()
        LOG.info("service_started pid=%s", os.getpid())
        write_json_atomic(state / "service.json", {"pid": os.getpid(), "started": time.time()})
        try:
            api.listen(store, json.loads(config_path.read_text(encoding="utf-8"))["pairing_code"])
        finally:
            stop.set()
            LOG.info("receiver_stopped")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Never echo SDK exceptions with connection URLs, headers or secrets.
        print(f"共学入口启动失败：{type(error).__name__}；请运行 doctor 检查本机配置。", file=sys.stderr)
        raise SystemExit(1)
