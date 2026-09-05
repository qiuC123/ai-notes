"""Experimental Pi reader over the existing learning queue; never finalizes a ledger."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from ai_notes.contracts import DECISIONS_SCHEMA, QUEUE_SCHEMA, load_schema, validate_contract
from ai_notes.review import _validate_cross_contracts
from ai_notes.storage import project_fingerprint, resolve_within, sha256_file

# Deliberate initial evidence scope, not a claim of repository-wide coverage.
OWNED_PATHS = ("src/ai_notes/storage.py", "src/ai_notes/learning.py", "src/ai_notes/review.py")


class LearningReader:
    def __init__(self, queue_path: Path, root: Path):
        self.path, self.root = queue_path.resolve(), root.resolve()
        if self.path.stat().st_size > 1024 * 1024:
            raise ValueError("Learning queue exceeds 1 MiB experiment budget")
        self.queue = validate_contract(QUEUE_SCHEMA, json.loads(self.path.read_text(encoding="utf-8")))
        expected = self.root / "outputs/learning" / self.queue["run_id"] / "learning-queue.json"
        if self.path != expected.resolve():
            raise ValueError("Queue must belong to the explicit learning root")
        self.queue_hash = sha256_file(self.path)
        self.owned = Path(self.queue["owned_project"]["root"])
        current = project_fingerprint(self.owned)
        for field in ("git_head", "working_tree_fingerprint"):
            if current[field] != self.queue["owned_project"][field]:
                raise ValueError("Owned project changed after queue preparation")

    def context(self):
        return {
            "run_id": self.queue["run_id"], "queue_sha256": self.queue_hash,
            "input": self.queue["input"], "verified_target": self.queue["verified_target"],
            "source_risk": self.queue["source_risk"], "missing_scopes": self.queue["missing_scopes"],
            "external_sources": [{k: item[k] for k in ("evidence_id", "title", "official_url", "text_sha256")}
                                 | {"lines": len(item["text"].splitlines())} for item in self.queue["external_evidence"]],
            "owned_sources": [{"path": path, "file_sha256": sha256_file(resolve_within(self.owned, path))}
                              for path in OWNED_PATHS],
            "scope_note": "Only these preselected owned source files; this is not exhaustive autonomous navigation.",
        }

    def read(self, source: str, start: int = 1, count: int = 120):
        if type(start) is not int or type(count) is not int or start < 1 or not 1 <= count <= 200:
            raise ValueError("Expected positive start and count in 1..200")
        external = {item["evidence_id"]: item for item in self.queue["external_evidence"]}
        if source in external:
            item = external[source]
            text = item["text"]
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if digest != item["text_sha256"]:
                raise ValueError("External evidence hash mismatch")
        elif source in OWNED_PATHS:
            path = resolve_within(self.owned, source)
            if path.stat().st_size > 512 * 1024:
                raise ValueError("Owned file exceeds budget")
            text, digest = path.read_text(encoding="utf-8"), sha256_file(path)
        else:
            raise ValueError("Source is not in the explicit evidence scope")
        lines = text.splitlines()
        if start > max(1, len(lines)):
            raise ValueError("Start exceeds source length")
        numbered = "\n".join(f"{i}: {line}" for i, line in enumerate(lines[start-1:start-1+count], start))
        if len(numbered) > 24000:
            raise ValueError("Read exceeds output budget; request fewer lines")
        return {"source": source, "sha256": digest, "total_lines": len(lines), "text": numbered}

    def submit(self, analysis: dict):
        expected = set(load_schema(DECISIONS_SCHEMA)["required"]) - {"schema_version", "run_id", "queue_sha256"}
        if not isinstance(analysis, dict) or set(analysis) != expected:
            raise ValueError("Submit analysis only; run and queue metadata are runtime-owned")
        decisions = {**analysis, "schema_version": DECISIONS_SCHEMA,
                     "run_id": self.queue["run_id"], "queue_sha256": self.queue_hash}
        validate_contract(DECISIONS_SCHEMA, decisions)
        for connection in decisions["connections"]:
            if connection["owned_project_problem"]["status"] != "inferred":
                raise ValueError("This experiment has no user-confirmed specific problem; use inferred")
            for ref in connection["owned_project_problem"]["evidence"]:
                if ref["path"] not in OWNED_PATHS:
                    raise ValueError("Owned citation outside experiment scope")
        _validate_cross_contracts(self.root, self.queue, decisions, self.queue_hash)
        return {"decisions": decisions, "valid": True, "semantic_correctness_verified": False,
                "ledger_written": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("Request exceeds budget")
        request = json.loads(raw)
        reader = LearningReader(args.input, args.root)
        actions = {"context": reader.context, "read": reader.read, "submit": reader.submit}
        if set(request) != {"action", "params"} or request["action"] not in actions:
            raise ValueError("Unknown request")
        print(json.dumps({"ok": True, "data": actions[request["action"]](**request["params"])}, ensure_ascii=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
