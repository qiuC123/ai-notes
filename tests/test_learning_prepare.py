from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_notes.github import GitHubError, GitHubPartialError, PublicGitHubApi, parse_github_url, verify_project
from ai_notes.learning import prepare_learning


SHA = "1" * 40
RUN_ID = "20260902T083000Z-0123abcd"


def repository_payload(*, fork: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "full_name": "openai/codex",
        "default_branch": "main",
        "description": "A coding agent",
        "homepage": "https://developers.openai.com/codex/",
        "archived": False,
        "fork": fork,
        "topics": ["agent"],
        "language": "Rust",
        "license": {"spdx_id": "Apache-2.0"},
    }
    if fork:
        payload["full_name"] = "someone/codex"
        payload["parent"] = {"full_name": "openai/codex"}
    return payload


class FakeGitHubApi:
    def __init__(self, *, fail_optional: bool = False, fork: bool = False) -> None:
        self.fail_optional = fail_optional
        self.fork = fork
        self.paths: list[str] = []

    def get_json(self, path: str) -> object:
        self.paths.append(path)
        if path == "/repos/openai/codex":
            return repository_payload()
        if path == "/repos/someone/codex":
            return repository_payload(fork=True)
        if path == "/repos/openai/codex/commits/main":
            return {"sha": SHA}
        if path == f"/repos/openai/codex/contents/README.md?ref={SHA}":
            if self.fail_optional:
                raise GitHubPartialError("rate limit")
            return {
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(b"# Codex\nCoding agent.").decode("ascii"),
            }
        if path == f"/repos/openai/codex/contents/docs/architecture.md?ref={SHA}":
            return {
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(b"# Architecture\nSandboxed tool execution.").decode("ascii"),
            }
        if path == f"/repos/openai/codex/git/trees/{SHA}?recursive=1":
            if self.fail_optional:
                raise GitHubPartialError("rate limit")
            return {"truncated": False, "tree": [{"type": "blob", "path": "README.md"}]}
        raise AssertionError(f"Unexpected GitHub API path: {path}")


def initialize_project(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "README.md").write_text("# Ai Notes\n", encoding="utf-8")
    (path / "CONTEXT.md").write_text("Project context.\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "README.md", "CONTEXT.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "initial",
        ],
        check=True,
    )


def write_policy(root: Path) -> None:
    config = root / "config"
    config.mkdir(parents=True)
    (config / "ai_notes_learning.yaml").write_text(
        """policy_version: "1"
retention_days: 30
max_external_evidence: 20
max_evidence_chars: 262144
max_tree_paths: 500
max_focus_files: 100
recognized_licenses: [Apache-2.0, MIT]
""",
        encoding="utf-8",
    )


