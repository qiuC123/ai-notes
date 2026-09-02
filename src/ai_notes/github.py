from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, unquote, urljoin, urlparse

import httpx

from aihot.http import build_verified_ssl_context


_OWNER_REPO = re.compile(r"^[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


class GitHubError(RuntimeError):
    pass


class GitHubNotFoundError(GitHubError):
    pass


class GitHubPartialError(GitHubError):
    pass


@dataclass(frozen=True, slots=True)
class GitHubInput:
    original_url: str
    owner: str
    repository: str
    kind: str
    selector: str | None

    @property
    def repository_id(self) -> str:
        return f"{self.owner}/{self.repository}"


@dataclass(frozen=True, slots=True)
class VerifiedGitHubProject:
    input: GitHubInput
    repository_id: str
    canonical_url: str
    commit_sha: str
    ref: str
    object_url: str
    metadata: dict[str, Any]
    target_payload: dict[str, Any] | None
    fork_redirected: bool
    upstream_repository: str | None


class GitHubJsonApi(Protocol):
    def get_json(self, path: str) -> object:
        ...


class GitHubEvidenceFallback(Protocol):
    def read_file(self, repository_id: str, path: str, commit_sha: str, *, max_bytes: int) -> str:
        ...

    def list_files(self, repository_id: str, commit_sha: str, *, max_archive_bytes: int) -> list[str]:
        ...


def parse_github_url(value: str) -> GitHubInput:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "github.com":
        raise ValueError("Only public https://github.com URLs are supported")
    if parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment:
        raise ValueError("GitHub URL must not contain credentials, a custom port, query, or fragment")
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise ValueError("GitHub URL must identify a repository")
    owner, repository = parts[0], parts[1]
    if repository.endswith(".git"):
        repository = repository[:-4]
    if not _OWNER_REPO.fullmatch(owner) or not _OWNER_REPO.fullmatch(repository):
        raise ValueError("GitHub owner or repository contains unsupported characters")
    tail = parts[2:]
    if not tail:
        return GitHubInput(value, owner, repository, "repository", None)
    if len(tail) >= 3 and tail[:2] == ["releases", "tag"]:
        return GitHubInput(value, owner, repository, "release", "/".join(tail[2:]))
    if len(tail) == 2 and tail[0] in {"pull", "issues"} and tail[1].isdigit():
        kind = "pull" if tail[0] == "pull" else "issue"
        return GitHubInput(value, owner, repository, kind, tail[1])
    if len(tail) >= 2 and tail[0] == "tree":
        return GitHubInput(value, owner, repository, "tree", "/".join(tail[1:]))
    raise ValueError("Supported GitHub inputs are repository, Release, PR, Issue, or tree URLs")


class PublicGitHubApi:
    def __init__(self, *, timeout_seconds: float = 30.0, max_redirects: int = 3) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_redirects = max_redirects

    def get_json(self, path: str) -> object:
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = urljoin("https://api.github.com/", path.lstrip("/"))
        for _ in range(self.max_redirects + 1):
            parsed = urlparse(url)
            if parsed.scheme != "https" or (parsed.hostname or "").lower() != "api.github.com":
                raise GitHubError(f"GitHub API redirect left the approved host: {url}")
            try:
                with httpx.Client(
                    timeout=self.timeout_seconds,
                    follow_redirects=False,
                    verify=build_verified_ssl_context(),
                    headers={
                        "User-Agent": "Ai-Notes/0.3 (+https://github.com/openai/codex)",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                ) as client:
                    response = client.get(url)
            except (httpx.HTTPError, OSError) as error:
                raise GitHubPartialError(f"GitHub API request failed: {type(error).__name__}: {error}") from error
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise GitHubError("GitHub API redirect omitted Location")
                url = urljoin(url, location)
                continue
            if response.status_code == 404:
                raise GitHubNotFoundError(f"GitHub object was not found: {url}")
            if response.status_code in {403, 429}:
                remaining = response.headers.get("x-ratelimit-remaining")
                reason = "anonymous rate limit exhausted" if remaining == "0" else f"HTTP {response.status_code}"
                raise GitHubPartialError(f"GitHub API {reason}: {url}")
            if response.status_code >= 400:
                raise GitHubError(f"GitHub API HTTP {response.status_code}: {url}")
            try:
                return response.json()
            except json.JSONDecodeError as error:
                raise GitHubError(f"GitHub API returned invalid JSON: {url}") from error
        raise GitHubError("GitHub API exceeded the approved redirect limit")


class PublicGitHubArchive:
    _APPROVED_HOSTS = {"raw.githubusercontent.com", "codeload.github.com"}

    def __init__(self, *, timeout_seconds: float = 60.0, max_redirects: int = 3) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_redirects = max_redirects

    def _read_bounded(self, url: str, *, max_bytes: int) -> bytes:
        for _ in range(self.max_redirects + 1):
            parsed = urlparse(url)
            if parsed.scheme != "https" or (parsed.hostname or "").lower() not in self._APPROVED_HOSTS:
                raise GitHubError(f"GitHub evidence redirect left the approved hosts: {url}")
            redirect: str | None = None
            try:
                with httpx.Client(
                    timeout=self.timeout_seconds,
                    follow_redirects=False,
                    verify=build_verified_ssl_context(),
                    headers={"User-Agent": "Ai-Notes/0.3 (+https://github.com/openai/codex)"},
                ) as client:
                    with client.stream("GET", url) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise GitHubError("GitHub evidence redirect omitted Location")
                            redirect = urljoin(url, location)
                        elif response.status_code == 404:
                            raise GitHubNotFoundError(f"GitHub evidence was not found: {url}")
                        elif response.status_code in {403, 429}:
                            raise GitHubPartialError(f"GitHub evidence HTTP {response.status_code}: {url}")
                        elif response.status_code >= 400:
                            raise GitHubError(f"GitHub evidence HTTP {response.status_code}: {url}")
                        else:
                            chunks: list[bytes] = []
                            size = 0
                            for chunk in response.iter_bytes():
                                size += len(chunk)
                                if size > max_bytes:
                                    raise GitHubPartialError(f"GitHub evidence exceeds the configured size limit: {url}")
                                chunks.append(chunk)
                            return b"".join(chunks)
            except (httpx.HTTPError, OSError) as error:
                raise GitHubPartialError(
                    f"GitHub evidence request failed: {type(error).__name__}: {error}"
                ) from error
            if redirect is not None:
                url = redirect
                continue
        raise GitHubError("GitHub evidence exceeded the approved redirect limit")

    def read_file(self, repository_id: str, path: str, commit_sha: str, *, max_bytes: int) -> str:
        url = f"https://raw.githubusercontent.com/{repository_id}/{commit_sha}/{quote(path, safe='/')}"
        payload = self._read_bounded(url, max_bytes=max_bytes)
        return payload.decode("utf-8", errors="replace")

    def list_files(self, repository_id: str, commit_sha: str, *, max_archive_bytes: int) -> list[str]:
        url = f"https://codeload.github.com/{repository_id}/zip/{commit_sha}"
        payload = self._read_bounded(url, max_bytes=max_archive_bytes)
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                paths: list[str] = []
                for item in archive.infolist():
                    if item.is_dir():
                        continue
                    parts = item.filename.split("/", 1)
                    if len(parts) != 2 or not parts[1] or ".." in parts[1].split("/"):
                        continue
                    paths.append(parts[1])
        except zipfile.BadZipFile as error:
            raise GitHubError("GitHub codeload response is not a valid ZIP archive") from error
        return sorted(set(paths))


def _require_object(payload: object, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise GitHubError(f"GitHub {label} response is not an object")
    return payload


def _resolve_commit(api: GitHubJsonApi, repository_id: str, ref: str) -> str:
    payload = _require_object(
        api.get_json(f"/repos/{repository_id}/commits/{quote(ref, safe='')}") ,
        "commit",
    )
    sha = payload.get("sha")
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise GitHubError("GitHub commit response omitted a full SHA")
    return sha


def _resolve_tree_ref(api: GitHubJsonApi, repository_id: str, selector: str) -> tuple[str, str]:
    parts = [part for part in selector.split("/") if part]
    for length in range(len(parts), 0, -1):
        ref = "/".join(parts[:length])
        try:
            return ref, _resolve_commit(api, repository_id, ref)
        except GitHubNotFoundError:
            continue
    raise GitHubNotFoundError(f"GitHub tree ref was not found: {selector}")


def verify_project(api: GitHubJsonApi, github_input: GitHubInput) -> VerifiedGitHubProject:
    requested_repository = github_input.repository_id
    requested_metadata = _require_object(api.get_json(f"/repos/{requested_repository}"), "repository")
    full_name = requested_metadata.get("full_name")
    if not isinstance(full_name, str) or full_name.lower() != requested_repository.lower():
        raise GitHubError("GitHub repository identity did not match the requested owner/repository")

    fork_redirected = bool(requested_metadata.get("fork"))
    upstream_repository: str | None = None
    metadata = requested_metadata
    repository_id = requested_repository
    if fork_redirected:
        if github_input.kind != "repository":
            raise GitHubError("Fork-specific objects require an explicit future fork-study mode")
        parent = requested_metadata.get("parent")
        upstream_repository = parent.get("full_name") if isinstance(parent, dict) else None
        if not isinstance(upstream_repository, str) or "/" not in upstream_repository:
            raise GitHubError("Fork metadata omitted a verifiable upstream repository")
        repository_id = upstream_repository
        metadata = _require_object(api.get_json(f"/repos/{repository_id}"), "upstream repository")

    default_branch = metadata.get("default_branch")
    if not isinstance(default_branch, str) or not default_branch:
        raise GitHubError("GitHub repository omitted its default branch")
    target_payload: dict[str, Any] | None = None
    ref = default_branch
    if github_input.kind == "release":
        target_payload = _require_object(
            api.get_json(f"/repos/{repository_id}/releases/tags/{quote(github_input.selector or '', safe='')}") ,
            "release",
        )
        target_ref = target_payload.get("target_commitish")
        ref = target_ref if isinstance(target_ref, str) and target_ref else default_branch
        commit_sha = _resolve_commit(api, repository_id, ref)
    elif github_input.kind == "pull":
        target_payload = _require_object(api.get_json(f"/repos/{repository_id}/pulls/{github_input.selector}"), "pull")
        base = target_payload.get("base")
        base_sha = base.get("sha") if isinstance(base, dict) else None
        if not isinstance(base_sha, str) or not _SHA.fullmatch(base_sha):
            raise GitHubError("GitHub PR response omitted its base commit SHA")
        commit_sha = base_sha
        ref = f"pull/{github_input.selector}@base"
    elif github_input.kind == "issue":
        target_payload = _require_object(api.get_json(f"/repos/{repository_id}/issues/{github_input.selector}"), "issue")
        commit_sha = _resolve_commit(api, repository_id, default_branch)
    elif github_input.kind == "tree":
        ref, commit_sha = _resolve_tree_ref(api, repository_id, github_input.selector or "")
    else:
        commit_sha = _resolve_commit(api, repository_id, default_branch)

    canonical_url = f"https://github.com/{repository_id}"
    object_url = canonical_url if github_input.kind == "repository" else github_input.original_url
    if github_input.kind in {"repository", "tree"}:
        object_url = f"{canonical_url}/tree/{commit_sha}"
    return VerifiedGitHubProject(
        input=github_input,
        repository_id=repository_id,
        canonical_url=canonical_url,
        commit_sha=commit_sha,
        ref=ref,
        object_url=object_url,
        metadata=metadata,
        target_payload=target_payload,
        fork_redirected=fork_redirected,
        upstream_repository=upstream_repository,
    )


def read_repository_file(api: GitHubJsonApi, repository_id: str, path: str, commit_sha: str, *, max_bytes: int) -> str:
    payload = _require_object(
        api.get_json(f"/repos/{repository_id}/contents/{quote(path, safe='/')}?ref={commit_sha}"),
        "content",
    )
    if payload.get("type") != "file" or payload.get("encoding") != "base64":
        raise GitHubError(f"GitHub content is not a base64 file: {path}")
    content = payload.get("content")
    if not isinstance(content, str):
        raise GitHubError(f"GitHub content omitted base64 data: {path}")
    try:
        decoded = base64.b64decode(content, validate=False)
    except ValueError as error:
        raise GitHubError(f"GitHub content had invalid base64: {path}") from error
    if len(decoded) > max_bytes:
        raise GitHubPartialError(f"GitHub content exceeds the configured size limit: {path}")
    return decoded.decode("utf-8", errors="replace")


def parse_pinned_blob_url(url: str, repository_id: str, commit_sha: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "github.com" or parsed.query or parsed.fragment:
        raise ValueError("Included evidence must be an official GitHub blob URL")
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    owner, repository = repository_id.split("/", 1)
    expected = [owner.lower(), repository.lower(), "blob", commit_sha]
    if len(parts) < 5 or [parts[0].lower(), parts[1].lower(), parts[2], parts[3]] != expected:
        raise ValueError("Included blob evidence must use the verified repository and exact commit SHA")
    path = "/".join(parts[4:])
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise ValueError("Included blob path is invalid")
    return path
