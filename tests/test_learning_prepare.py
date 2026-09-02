from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_notes.github import (
    GitHubError,
    GitHubPartialError,
    PublicGitHubApi,
    PublicGitHubArchive,
    parse_github_url,
    verify_project,
)
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


class FakeEvidenceFallback:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def read_file(self, repository_id: str, path: str, commit_sha: str, *, max_bytes: int) -> str:
        self.calls.append(f"read:{repository_id}:{path}:{commit_sha}:{max_bytes}")
        return "# Codex\nCoding agent."

    def list_files(self, repository_id: str, commit_sha: str, *, max_archive_bytes: int) -> list[str]:
        self.calls.append(f"tree:{repository_id}:{commit_sha}:{max_archive_bytes}")
        return ["README.md", "docs/architecture.md"]


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


def write_discovery(path: Path, *, adjacent_count: int = 1, selected: str = "openai/codex") -> None:
    candidates = [
        {
            "repository": "openai/codex",
            "url": "https://github.com/openai/codex",
            "lane": "direct",
            "description": "A coding agent",
        },
        {
            "repository": "example/one",
            "url": "https://github.com/example/one",
            "lane": "direct",
            "description": "Direct candidate one",
        },
        {
            "repository": "example/two",
            "url": "https://github.com/example/two",
            "lane": "direct",
            "description": "Direct candidate two",
        },
        {
            "repository": "example/three",
            "url": "https://github.com/example/three",
            "lane": "direct",
            "description": "Direct candidate three",
        },
    ]
    for index in range(adjacent_count):
        candidates.append(
            {
                "repository": f"adjacent/project-{index}",
                "url": f"https://github.com/adjacent/project-{index}",
                "lane": "adjacent",
                "description": "Adjacent candidate",
            }
        )
    path.write_text(
        json.dumps(
            {
                "search_queries": [
                    {"query": "coding agent repository", "lane": "direct"},
                    {"query": "architecture decisions", "lane": "adjacent"},
                ],
                "screened_candidates": candidates,
                "deep_read_repositories": [item["repository"] for item in candidates[:5]],
                "selected_repository": selected,
            }
        ),
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

    def test_public_archive_rejects_redirect_and_enforces_bounded_stream(self) -> None:
        class Response:
            def __init__(self, status_code: int, *, headers: dict[str, str] | None = None, chunks: list[bytes] | None = None) -> None:
                self.status_code = status_code
                self.headers = headers or {}
                self._chunks = chunks or []

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def iter_bytes(self):
                yield from self._chunks

        class Client:
            def __init__(self, response: Response) -> None:
                self.response = response

            def __enter__(self) -> "Client":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def stream(self, method: str, url: str) -> Response:
                return self.response

        redirect = Response(302, headers={"location": "https://evil.example/archive.zip"})
        with patch("ai_notes.github.httpx.Client", return_value=Client(redirect)):
            with self.assertRaisesRegex(GitHubError, "approved hosts"):
                PublicGitHubArchive().read_file("openai/codex", "README.md", SHA, max_bytes=100)

        oversized = Response(200, chunks=[b"123", b"456"])
        with patch("ai_notes.github.httpx.Client", return_value=Client(oversized)):
            with self.assertRaisesRegex(GitHubPartialError, "size limit"):
                PublicGitHubArchive().read_file("openai/codex", "README.md", SHA, max_bytes=5)

    def test_public_archive_lists_files_without_extracting_zip(self) -> None:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr(f"codex-{SHA}/README.md", "readme")
            archive.writestr(f"codex-{SHA}/docs/architecture.md", "architecture")

        class Response:
            status_code = 200
            headers: dict[str, str] = {}

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def iter_bytes(self):
                yield payload.getvalue()

        class Client:
            def __enter__(self) -> "Client":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def stream(self, method: str, url: str) -> Response:
                return Response()

        with patch("ai_notes.github.httpx.Client", return_value=Client()):
            files = PublicGitHubArchive().list_files("openai/codex", SHA, max_archive_bytes=10_000)

        self.assertEqual(["README.md", "docs/architecture.md"], files)

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
        self.assertIsNone(queue["input"]["discovery"])
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

    def test_fixed_commit_archive_fallback_recovers_optional_api_rate_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "owned"
            initialize_project(project)
            write_policy(root)
            fallback = FakeEvidenceFallback()

            result = prepare_learning(
                root=root,
                github_url="https://github.com/openai/codex",
                project_path=project,
                api=FakeGitHubApi(fail_optional=True),
                fallback=fallback,
                now=datetime(2026, 9, 2, 8, 30, tzinfo=UTC),
            )
            queue = json.loads(result.queue_path.read_text(encoding="utf-8"))

        self.assertEqual("success", result.status)
        self.assertEqual([], queue["missing_scopes"])
        self.assertEqual({"repository", "readme", "tree"}, {item["kind"] for item in queue["external_evidence"]})
        self.assertEqual(2, len(fallback.calls))

    def test_fixed_commit_fallback_recovers_pinned_include_rate_limit(self) -> None:
        class IncludeLimitedApi(FakeGitHubApi):
            def get_json(self, path: str) -> object:
                if path == f"/repos/openai/codex/contents/docs/architecture.md?ref={SHA}":
                    raise GitHubPartialError("rate limit")
                return super().get_json(path)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "owned"
            initialize_project(project)
            write_policy(root)
            fallback = FakeEvidenceFallback()
            include = f"https://github.com/openai/codex/blob/{SHA}/docs/architecture.md"

            result = prepare_learning(
                root=root,
                github_url="https://github.com/openai/codex",
                project_path=project,
                include_urls=(include,),
                api=IncludeLimitedApi(),
                fallback=fallback,
                now=datetime(2026, 9, 2, 8, 30, tzinfo=UTC),
            )
            queue = json.loads(result.queue_path.read_text(encoding="utf-8"))

        self.assertEqual("success", result.status)
        self.assertIn("blob", {item["kind"] for item in queue["external_evidence"]})
        self.assertTrue(any(call.startswith("read:openai/codex:docs/architecture.md") for call in fallback.calls))

    def test_standard_evidence_retry_recovers_readme_and_tree_without_repinning_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "owned"
            initialize_project(project)
            write_policy(root)
            api = FakeGitHubApi(fail_optional=True)
            first = prepare_learning(
                root=root,
                github_url="https://github.com/openai/codex",
                project_path=project,
                api=api,
                now=datetime(2026, 9, 2, 8, 30, tzinfo=UTC),
            )
            api.fail_optional = False
            recovered = prepare_learning(
                root=root,
                github_url="https://github.com/openai/codex",
                project_path=project,
                api=api,
                run_id=first.run_id,
                now=datetime(2026, 9, 2, 8, 31, tzinfo=UTC),
            )
            queue = json.loads(recovered.queue_path.read_text(encoding="utf-8"))

        self.assertEqual("partial", first.status)
        self.assertEqual("success", recovered.status)
        self.assertEqual([], queue["missing_scopes"])
        self.assertEqual({"repository", "readme", "tree"}, {item["kind"] for item in queue["external_evidence"]})
        self.assertEqual(SHA, queue["verified_target"]["commit_sha"])

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

    def test_discovered_run_requires_and_embeds_bounded_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "owned"
            initialize_project(project)
            write_policy(root)
            provenance = root / "discovery.json"
            write_discovery(provenance)

            result = prepare_learning(
                root=root,
                github_url="https://github.com/openai/codex",
                project_path=project,
                entry_mode="discovered",
                discovery_input_path=provenance,
                api=FakeGitHubApi(),
                now=datetime(2026, 9, 2, 8, 30, tzinfo=UTC),
            )
            queue = json.loads(result.queue_path.read_text(encoding="utf-8"))

        self.assertEqual("success", result.status)
        self.assertEqual("discovered", queue["input"]["entry_mode"])
        self.assertEqual("openai/codex", queue["input"]["discovery"]["selected_repository"])
        self.assertEqual(5, len(queue["input"]["discovery"]["screened_candidates"]))

    def test_discovered_run_fails_closed_without_provenance_or_when_adjacent_budget_is_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "owned"
            initialize_project(project)
            write_policy(root)

            missing = prepare_learning(
                root=root,
                github_url="https://github.com/openai/codex",
                project_path=project,
                entry_mode="discovered",
                api=FakeGitHubApi(),
                now=datetime(2026, 9, 2, 8, 30, tzinfo=UTC),
            )
            provenance = root / "discovery.json"
            write_discovery(provenance, adjacent_count=2)
            exceeded = prepare_learning(
                root=root,
                github_url="https://github.com/openai/codex",
                project_path=project,
                entry_mode="discovered",
                discovery_input_path=provenance,
                api=FakeGitHubApi(),
                now=datetime(2026, 9, 2, 8, 31, tzinfo=UTC),
            )
            missing_manifest = json.loads(missing.manifest_path.read_text(encoding="utf-8"))
            exceeded_manifest = json.loads(exceeded.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual("failed", missing.status)
        self.assertIn("requires a bounded", missing_manifest["validation_errors"][0])
        self.assertEqual("failed", exceeded.status)
        self.assertIn("20%", exceeded_manifest["validation_errors"][0])


if __name__ == "__main__":
    unittest.main()
