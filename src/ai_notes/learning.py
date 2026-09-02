from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from ai_notes.contracts import MANIFEST_SCHEMA, QUEUE_SCHEMA, validate_contract
from ai_notes.github import (
    GitHubError,
    GitHubJsonApi,
    GitHubPartialError,
    PublicGitHubApi,
    VerifiedGitHubProject,
    parse_github_url,
    parse_pinned_blob_url,
    read_repository_file,
    verify_project,
)
from ai_notes.storage import (
    RunLock,
    focus_files,
    project_fingerprint,
    prune_learning_artifacts,
    sha256_bytes,
    write_bytes_atomic,
    write_json_atomic,
)


@dataclass(frozen=True, slots=True)
class LearningPolicy:
    policy_version: str = "1"
    retention_days: int = 30
    max_external_evidence: int = 20
    max_evidence_chars: int = 262_144
    max_tree_paths: int = 500
    max_focus_files: int = 100
    recognized_licenses: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PrepareResult:
    run_id: str
    queue_path: Path | None
    manifest_path: Path | None
    status: str
    missing_scopes: tuple[str, ...]


def load_learning_policy(path: Path) -> LearningPolicy:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Learning policy must be a YAML object")
    return LearningPolicy(
        policy_version=str(payload.get("policy_version", "1")),
        retention_days=int(payload.get("retention_days", 30)),
        max_external_evidence=int(payload.get("max_external_evidence", 20)),
        max_evidence_chars=int(payload.get("max_evidence_chars", 262_144)),
        max_tree_paths=int(payload.get("max_tree_paths", 500)),
        max_focus_files=int(payload.get("max_focus_files", 100)),
        recognized_licenses=tuple(str(item) for item in payload.get("recognized_licenses", [])),
    )


def _new_run_id(now: datetime, github_url: str, project: Path) -> str:
    timestamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    entropy = secrets.token_hex(8)
    suffix = hashlib.sha256(f"{github_url}\n{project.resolve()}\n{entropy}".encode("utf-8")).hexdigest()[:8]
    return f"{timestamp}-{suffix}"


def _evidence(kind: str, url: str, title: str, text: str, commit_sha: str | None) -> dict[str, Any]:
    text_hash = sha256_bytes(text.encode("utf-8"))
    identity = f"{kind}\n{url}\n{commit_sha or ''}\n{text_hash}"
    return {
        "evidence_id": "ext-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12],
        "kind": kind,
        "official_url": url,
        "commit_sha": commit_sha,
        "title": title[:500] or kind,
        "text": text,
        "text_sha256": text_hash,
    }


def _repository_summary(project: VerifiedGitHubProject) -> str:
    metadata = project.metadata
    fields = {
        "full_name": metadata.get("full_name"),
        "description": metadata.get("description"),
        "homepage": metadata.get("homepage"),
        "default_branch": metadata.get("default_branch"),
        "archived": metadata.get("archived"),
        "fork": metadata.get("fork"),
        "topics": metadata.get("topics") if isinstance(metadata.get("topics"), list) else [],
        "language": metadata.get("language"),
        "license": metadata.get("license"),
    }
    return json.dumps(fields, ensure_ascii=False, indent=2, sort_keys=True)


def _target_text(project: VerifiedGitHubProject) -> tuple[str, str] | None:
    payload = project.target_payload
    if payload is None:
        return None
    if project.input.kind == "release":
        title = str(payload.get("name") or payload.get("tag_name") or "Release")
        body = str(payload.get("body") or "")
    else:
        title = str(payload.get("title") or project.input.kind)
        body = str(payload.get("body") or "")
    text = json.dumps({"title": title, "body": body, "state": payload.get("state")}, ensure_ascii=False, indent=2)
    return title, text


def _source_risk(project: VerifiedGitHubProject, policy: LearningPolicy) -> dict[str, Any]:
    metadata = project.metadata
    license_payload = metadata.get("license")
    spdx = license_payload.get("spdx_id") if isinstance(license_payload, dict) else None
    if not isinstance(spdx, str) or spdx in {"", "NOASSERTION", "OTHER"}:
        spdx = None
    reasons: list[str] = []
    if project.fork_redirected:
        reasons.append("fork_redirected_to_upstream")
    if bool(metadata.get("archived")):
        reasons.append("archived_repository")
    if spdx is None:
        reasons.append("license_unknown")
    elif policy.recognized_licenses and spdx not in policy.recognized_licenses:
        reasons.append("license_requires_review")
    learning_only = any(reason in {"archived_repository", "license_unknown", "license_requires_review"} for reason in reasons)
    return {
        "fork": project.fork_redirected,
        "upstream_repository": project.upstream_repository,
        "archived": bool(metadata.get("archived")),
        "license_spdx": spdx,
        "learning_only": learning_only,
        "reasons": reasons,
    }


