"""Durable single-user Feishu inbox/outbox. No network or model calls in reception."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid

from ai_notes.github import parse_github_url

HELP = ("发送一个 GitHub 项目链接，可附问题；之后直接发文字继续追问。\n"
        "追问会保留会话；新链接开始新项目，“新话题”重置上下文（保留历史文件）。\n"
        "发送“状态”查看最近任务，“结果”重发最近结果，“帮助”查看说明。\n"
        "本机串行分析，通常需要几分钟。仅做只读初步分析。\n"
        "自有项目范围：Ai Notes 的 storage.py、learning.py、review.py；尚不支持其他项目的本地代码。")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def project_url(text):
    # Feishu displays clickable links even when the original text has no scheme.
    # Match whole explicit URLs first so an embedded github.com cannot hide an alien host.
    links = re.findall(
        r"https?://[^\s<>\"'`，。；！？、（）\[\](),]+|"
        r"(?<![A-Za-z0-9_./@:-])(?:www\.)?github\.com/[^\s<>\"'`，。；！？、（）\[\](),]+",
        text, flags=re.IGNORECASE)
    if not links:
        return None
    normalized = []
    for link in links:
        url = link.rstrip(".;；！!？?")
        if not re.match(r"https?://", url, flags=re.IGNORECASE):
            url = "https://" + url
        url = re.sub(r"^https?://(?:www\.)?github\.com/", "https://github.com/", url, flags=re.IGNORECASE)
        try:
            parse_github_url(url)
        except ValueError:
            raise ValueError("目前只支持公开 GitHub 项目、分支、Release、Issue 或 PR 链接。") from None
        if url not in normalized:
            normalized.append(url)
    if len(normalized) != 1:
        raise ValueError("每次请发送一个 GitHub 链接。")
    return normalized[0]


class Store:
    help_text = HELP
    new_topic_text = "已开始新话题，请发送 GitHub 项目链接。历史文件已保留；已排队任务仍会完成。"
    retry_text = "可重新发链接重试。"

    def request_target(self, text, previous):
        url = project_url(text)
        if not url and not previous:
            raise ValueError("请先发送一个 GitHub 项目链接，再继续追问。")
        return url

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS owner (user_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS inbox (message_id TEXT PRIMARY KEY, received REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, message_id TEXT UNIQUE NOT NULL,
                    chat_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    url TEXT NOT NULL, question TEXT NOT NULL, parent_id TEXT,
                    status TEXT NOT NULL, created REAL NOT NULL,
                    queue_path TEXT, learning_root TEXT, report TEXT, result_path TEXT,
                    error TEXT);
                CREATE TABLE IF NOT EXISTS conversations (
                    chat_id TEXT NOT NULL, user_id TEXT NOT NULL, last_job TEXT NOT NULL,
                    PRIMARY KEY(chat_id,user_id));
                CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY, message_id TEXT NOT NULL, text TEXT NOT NULL,
                    sent INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                    next_try REAL NOT NULL DEFAULT 0, created REAL NOT NULL);
            """)
            if "session_file" not in {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}:
                db.execute("ALTER TABLE jobs ADD COLUMN session_file TEXT")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def reply(db, message_id, text, key):
        # Smaller than Feishu's UTF-8 byte limit, even with all-Chinese text.
        chunks = [text[i:i + 1800] for i in range(0, len(text), 1800)]
        for i, chunk in enumerate(chunks):
            body = f"({i+1}/{len(chunks)})\n{chunk}" if len(chunks) > 1 else chunk
            identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, f"chemist:{message_id}:{key}:{i}"))
            db.execute("INSERT OR IGNORE INTO outbox(id,message_id,text,created) VALUES(?,?,?,?)",
                       (identifier, message_id, body, time.time()))

    def receive(self, event, pairing_code):
        """Return only after commit; SDK may safely acknowledge or retry delivery."""
        sender = event.get("sender", {})
        message = event.get("message", {})
        user = (sender.get("sender_id") or {}).get("open_id")
        mid, chat = message.get("message_id"), message.get("chat_id")
        if sender.get("sender_type") != "user" or message.get("chat_type") != "p2p" or not all((user, mid, chat)):
            return
        try:
            content = json.loads(message.get("content") or "{}")
            text = content.get("text", "").strip() if message.get("message_type") == "text" else ""
        except (ValueError, AttributeError):
            return
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            owner = db.execute("SELECT user_id FROM owner").fetchone()
            if not owner:
                if not hmac.compare_digest(digest(text), digest("配对 " + pairing_code)):
                    return
                db.execute("INSERT INTO owner VALUES(?)", (user,))
            elif owner["user_id"] != user:
                return
            inserted = db.execute("INSERT OR IGNORE INTO inbox VALUES(?,?)", (mid, time.time())).rowcount
            if not inserted:
                return
            if text.startswith("配对 "):
                self.reply(db, mid, "配对成功。\n" + self.help_text, "paired")
                return
            if text in {"帮助", "help", "/help"} or not text:
                self.reply(db, mid, self.help_text, "help")
                return
            previous = db.execute("SELECT j.* FROM conversations c JOIN jobs j ON c.last_job=j.id "
                                  "WHERE c.chat_id=? AND c.user_id=?", (chat, user)).fetchone()
            if text == "新话题":
                db.execute("DELETE FROM conversations WHERE chat_id=? AND user_id=?", (chat, user))
                self.reply(db, mid, self.new_topic_text, "new-topic")
                return
            if text == "结果":
                self.reply(db, mid, previous["report"] if previous and previous["report"] else "最近任务尚无结果。", "resend")
                return
            if text in {"状态", "status", "/status"}:
                labels = {"queued": "排队中", "running": "分析中", "done": "分析完成", "failed": "分析失败"}
                status = "尚无分析任务。" if not previous else (
                    f"任务 {previous['id'][:8]}：{labels[previous['status']]}\n{previous['url']}\n"
                    + (previous["error"] or ""))
                pending = db.execute("SELECT COUNT(*) FROM outbox WHERE sent=0").fetchone()[0]
                self.reply(db, mid, status + f"\n待发送消息：{pending}", "status")
                return
            try:
                if len(text) > 1800:
                    raise ValueError("消息过长，请缩短到1800字以内。")
                url = self.request_target(text, previous)
                if db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 20:
                    raise ValueError("已有20个待处理任务，请稍后再发。")
            except ValueError as error:
                self.reply(db, mid, str(error), "invalid")
                return
            identifier = uuid.uuid4().hex
            parent = previous["id"] if previous and url is None else None
            db.execute("INSERT INTO jobs(id,message_id,chat_id,user_id,url,question,parent_id,status,created) "
                       "VALUES(?,?,?,?,?,?,?,'queued',?)",
                       (identifier, mid, chat, user, url or previous["url"], text, parent, time.time()))
            db.execute("INSERT INTO conversations VALUES(?,?,?) ON CONFLICT(chat_id,user_id) "
                       "DO UPDATE SET last_job=excluded.last_job", (chat, user, identifier))
            self.reply(db, mid, f"已收到，任务 {identifier[:8]} 已排队。分析结束后会回复这里。", "accepted")

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='queued' WHERE status='running'")

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created,rowid LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET status='running' WHERE id=?", (row["id"],))
                return dict(row)

    def get(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
            return dict(row) if row else None

    def complete(self, job, result):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='done',queue_path=?,learning_root=?,report=?,result_path=?,session_file=? WHERE id=?",
                       (result["queue_path"], result["learning_root"], result["report"], result["result_path"],
                        result.get("session_file"), job["id"]))
            self.reply(db, job["message_id"], result["report"], "result")

    def context_parent(self, job):
        """Only successful ancestors in this topic supply history after a failure."""
        parent_id = job["parent_id"]
        while parent_id:
            parent = self.get(parent_id)
            if not parent or (parent["chat_id"], parent["user_id"]) != (job["chat_id"], job["user_id"]):
                raise ValueError("Invalid conversation ancestry")
            if parent["status"] == "done":
                return parent
            parent_id = parent["parent_id"]
        return None

    def fail(self, job, error):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='failed',error=? WHERE id=?", (error, job["id"]))
            self.reply(db, job["message_id"], f"任务 {job['id'][:8]} 未完成：{error}\n{self.retry_text}", "failed")

    def pending_reply(self):
        with self.connect() as db:
            # Preserve order; a failed part must not be overtaken by later messages.
            row = db.execute("SELECT * FROM outbox WHERE sent=0 ORDER BY created,rowid LIMIT 1").fetchone()
            return dict(row) if row and row["next_try"] <= time.time() else None

    def delivered(self, identifier):
        with self.connect() as db:
            db.execute("UPDATE outbox SET sent=1 WHERE id=?", (identifier,))

    def delivery_failed(self, item):
        with self.connect() as db:
            delay = min(300, 2 ** min(item["attempts"] + 1, 9))
            db.execute("UPDATE outbox SET attempts=attempts+1,next_try=?,sent=? WHERE id=?",
                       (time.time() + delay, -1 if item["attempts"] >= 11 else 0, item["id"]))

    def status(self):
        with self.connect() as db:
            return {"paired": bool(db.execute("SELECT 1 FROM owner").fetchone()),
                    "jobs": dict(db.execute("SELECT status,COUNT(*) FROM jobs GROUP BY status").fetchall()),
                    "pending_replies": db.execute("SELECT COUNT(*) FROM outbox WHERE sent=0").fetchone()[0],
                    "failed_replies": db.execute("SELECT COUNT(*) FROM outbox WHERE sent=-1").fetchone()[0]}