class LearningPrepareTests(unittest.TestCase):
    def test_github_input_parser_accepts_supported_objects_and_rejects_non_github_hosts(self) -> None:
        cases = {
            "https://github.com/openai/codex": ("repository", None),
            "https://github.com/openai/codex/releases/tag/rust-v1.2.3": ("release", "rust-v1.2.3"),
            "https://github.com/openai/codex/pull/123": ("pull", "123"),
            "https://github.com/openai/codex/issues/456": ("issue", "456"),
            "https://github.com/openai/codex/tree/feature/test": ("tree", "feature/test"),
        }
        for url, expected in cases.items():
            parsed = parse_github_url(url)
            self.assertEqual(expected, (parsed.kind, parsed.selector))
        with self.assertRaises(ValueError):
            parse_github_url("https://example.com/openai/codex")
        with self.assertRaises(ValueError):
            parse_github_url("https://github.com/openai/codex?tab=readme")

    def test_repository_fork_defaults_to_verified_upstream(self) -> None:
        project = verify_project(FakeGitHubApi(fork=True), parse_github_url("https://github.com/someone/codex"))

        self.assertEqual("openai/codex", project.repository_id)
        self.assertTrue(project.fork_redirected)
        self.assertEqual("someone/codex", project.input.repository_id)

    def test_release_pr_issue_and_tree_inputs_resolve_to_exact_commits(self) -> None:
        class ObjectApi:
            def get_json(self, path: str) -> object:
                mapping: dict[str, object] = {
                    "/repos/openai/codex": repository_payload(),
                    "/repos/openai/codex/releases/tags/v1": {"tag_name": "v1", "target_commitish": "main", "body": "Release"},
                    "/repos/openai/codex/pulls/12": {"title": "PR", "body": "Change", "state": "open", "base": {"sha": "2" * 40}},
                    "/repos/openai/codex/issues/34": {"title": "Issue", "body": "Problem", "state": "open"},
                    "/repos/openai/codex/commits/main": {"sha": SHA},
                    "/repos/openai/codex/commits/feature%2Ftest": {"sha": "3" * 40},
                }
                if path not in mapping:
                    raise AssertionError(path)
                return mapping[path]

        expected = {
            "https://github.com/openai/codex/releases/tag/v1": SHA,
            "https://github.com/openai/codex/pull/12": "2" * 40,
            "https://github.com/openai/codex/issues/34": SHA,
            "https://github.com/openai/codex/tree/feature/test": "3" * 40,
        }
        for url, commit in expected.items():
            self.assertEqual(commit, verify_project(ObjectApi(), parse_github_url(url)).commit_sha)

    def test_public_api_rejects_redirect_to_non_github_host_before_following(self) -> None:
        response = SimpleNamespace(
            status_code=302,
            headers={"location": "https://evil.example/steal"},
        )

        class Client:
            def __enter__(self) -> "Client":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def get(self, url: str) -> object:
                return response

        with patch("ai_notes.github.httpx.Client", return_value=Client()):
            with self.assertRaises(GitHubError):
                PublicGitHubApi().get_json("/repos/openai/codex")

    def test_prepare_pins_commit_writes_bounded_queue_and_does_not_copy_owned_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "owned"
            initialize_project(project)
            write_policy(root)
            include = f"https://github.com/openai/codex/blob/{SHA}/docs/architecture.md"

            result = prepare_learning(
                root=root,
                github_url="https://github.com/openai/codex",
                project_path=project,
                include_urls=(include,),
                api=FakeGitHubApi(),
                run_id=None,
                now=datetime(2026, 9, 2, 8, 30, tzinfo=UTC),
            )
            queue = json.loads(result.queue_path.read_text(encoding="utf-8"))
            raw = json.loads(
                (root / "data" / "learning" / "raw" / result.run_id / "external-evidence.json").read_text(
                    encoding="utf-8"
                )
            )
            registry = (root / "data" / "learning" / "projects.yaml").read_text(encoding="utf-8")

        self.assertEqual("success", result.status)
        self.assertEqual("nominated", queue["input"]["entry_mode"])
        self.assertEqual(SHA, queue["verified_target"]["commit_sha"])
        self.assertEqual({"repository", "readme", "tree", "blob"}, {item["kind"] for item in queue["external_evidence"]})
        self.assertEqual(queue["external_evidence"], raw["evidence"])
        self.assertIn("README.md", {item["path"] for item in queue["owned_project"]["focus_files"]})
        self.assertNotIn("# Ai Notes", json.dumps(queue, ensure_ascii=False))
        self.assertIn(str(project.resolve()), registry)

    def test_optional_github_rate_limit_produces_partial_queue_not_empty_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "owned"
            initialize_project(project)
            write_policy(root)

            result = prepare_learning(
                root=root,
                github_url="https://github.com/openai/codex",
                project_path=project,
                api=FakeGitHubApi(fail_optional=True),
                now=datetime(2026, 9, 2, 8, 30, tzinfo=UTC),
            )
            queue = json.loads(result.queue_path.read_text(encoding="utf-8"))

        self.assertEqual("partial", result.status)
        self.assertGreaterEqual(len(queue["missing_scopes"]), 2)
        self.assertEqual(["repository"], [item["kind"] for item in queue["external_evidence"]])

    def test_rate_limit_before_commit_verification_writes_partial_manifest_without_fake_queue(self) -> None:
        class LimitedApi:
            def get_json(self, path: str) -> object:
                raise GitHubPartialError("anonymous rate limit exhausted")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "owned"
            initialize_project(project)
            write_policy(root)

            result = prepare_learning(
                root=root,
                github_url="https://github.com/openai/codex",
                project_path=project,
                api=LimitedApi(),
                now=datetime(2026, 9, 2, 8, 30, tzinfo=UTC),
            )
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual("partial", result.status)
        self.assertIsNone(result.queue_path)
        self.assertEqual("partial", manifest["status"])
        self.assertIsNone(manifest["queue_sha256"])
        self.assertIn("rate limit", manifest["missing_scopes"][0])

    def test_deterministic_verification_error_writes_failed_manifest_without_fake_queue(self) -> None:
        class BrokenApi:
            def get_json(self, path: str) -> object:
                raise GitHubError("invalid verified response")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "owned"
            initialize_project(project)
            write_policy(root)

            result = prepare_learning(
                root=root,
                github_url="https://github.com/openai/codex",
                project_path=project,
                api=BrokenApi(),
                now=datetime(2026, 9, 2, 8, 30, tzinfo=UTC),
            )
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual("failed", result.status)
        self.assertIsNone(result.queue_path)
        self.assertEqual("failed", manifest["status"])
        self.assertEqual([], manifest["missing_scopes"])
        self.assertIn("invalid verified response", manifest["validation_errors"][0])

    def test_include_requires_exact_verified_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "owned"
            initialize_project(project)
            write_policy(root)
            moving = "https://github.com/openai/codex/blob/main/docs/architecture.md"

            result = prepare_learning(
                root=root,
                github_url="https://github.com/openai/codex",
                project_path=project,
                include_urls=(moving,),
                api=FakeGitHubApi(),
                now=datetime(2026, 9, 2, 8, 30, tzinfo=UTC),
            )

        self.assertEqual("partial", result.status)
        self.assertIn("exact commit SHA", result.missing_scopes[0])

    def test_successful_include_retry_clears_its_previous_partial_scope(self) -> None:
        class RetryApi(FakeGitHubApi):
            def __init__(self) -> None:
                super().__init__()
                self.include_attempts = 0

            def get_json(self, path: str) -> object:
                if path == f"/repos/openai/codex/contents/docs/architecture.md?ref={SHA}":
                    self.include_attempts += 1
                    if self.include_attempts == 1:
                        raise GitHubPartialError("temporary rate limit")
                return super().get_json(path)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "owned"
            initialize_project(project)
            write_policy(root)
            include = f"https://github.com/openai/codex/blob/{SHA}/docs/architecture.md"
            api = RetryApi()
            first = prepare_learning(
                root=root,
                github_url="https://github.com/openai/codex",
                project_path=project,
                include_urls=(include,),
                api=api,
                now=datetime(2026, 9, 2, 8, 30, tzinfo=UTC),
            )
            recovered = prepare_learning(
                root=root,
                github_url="https://github.com/openai/codex",
                project_path=project,
                include_urls=(include,),
                api=api,
                run_id=first.run_id,
                now=datetime(2026, 9, 2, 8, 31, tzinfo=UTC),
            )
            queue = json.loads(recovered.queue_path.read_text(encoding="utf-8"))

        self.assertEqual("partial", first.status)
        self.assertEqual("success", recovered.status)
        self.assertEqual([], queue["missing_scopes"])
        self.assertIn("blob", {item["kind"] for item in queue["external_evidence"]})


if __name__ == "__main__":
    unittest.main()
