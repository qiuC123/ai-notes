from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=path.parent, prefix=f".{path.name}.") as handle:
            handle.write(payload)
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json_atomic(path: Path, payload: object) -> None:
    rendered = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    write_bytes_atomic(path, rendered)


def append_jsonl_atomic(path: Path, event: dict[str, Any]) -> None:
    existing = path.read_bytes() if path.exists() else b""
    if existing and not existing.endswith(b"\n"):
        existing += b"\n"
    write_bytes_atomic(path, existing + canonical_json_bytes(event))


def resolve_within(root: Path, relative_path: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise ValueError("Owned-project evidence path must be a non-empty relative path")
    resolved_root = root.resolve()
    resolved = (resolved_root / relative_path).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"Owned-project evidence escapes the authorized root: {relative_path}") from error
    if not resolved.is_file():
        raise ValueError(f"Owned-project evidence file does not exist: {relative_path}")
    return resolved


def _git(project: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(project), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def project_fingerprint(project: Path) -> dict[str, str | None]:
    root = project.resolve()
    git_root = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    if git_root != root:
        raise ValueError(f"Authorized project must be its Git root: {root}")
    head = _git(root, "rev-parse", "HEAD")
    repository_id = _git(root, "config", "--get", "remote.origin.url") or root.name
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    return {
        "name": root.name,
        "root": str(root),
        "repository_id": repository_id,
        "git_head": head,
        "working_tree_fingerprint": sha256_bytes(status.encode("utf-8")),
    }


def focus_files(project: Path, *, limit: int = 100) -> list[dict[str, Any]]:
    tracked = _git(project, "ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    preferred_names = {
        "agents.md",
        "context.md",
        "pyproject.toml",
        "package.json",
        "cargo.toml",
        "go.mod",
        "requirements.txt",
    }

    def priority(value: str) -> tuple[int, str]:
        lowered = value.lower().replace("\\", "/")
        name = Path(lowered).name
        preferred = (
            name.startswith("readme")
            or name.startswith("todo")
            or name in preferred_names
            or lowered.startswith("docs/adr/")
        )
        return (0 if preferred else 1, lowered)

    result: list[dict[str, Any]] = []
    for relative in sorted(set(tracked), key=priority):
        if len(result) >= limit:
            break
        try:
            path = resolve_within(project, relative)
        except ValueError:
            continue
        size = path.stat().st_size
        if size > 2 * 1024 * 1024:
            continue
        result.append({"path": relative.replace("\\", "/"), "sha256": sha256_file(path), "size": size})
    return result


class RunLock(AbstractContextManager["RunLock"]):
    def __init__(self, path: Path) -> None:
        self.path = path
        self._descriptor: int | None = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise RuntimeError(f"Ai Notes learning run is already active: {self.path}") from error
        os.write(self._descriptor, str(os.getpid()).encode("ascii"))
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if self._descriptor is not None:
            try:
                os.close(self._descriptor)
            except OSError:
                pass
        self.path.unlink(missing_ok=True)


def prune_learning_artifacts(root: Path, *, as_of: date, retention_days: int = 30) -> list[str]:
    cutoff = as_of - timedelta(days=retention_days)
    removed: list[str] = []
    for base in (root / "data" / "learning" / "raw", root / "outputs" / "learning"):
        if not base.exists():
            continue
        for path in base.iterdir():
            if not path.is_dir():
                continue
            marker = path / "created-at.txt"
            try:
                created = datetime.fromisoformat(marker.read_text(encoding="utf-8").strip().replace("Z", "+00:00")).date()
            except (OSError, ValueError):
                continue
            if created < cutoff:
                shutil.rmtree(path)
                removed.append(str(path.relative_to(root)).replace("\\", "/"))
    return sorted(removed)