def _collect_standard_evidence(
    api: GitHubJsonApi,
    project: VerifiedGitHubProject,
    policy: LearningPolicy,
) -> tuple[list[dict[str, Any]], list[str]]:
    evidence = [
        _evidence("repository", project.canonical_url, "Repository metadata", _repository_summary(project), project.commit_sha)
    ]
    missing: list[str] = []
    try:
        readme = read_repository_file(
            api,
            project.repository_id,
            "README.md",
            project.commit_sha,
            max_bytes=policy.max_evidence_chars,
        )
        evidence.append(
            _evidence(
                "readme",
                f"{project.canonical_url}/blob/{project.commit_sha}/README.md",
                "README.md",
                readme,
                project.commit_sha,
            )
        )
    except GitHubError as error:
        missing.append(f"readme: {type(error).__name__}: {error}")

    try:
        tree_payload = api.get_json(f"/repos/{project.repository_id}/git/trees/{project.commit_sha}?recursive=1")
        if not isinstance(tree_payload, dict) or not isinstance(tree_payload.get("tree"), list):
            raise GitHubError("GitHub tree response is invalid")
        paths = [
            str(item.get("path"))
            for item in tree_payload["tree"]
            if isinstance(item, dict) and item.get("type") == "blob" and isinstance(item.get("path"), str)
        ]
        if tree_payload.get("truncated"):
            missing.append("tree: GitHub recursive tree was truncated")
        tree_text = "\n".join(paths[: policy.max_tree_paths])
        evidence.append(
            _evidence(
                "tree",
                f"{project.canonical_url}/tree/{project.commit_sha}",
                "Repository file tree",
                tree_text,
                project.commit_sha,
            )
        )
    except GitHubError as error:
        missing.append(f"tree: {type(error).__name__}: {error}")

    target = _target_text(project)
    if target is not None:
        title, text = target
        evidence.append(_evidence(project.input.kind, project.object_url, title, text, project.commit_sha))
    return evidence, missing


