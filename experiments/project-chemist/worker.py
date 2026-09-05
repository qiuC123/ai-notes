"""Read-only, pinned-Git tools for the isolated Pi feasibility experiment.

No search provider, model calls, working-tree reads, or learning-ledger writes.
Only the operator-provided blind manifest selects repositories and revisions.
"""
from __future__ import annotations

import argparse
import ast
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

from ai_notes.contracts import IMPACT_BASELINE_SCHEMA, IMPACT_BASELINE_V2_SCHEMA, validate_contract
from ai_notes.storage import canonical_json_bytes

MAX_FILE = 512 * 1024
MAX_OUTPUT = 8 * 1024 * 1024
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".md", ".json", ".toml", ".yaml", ".yml", ".txt"}
BLOCKED_PARTS = {".git", ".pi", ".codex", ".agents", "node_modules", ".venv", "venv"}


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"Expected integer in {low}..{high}")
    return value


def git(root: Path, *args: str) -> bytes:
    # A caller's Git environment must not redirect reads outside the manifest.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(
            ["git", "--no-pager", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            stdout=output, stderr=subprocess.PIPE, env=env, timeout=15,
        )
        if result.returncode:
            raise ValueError("Pinned Git read failed; check local repository and commit availability")
        if output.tell() > MAX_OUTPUT:
            raise ValueError("Git output exceeds experiment budget")
        output.seek(0)
        return output.read()


def allowed_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(parts) and not (
        "\\" in path or ":" in path or path.startswith("/")
        or any(p in {".", ".."} or p.lower() in BLOCKED_PARTS for p in parts)
        or any(p.lower().startswith(".env") for p in parts)
        or re.search(r"(^|[/_.-])(auth|credentials|secrets?|tokens?)([/_.-]|$)", path, re.I)
    ) and PurePosixPath(path).suffix.lower() in TEXT_SUFFIXES


def load_manifest(path: Path) -> dict:
    if path.stat().st_size > 128 * 1024:
        raise ValueError("Manifest exceeds budget")
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = {"schema_version", "suite_id", "source_learning", "repositories", "repository_read_rule", "cases", "response_contract"}
    if not isinstance(payload, dict) or set(payload) != expected or payload["schema_version"] not in ("impact-blind-input.v1", "impact-blind-input.v2"):
        raise ValueError("Expected blind input, never the gold suite")
    expected_schema = IMPACT_BASELINE_V2_SCHEMA if payload["schema_version"] == "impact-blind-input.v2" else IMPACT_BASELINE_SCHEMA
    if payload.get("response_contract", {}).get("schema_version") != expected_schema:
        raise ValueError("Blind input and response contract versions must match")
    repos = payload["repositories"]
    if not isinstance(repos, list) or not 2 <= len(repos) <= 5:
        raise ValueError("Expected 2..5 authorized repositories")
    ids = set()
    for repo in repos:
        if set(repo) != {"project_id", "root", "git_head", "read_only"}:
            raise ValueError("Unexpected repository fields")
        if not isinstance(repo["project_id"], str) or not repo["project_id"] or repo["project_id"] in ids:
            raise ValueError("Invalid or duplicate project ID")
        ids.add(repo["project_id"])
        if repo["read_only"] is not True or not re.fullmatch(r"[0-9a-f]{40}", repo["git_head"]):
            raise ValueError("Read-only pinned commit required")
        if not Path(repo["root"]).is_absolute():
            raise ValueError("Absolute authorized root required")
    cases = payload["cases"]
    if not isinstance(cases, list) or len(cases) != 5:
        raise ValueError("The v1 experiment requires five cases")
    case_ids = set()
    for case in cases:
        if set(case) != {"case_id", "title", "change_project", "change_description"}:
            raise ValueError("Blind cases may not contain answer fields")
        if not re.fullmatch(r"impact-[0-9]{2}", case["case_id"]) or case["case_id"] in case_ids:
            raise ValueError("Invalid or duplicate case ID")
        case_ids.add(case["case_id"])
        if case["change_project"] not in ids:
            raise ValueError("Unknown change project")
    return payload


