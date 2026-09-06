"""One bounded mobile analysis in a disposable subprocess; reuse learning contracts."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
from urllib.parse import quote
import uuid

import yaml

from ai_notes.learning import prepare_learning
from ai_notes.storage import write_json_atomic
from learning_worker import LearningReader

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SCOPE = ("自有证据仅为 Ai Notes 的 storage.py、learning.py、review.py。"
         "外部证据为 README、最多500项的文件树和最多6个抽样源码文件，可能遗漏关键实现。")


def select_sources(queue):
    tree = next((e["text"] for e in queue["external_evidence"] if e["kind"] == "tree"), "")
    suffixes = {".py", ".ts", ".tsx", ".js", ".go", ".rs", ".java", ".c", ".cpp"}
    candidates = []
    for name in tree.splitlines():
        path = PurePosixPath(name)
        if (path.suffix not in suffixes or path.is_absolute() or ".." in path.parts or
            any(part.startswith(".") or part in {"tests", "test", "vendor", "dist", "fixtures", "examples"}
                for part in path.parts) or name.endswith((".test.ts", ".spec.ts", ".d.ts"))):
            continue
        score = (0 if path.stem in {"main", "index", "app", "cli", "core", "run"} else 1,
                 0 if path.parts[0] in {"src", "lib"} else 1, len(path.parts), name)
        candidates.append((score, name))
    return [name for _, name in sorted(candidates)[:6]]


def pi_cli():
    command = shutil.which("pi.cmd") or shutil.which("pi") or shutil.which("pi.ps1")
    if not command:
        raise RuntimeError("Pi CLI 未安装")
    path = Path(command).parent / "node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js"
    if not path.is_file():
        raise RuntimeError("未找到现有 npm Pi CLI")
    return path


def render_report(decisions, queue, question):
    evidence = {e["evidence_id"]: e for e in queue["external_evidence"]}
    understanding = decisions["project_understanding"]
    lines = ["Pi 初步分析 · 尚未经 Codex 语义复核", queue["input"]["canonical_url"],
             "问题：" + question, "", understanding["problem"], "", "核心机制"]
    refs = {}

    def claim(item):
        lines.append("• " + item["claim"])
        for ref in item["evidence"]:
            source = evidence[ref["evidence_id"]]
            refs[(source["official_url"], ref["quote"])] = source["title"]

    for item in understanding["core_abstractions"] + understanding["architecture"]:
        claim(item)
    for connection in decisions["connections"]:
        lines.extend(["", "可能关联：" + connection["title"], connection["hypothesis"],
                      "预期收益（未实测）：" + connection["expected_benefit"]])
        claim(connection["external_capability"])
        lines.append("自有侧问题（推断）：" + connection["owned_project_problem"]["claim"])
        for ref in connection["owned_project_problem"]["evidence"]:
            lines.append(f"• {ref['path']}:{ref['start_line']} — {ref['description']}")
        lines.extend("成本：" + value for value in connection["costs"])
        lines.extend("限制：" + value for value in connection["risks"])
        if connection["experiment"]:
            lines.append("下一步建议（未执行）：" + connection["experiment"]["objective"])
            lines.extend("• " + value for value in connection["experiment"]["success_criteria"])
    if not decisions["connections"]:
        lines.extend(["", "未形成可靠关联：" + decisions["no_connection_reason"]])
    lines.extend("不适用条件：" + value for value in understanding["non_applicable_conditions"])
    for key in ("license_notes", "security_notes", "maintenance_notes"):
        lines.extend(decisions["risks"][key])
    lines.extend(["", "证据（原文摘录）"])
    for (url, text), title in refs.items():
        lines.extend([title, "“" + text[:240] + ("…" if len(text) > 240 else "") + "”", url])
    lines.extend(["", "阅读范围：" + SCOPE,
                  "外部版本：" + queue["verified_target"]["commit_sha"]])
    # Failure details can contain remote content; show categories rather than raw exceptions.
    if queue["missing_scopes"]:
        categories = sorted({value.split(":", 1)[0].split(" ", 1)[0] for value in queue["missing_scopes"]})
        lines.append("缺失证据：" + "、".join(categories))
    lines.append("可直接追问；发送新链接开始新的项目分析。")
    return "\n".join(lines)


def analyze(job, parent, state):
    attempt = state / "runs" / job["id"] / uuid.uuid4().hex
    attempt.mkdir(parents=True)
    if parent and parent.get("queue_path"):
        root, queue_path = Path(parent["learning_root"]), Path(parent["queue_path"])
    else:
        root = attempt / "learning"
        (root / "config").mkdir(parents=True)
        policy = yaml.safe_load((REPO / "config/ai_notes_learning.yaml").read_text(encoding="utf-8"))
        policy.update(max_external_evidence=10, max_evidence_chars=65536)
        (root / "config/ai_notes_learning.yaml").write_text(yaml.safe_dump(policy), encoding="utf-8")
        prepared = prepare_learning(root=root, github_url=job["url"], project_path=REPO)
        if not prepared.queue_path:
            raise RuntimeError("GitHub 证据采集失败；请检查链接和网络后重试")
        queue = json.loads(prepared.queue_path.read_text(encoding="utf-8"))
        urls = tuple(f"{queue['input']['canonical_url']}/blob/{queue['verified_target']['commit_sha']}/{quote(name, safe='/')}"
                     for name in select_sources(queue))
        if urls:
            prepared = prepare_learning(root=root, github_url=job["url"], project_path=REPO,
                                        include_urls=urls, run_id=prepared.run_id)
        queue_path = prepared.queue_path
    reader = LearningReader(queue_path, root)
    # Each attempt extends an immutable successful checkpoint. Failed attempts
    # never mutate the parent's history and are not replayed after a crash.
    session_path = attempt / "session.jsonl"
    inherited_session = parent.get("session_file") if parent else None
    if inherited_session:
        source = Path(inherited_session).resolve()
        if not source.is_relative_to(state.resolve() / "runs"):
            raise ValueError("Session must belong to this mobile state directory")
        validate_session(source)
        shutil.copyfile(source, session_path)
    previous = (parent.get("report") or "")[-12000:] if parent and not inherited_session else ""
    request_path = attempt / "request.json"
    write_json_atomic(request_path, {"question": job["question"], "previous_discussion": previous})
    output = attempt / "pi"
    output.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith("CHEMIST_FEISHU_")}
    env.update(PYTHONPATH=str(REPO / "src"), PYTHONIOENCODING="utf-8", PI_OFFLINE="1",
               CHEMIST_PYTHON=sys.executable, CHEMIST_INPUT=str(queue_path),
               CHEMIST_LEARNING_ROOT=str(root), CHEMIST_RUN_DIR=str(output),
               CHEMIST_RUN_ID="mobile-" + job["id"])
    with (attempt / "runner.log").open("w", encoding="utf-8") as log:
        process = subprocess.run([sys.executable, str(HERE / "run.py"), "--pi-cli", str(pi_cli()),
                                  "--profile", "learning", "--session-file", str(session_path),
                                  "--request-file", str(request_path)],
                                 cwd=attempt, env=env, stdout=log, stderr=log, check=False)
    if process.returncode:
        raise RuntimeError("Pi 分析或引用校验未完成；请检查本机运行记录后重试")
    decisions_path = output / "decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    # Recheck after the model exits; source changes invalidate even a sealed report.
    reader = LearningReader(queue_path, root)
    reader.submit({key: value for key, value in decisions.items()
                   if key not in {"schema_version", "queue_sha256", "run_id"}})
    validate_session(session_path)
    report = render_report(decisions, reader.queue, job["question"])
    (attempt / "report.txt").write_text(report, encoding="utf-8")
    return {"queue_path": str(queue_path), "learning_root": str(root), "report": report,
            "result_path": str(decisions_path), "session_file": str(session_path)}


def validate_session(path):
    """Reject missing/truncated history instead of letting Pi silently reset it."""
    with path.open(encoding="utf-8") as handle:
        header = json.loads(next(handle))
        if header.get("type") != "session" or not header.get("id"):
            raise ValueError("Invalid Pi session header")
        has_assistant = False
        for line in handle:
            entry = json.loads(line)
            if entry.get("type") == "message" and entry.get("message", {}).get("role") == "assistant":
                has_assistant = True
        if not has_assistant:
            raise ValueError("Pi session has no completed assistant history")


if __name__ == "__main__":
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    result = analyze(request["job"], request["parent"], Path(request["state"]))
    write_json_atomic(Path(sys.argv[2]), result)