def _retry_missing_standard_evidence(
    api: GitHubJsonApi,
    project: VerifiedGitHubProject,
    policy: LearningPolicy,
    evidence: list[dict[str, Any]],
    missing: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    retry_readme = any(item.startswith("readme:") for item in missing)
    retry_tree = any(item.startswith("tree:") for item in missing)
    if retry_readme:
        missing = [item for item in missing if not item.startswith("readme:")]
        evidence = [item for item in evidence if item["kind"] != "readme"]
        try:
            readme = read_repository_file(
                api,
                project.repository_id,
                "README.md",
                project.commit_sha,
                max_bytes=policy.max_evidence_chars,
            )
            evidence.append(
                _evidence(
                    "readme",
                    f"{project.canonical_url}/blob/{project.commit_sha}/README.md",
                    "README.md",
                    readme,
                    project.commit_sha,
                )
            )
        except GitHubError as error:
            missing.append(f"readme: {type(error).__name__}: {error}")

    if retry_tree:
        missing = [item for item in missing if not item.startswith("tree:")]
        evidence = [item for item in evidence if item["kind"] != "tree"]
        try:
            tree_payload = api.get_json(f"/repos/{project.repository_id}/git/trees/{project.commit_sha}?recursive=1")
            if not isinstance(tree_payload, dict) or not isinstance(tree_payload.get("tree"), list):
                raise GitHubError("GitHub tree response is invalid")
            paths = [
                str(item.get("path"))
                for item in tree_payload["tree"]
                if isinstance(item, dict) and item.get("type") == "blob" and isinstance(item.get("path"), str)
            ]
            if tree_payload.get("truncated"):
                missing.append("tree: GitHub recursive tree was truncated")
            evidence.append(
                _evidence(
                    "tree",
                    f"{project.canonical_url}/tree/{project.commit_sha}",
                    "Repository file tree",
                    "\n".join(paths[: policy.max_tree_paths]),
                    project.commit_sha,
                )
            )
        except GitHubError as error:
            missing.append(f"tree: {type(error).__name__}: {error}")
    return evidence, missing


def _register_project(root: Path, owned_project: dict[str, Any]) -> None:
    path = root / "data" / "learning" / "projects.yaml"
    if path.exists():
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    else:
        payload = {}
    if not isinstance(payload, dict) or not isinstance(payload.get("projects", []), list):
        raise ValueError("Local project registry is invalid")
    projects = [item for item in payload.get("projects", []) if isinstance(item, dict)]
    canonical = str(owned_project["root"])
    entry = {
        "name": owned_project["name"],
        "path": canonical,
        "repository_id": owned_project["repository_id"],
        "reason": "First authorized Ai Notes co-learning project",
    }
    projects = [item for item in projects if item.get("path") != canonical]
    projects.append(entry)
    rendered = yaml.safe_dump({"projects": projects}, allow_unicode=True, sort_keys=True)
    write_bytes_atomic(path, rendered.encode("utf-8"))


def _load_discovery_provenance(path: Path, github_url: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Discovery provenance must be a JSON object")
    required = {"search_queries", "screened_candidates", "deep_read_repositories", "selected_repository"}
    if set(payload) != required:
        raise ValueError("Discovery provenance has missing or unknown fields")
    queries = payload["search_queries"]
    candidates = payload["screened_candidates"]
    deep_reads = payload["deep_read_repositories"]
    selected = payload["selected_repository"]
    if not isinstance(queries, list) or not 1 <= len(queries) <= 5:
        raise ValueError("Discovery provenance requires 1 to 5 search queries")
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 20:
        raise ValueError("Discovery provenance requires 1 to 20 screened candidates")
    if not isinstance(deep_reads, list) or not 1 <= len(deep_reads) <= 5:
        raise ValueError("Discovery provenance requires 1 to 5 deep-read repositories")
    if not isinstance(selected, str):
        raise ValueError("Discovery provenance selected_repository must be a string")

    normalized_candidates: list[dict[str, Any]] = []
    candidate_ids: set[str] = set()
    lane_counts = {"direct": 0, "adjacent": 0}
    for item in candidates:
        if not isinstance(item, dict) or set(item) != {"repository", "url", "lane", "description"}:
            raise ValueError("Each screened candidate must contain repository, url, lane, and description")
        parsed = parse_github_url(str(item["url"]))
        if parsed.kind != "repository" or parsed.repository_id != item["repository"]:
            raise ValueError("Screened candidate URL and repository identity do not match")
        if parsed.repository_id in candidate_ids:
            raise ValueError(f"Duplicate screened candidate: {parsed.repository_id}")
        lane = item["lane"]
        if lane not in lane_counts:
            raise ValueError(f"Unsupported discovery lane: {lane}")
        if not isinstance(item["description"], str) or len(item["description"]) > 1000:
            raise ValueError("Screened candidate description must be a string of at most 1000 characters")
        candidate_ids.add(parsed.repository_id)
        lane_counts[lane] += 1
        normalized_candidates.append(dict(item))
    if lane_counts["adjacent"] and lane_counts["direct"] < 4 * lane_counts["adjacent"]:
        raise ValueError("Discovery provenance exceeds the 20% adjacent-exploration budget")

    normalized_queries: list[dict[str, str]] = []
    for item in queries:
        if not isinstance(item, dict) or set(item) != {"query", "lane"}:
            raise ValueError("Each discovery query must contain query and lane")
        query = item["query"]
        lane = item["lane"]
        if not isinstance(query, str) or not query.strip() or len(query) > 500 or lane not in lane_counts:
            raise ValueError("Discovery query is invalid")
        normalized_queries.append({"query": query, "lane": lane})

    if (
        not all(isinstance(item, str) for item in deep_reads)
        or len(set(deep_reads)) != len(deep_reads)
        or not all(item in candidate_ids for item in deep_reads)
    ):
        raise ValueError("Deep-read repositories must be unique screened candidates")
    target = parse_github_url(github_url)
    if target.kind != "repository":
        raise ValueError("Active discovery currently supports repository URLs only")
    if selected != target.repository_id or selected not in deep_reads:
        raise ValueError("Selected repository must match the learning target and be included in deep reads")
    return {
        "search_queries": normalized_queries,
        "screened_candidates": normalized_candidates,
        "deep_read_repositories": list(deep_reads),
        "selected_repository": selected,
    }


def _write_unprepared_manifest(
    *, path: Path, run_id: str, created_at: str, policy: LearningPolicy, status: str, reason: str
) -> None:
    if status not in {"partial", "failed"}:
        raise ValueError(f"Unsupported unprepared manifest status: {status}")
    retained = datetime.fromisoformat(created_at.replace("Z", "+00:00")) + timedelta(days=policy.retention_days)
    payload = {
        "schema_version": MANIFEST_SCHEMA,
        "run_id": run_id,
        "started_at": created_at,
        "finished_at": created_at,
        "status": status,
        "healthy_no_connection": False,
        "policy_version": policy.policy_version,
        "queue_path": f"outputs/learning/{run_id}/learning-queue.json",
        "decisions_path": None,
        "queue_sha256": None,
        "decisions_sha256": None,
        "missing_scopes": [reason] if status == "partial" else [],
        "validation_errors": [reason] if status == "failed" else [],
        "retained_until": retained.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    validate_contract(MANIFEST_SCHEMA, payload)
    write_json_atomic(path, payload)


def prepare_learning(
    *,
    root: Path,
    github_url: str,
    project_path: Path,
    include_urls: tuple[str, ...] = (),
    entry_mode: str = "nominated",
    discovery_input_path: Path | None = None,
    api: GitHubJsonApi | None = None,
    run_id: str | None = None,
    now: datetime | None = None,
) -> PrepareResult:
    if entry_mode not in {"nominated", "discovered"}:
        raise ValueError(f"Unsupported learning entry mode: {entry_mode}")
    resolved_root = root.resolve()
    resolved_project = project_path.resolve()
    policy = load_learning_policy(resolved_root / "config" / "ai_notes_learning.yaml")
    current = now or datetime.now(UTC)
    identifier = run_id or _new_run_id(current, github_url, resolved_project)
    output_dir = resolved_root / "outputs" / "learning" / identifier
    raw_dir = resolved_root / "data" / "learning" / "raw" / identifier
    queue_path = output_dir / "learning-queue.json"
    manifest_path = output_dir / "learning-run-manifest.json"
    lock_path = resolved_root / "data" / "learning" / "ai-notes-learning.lock"
    api_client = api or PublicGitHubApi()

    with RunLock(lock_path):
        if manifest_path.exists():
            raise ValueError(f"Finalized learning run cannot be extended: {identifier}")
        prune_learning_artifacts(resolved_root, as_of=current.date(), retention_days=policy.retention_days)
        owned = project_fingerprint(resolved_project)
        owned["focus_files"] = focus_files(resolved_project, limit=policy.max_focus_files)

        if run_id is not None:
            if not queue_path.exists():
                raise ValueError(f"Learning run does not exist: {identifier}")
            queue = validate_contract(QUEUE_SCHEMA, json.loads(queue_path.read_text(encoding="utf-8")))
            if (
                queue["input"]["url"] != github_url
                or queue["input"]["entry_mode"] != entry_mode
                or Path(queue["owned_project"]["root"]).resolve() != resolved_project
            ):
                raise ValueError("Existing run input or owned project does not match")
            parsed_input = parse_github_url(github_url)
            discovery = queue["input"]["discovery"]
            if discovery_input_path is not None:
                supplied_discovery = _load_discovery_provenance(discovery_input_path, github_url)
                if supplied_discovery != discovery:
                    raise ValueError("Existing run discovery provenance does not match")
            source_risk = dict(queue["source_risk"])
            project = VerifiedGitHubProject(
                input=parsed_input,
                repository_id=str(queue["input"]["canonical_repository"]),
                canonical_url=str(queue["input"]["canonical_url"]),
                commit_sha=str(queue["verified_target"]["commit_sha"]),
                ref=str(queue["verified_target"]["ref"]),
                object_url=str(queue["verified_target"]["object_url"]),
                metadata={
                    "full_name": queue["input"]["canonical_repository"],
                    "default_branch": queue["verified_target"]["ref"],
                    "archived": source_risk["archived"],
                    "fork": source_risk["fork"],
                    "license": {"spdx_id": source_risk["license_spdx"]},
                },
                target_payload=None,
                fork_redirected=bool(source_risk["fork"]),
                upstream_repository=source_risk["upstream_repository"],
            )
            evidence = list(queue["external_evidence"])
            missing = list(queue["missing_scopes"])
            evidence, missing = _retry_missing_standard_evidence(
                api_client,
                project,
                policy,
                evidence,
                missing,
            )
            created_at = str(queue["created_at"])
            verified_at = str(queue["verified_target"]["verified_at"])
        else:
            try:
                if entry_mode == "discovered":
                    if discovery_input_path is None:
                        raise ValueError("Active discovery requires a bounded discovery provenance JSON file")
                    discovery = _load_discovery_provenance(discovery_input_path, github_url)
                else:
                    if discovery_input_path is not None:
                        raise ValueError("Nominated learning runs cannot include discovery provenance")
                    discovery = None
                project = verify_project(api_client, parse_github_url(github_url))
            except GitHubPartialError as error:
                created_at = current.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                output_dir.mkdir(parents=True, exist_ok=True)
                write_bytes_atomic(output_dir / "created-at.txt", (created_at + "\n").encode("utf-8"))
                reason = f"verified_target: {type(error).__name__}: {error}"
                _write_unprepared_manifest(
                    path=manifest_path,
                    run_id=identifier,
                    created_at=created_at,
                    policy=policy,
                    status="partial",
                    reason=reason,
                )
                _register_project(resolved_root, owned)
                return PrepareResult(identifier, None, manifest_path, "partial", (reason,))
            except (OSError, GitHubError, ValueError) as error:
                created_at = current.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                output_dir.mkdir(parents=True, exist_ok=True)
                write_bytes_atomic(output_dir / "created-at.txt", (created_at + "\n").encode("utf-8"))
                reason = f"verified_target: {type(error).__name__}: {error}"
                _write_unprepared_manifest(
                    path=manifest_path,
                    run_id=identifier,
                    created_at=created_at,
                    policy=policy,
                    status="failed",
                    reason=reason,
                )
                _register_project(resolved_root, owned)
                return PrepareResult(identifier, None, manifest_path, "failed", ())
            source_risk = _source_risk(project, policy)
            evidence, missing = _collect_standard_evidence(api_client, project, policy)
            created_at = current.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            verified_at = created_at

        existing_urls = {str(item["official_url"]) for item in evidence}
        for include_url in include_urls:
            missing = [item for item in missing if not item.startswith(f"include {include_url}:")]
            if len(evidence) >= policy.max_external_evidence:
                missing.append("include: maximum external evidence count reached")
                break
            try:
                path = parse_pinned_blob_url(include_url, project.repository_id, project.commit_sha)
                if include_url in existing_urls:
                    continue
                text = read_repository_file(
                    api_client,
                    project.repository_id,
                    path,
                    project.commit_sha,
                    max_bytes=policy.max_evidence_chars,
                )
                evidence.append(_evidence("blob", include_url, path, text, project.commit_sha))
                existing_urls.add(include_url)
            except (GitHubError, ValueError) as error:
                missing.append(f"include {include_url}: {type(error).__name__}: {error}")

        queue = {
            "schema_version": QUEUE_SCHEMA,
            "run_id": identifier,
            "created_at": created_at,
            "policy_version": policy.policy_version,
            "input": {
                "url": github_url,
                "entry_mode": entry_mode,
                "discovery": discovery,
                "kind": project.input.kind,
                "canonical_repository": project.repository_id,
                "canonical_url": project.canonical_url,
            },
            "verified_target": {
                "commit_sha": project.commit_sha,
                "ref": project.ref,
                "object_url": project.object_url,
                "verified_at": verified_at,
            },
            "source_risk": source_risk,
            "external_evidence": evidence,
            "owned_project": owned,
            "missing_scopes": sorted(set(missing)),
        }
        validate_contract(QUEUE_SCHEMA, queue)
        output_dir.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)
        marker = created_at + "\n"
        write_bytes_atomic(output_dir / "created-at.txt", marker.encode("utf-8"))
        write_bytes_atomic(raw_dir / "created-at.txt", marker.encode("utf-8"))
        write_json_atomic(queue_path, queue)
        write_json_atomic(raw_dir / "external-evidence.json", {"evidence": evidence})
        _register_project(resolved_root, owned)
        status = "partial" if missing else "success"
        return PrepareResult(identifier, queue_path, None, status, tuple(sorted(set(missing))))