class Chemist:
    def __init__(self, manifest: Path):
        self.manifest = load_manifest(manifest)
        self.input_hash = hashlib.sha256(canonical_json_bytes(self.manifest)).hexdigest()
        self.repos = {r["project_id"]: r for r in self.manifest["repositories"]}
        self.catalogs = {}
        self.texts = {}

    def catalog(self, project: str):
        if project not in self.repos:
            raise ValueError("Project is not authorized by the blind manifest")
        if project in self.catalogs:
            return self.catalogs[project]
        repo = self.repos[project]
        root = Path(repo["root"]).resolve(strict=True)
        head = repo["git_head"]
        if git(root, "cat-file", "-t", head).strip() != b"commit":
            raise ValueError("Pinned object is not a commit")
        prefix = git(root, "rev-parse", "--show-prefix").decode().strip()
        git_root = Path(git(root, "rev-parse", "--show-toplevel").decode().strip())
        tree = f"{head}:{prefix.rstrip('/')}" if prefix else head
        # ls-tree applies cwd's prefix even to an explicit subtree object.
        # Execute at the Git root so a monorepo project is not filtered twice.
        rows = git(git_root, "ls-tree", "-r", "-l", "-z", tree).split(b"\0")
        files = {}
        skipped = 0
        for row in filter(None, rows):
            header, raw_path = row.split(b"\t", 1)
            mode, kind, oid, size = header.split()
            path = raw_path.decode("utf-8")
            if mode not in {b"100644", b"100755"} or kind != b"blob" or int(size) > MAX_FILE or not allowed_path(path):
                skipped += 1
                continue
            files[path] = oid.decode()
        if not files:
            raise ValueError("No eligible pinned text files; cannot report a ready experiment")
        self.catalogs[project] = (root, files, skipped)
        return self.catalogs[project]

    def source(self, project: str, path: str) -> list[str]:
        root, files, _ = self.catalog(project)
        if path not in files or not allowed_path(path):
            raise ValueError("Path is outside the allowed pinned text inventory")
        key = (project, path)
        if key not in self.texts:
            raw = git(root, "cat-file", "blob", files[path])
            if len(raw) > MAX_FILE or b"\0" in raw:
                raise ValueError("Binary or oversized evidence is not supported")
            self.texts[key] = raw.decode("utf-8").splitlines()
        return self.texts[key]

    def context(self):
        # Do not forward arbitrary response_contract text as agent instructions.
        return {
            "suite_id": self.manifest["suite_id"], "blind_input_sha256": self.input_hash,
            "cases": self.manifest["cases"],
            "repositories": [
                {"project_id": pid, "git_head": r["git_head"], "read_only": True,
                 "eligible_files": len(self.catalog(pid)[1]), "skipped_files": self.catalog(pid)[2]}
                for pid, r in self.repos.items()
            ],
        }

    def inventory(self, project: str, contains="", offset=0):
        integer(offset, 0, 100000)
        if not isinstance(contains, str) or len(contains) > 200:
            raise ValueError("Invalid filename filter")
        _, files, skipped = self.catalog(project)
        matches = [p for p in sorted(files) if contains.casefold() in p.casefold()]
        page = matches[offset:offset + 100]
        return {"paths": page, "total": len(matches), "skipped_files": skipped,
                "next_offset": offset + 100 if offset + 100 < len(matches) else None}

    def read(self, project: str, path: str, start=1, count=100):
        integer(start, 1, 1000000)
        integer(count, 1, 200)
        lines = self.source(project, path)
        if start > max(1, len(lines)):
            raise ValueError("Line start exceeds pinned file")
        selected = lines[start - 1:start - 1 + count]
        text = "\n".join(f"{i}: {line}" for i, line in enumerate(selected, start))
        if len(text) > 24000:
            raise ValueError("Read exceeds output budget; request fewer lines")
        return {"project_id": project, "path": path, "git_head": self.repos[project]["git_head"],
                "total_lines": len(lines), "text": text}

    def search(self, project: str, query: str, path: str):
        if not isinstance(query, str) or not 1 <= len(query) <= 200:
            raise ValueError("Expected 1..200 character literal query")
        lines = self.source(project, path)
        matches = [{"line": i, "text": line[:500], "truncated": len(line) > 500}
                   for i, line in enumerate(lines, 1) if query.casefold() in line.casefold()]
        return {"matches": matches[:40], "total": len(matches), "truncated": len(matches) > 40}

    def test_selectors(self, project: str, path: str):
        if not path.endswith(".py") or not PurePosixPath(path).name.startswith("test"):
            raise ValueError("This experiment supports Python test files only")
        tree = ast.parse("\n".join(self.source(project, path)))
        selectors = []
        # Parse, never import or execute sample code. Collection is not verified.
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                selectors.append({"selector": node.name, "line": node.lineno})
            elif isinstance(node, ast.ClassDef):
                for method in node.body:
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name.startswith("test_"):
                        selectors.append({"selector": f"{node.name}.{method.name}", "line": method.lineno})
        return {"selectors": selectors, "collection_verified": False, "tests_executed": False}

    def validate_result(self, result: dict):
        validate_contract(self.manifest["response_contract"]["schema_version"], result)
        if result["suite_id"] != self.manifest["suite_id"] or result["blind_protocol"]["blind_input_sha256"] != self.input_hash:
            raise ValueError("Result does not match this frozen blind input")
        observed = result["observed_repositories"]
        if len(observed) != len(self.repos) or {r["project_id"]: r["git_head"] for r in observed} != {p: r["git_head"] for p, r in self.repos.items()}:
            raise ValueError("Result revisions do not match")
        cases = {c["case_id"]: c for c in self.manifest["cases"]}
        if {c["case_id"] for c in result["cases"]} != set(cases):
            raise ValueError("Assess every case exactly once")
        for case in result["cases"]:
            change = cases[case["case_id"]]["change_project"]
            assessments = case["project_assessments"]
            expected = set(self.repos) - {change}
            if len(assessments) != len(expected) or {a["project_id"] for a in assessments} != expected:
                raise ValueError("Assess every other project exactly once")
            for assessment in assessments:
                sides = set()
                for ref in assessment["evidence"]:
                    lines = self.source(ref["project_id"], ref["path"])
                    if "line_start" not in ref or "line_end" not in ref:
                        raise ValueError("Chemist requires explicit line ranges, not symbol-only evidence")
                    start, end = ref["line_start"], ref["line_end"]
                    if not 1 <= start <= end <= len(lines) or end - start >= 200:
                        raise ValueError("Invalid or overly broad evidence range")
                    if ref.get("symbol") and ref["symbol"] not in "\n".join(lines[start - 1:end]):
                        raise ValueError("Evidence symbol is absent from the cited lines")
                    sides.add(ref["project_id"])
                tests = assessment["required_tests"]
                for test in tests:
                    existing = self.test_selectors(test["project_id"], test["path"])["selectors"]
                    if test["selector"] not in {t["selector"] for t in existing}:
                        raise ValueError("Test selector does not exist in pinned source")
                if assessment["relationship"] == "direct_dependency":
                    required = {change, assessment["project_id"]}
                    if not required <= sides:
                        raise ValueError("Direct dependency requires evidence from both projects")
                    if not required <= {t["project_id"] for t in tests}:
                        raise ValueError("Direct dependency requires existing tests on both sides")
        return {"valid": True, "semantic_correctness_verified": False, "tests_executed": False,
                "result_sha256": hashlib.sha256(canonical_json_bytes(result)).hexdigest()}

    def assemble_result(self, analysis: dict):
        """Construct execution facts, never accept them from the model."""
        if not isinstance(analysis, dict) or set(analysis) != {"cases", "blind_attestation"}:
            raise ValueError("Submit only cases and blind_attestation; metadata is runtime-owned")
        attestation = analysis["blind_attestation"]
        flags = {"gold_answer_accessed_before_completion", "ai_notes_repository_inspected"}
        if not isinstance(attestation, dict) or set(attestation) != flags or any(v is not False for v in attestation.values()):
            raise ValueError("Explicit clean blind attestation required")
        task_id = os.environ.get("CHEMIST_RUN_ID", "")
        if not re.fullmatch(r"project-chemist-[0-9a-f-]{36}", task_id):
            raise ValueError("Missing runtime task ID")
        result = {
            "schema_version": self.manifest["response_contract"]["schema_version"], "suite_id": self.manifest["suite_id"],
            "completed_at": datetime.now(UTC).isoformat(),
            "blind_protocol": {"independent_task_id": task_id, "blind_input_sha256": self.input_hash, **attestation},
            "observed_repositories": [
                {"project_id": pid, "git_head": r["git_head"], "read_only": True}
                for pid, r in self.repos.items()
            ],
            "cases": analysis["cases"],
        }
        validation = self.validate_result(result)
        # Legacy v1 only allows the optional string 'git show'. Omit it rather
        # than changing v1 or lying. Actual method is in runtime metadata.
        return {**validation, "result": result,
                "execution": {"metadata_source": "runtime", "repository_read_method": "git cat-file"}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("Request exceeds budget")
        request = json.loads(raw)
        worker = Chemist(args.input)
        actions = {"context": worker.context, "inventory": worker.inventory, "read": worker.read,
                   "search": worker.search, "test_selectors": worker.test_selectors,
                   "validate_result": worker.validate_result, "assemble_result": worker.assemble_result}
        if not isinstance(request, dict) or set(request) != {"action", "params"} or request["action"] not in actions:
            raise ValueError("Unknown tool request")
        result = actions[request["action"]](**request["params"])
        print(json.dumps({"ok": True, "data": result}, ensure_ascii=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError, SyntaxError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
