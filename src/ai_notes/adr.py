from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_ADR_FILENAME = re.compile(r"^(?P<number>\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")
_REPLACEMENT_STATUS = re.compile(
    r"^(?P<kind>Partially superseded|Superseded) by "
    r"\[`ADR (?P<number>\d{4})`\]\((?P<target>[^)]+)\)"
    r"(?:; .+)?$"
)
_IMPLEMENTED_STATUS = re.compile(
    r"^Implemented(?: on \d{4}-\d{2}-\d{2}| for .+ on \d{4}-\d{2}-\d{2})?(?:; .+)?$"
)
_SIMPLE_STATUSES = {
    "Accepted": "accepted",
    "Deprecated": "deprecated",
    "Proposed": "proposed",
    "Rejected": "rejected",
}


class AdrValidationError(ValueError):
    """Raised when the local ADR set is not structurally consistent."""


@dataclass(frozen=True)
class AdrRecord:
    number: str
    title: str
    status: str
    status_text: str
    superseded_by: str | None
    path: str

    def as_dict(self) -> dict[str, str | None]:
        return {
            "number": self.number,
            "title": self.title,
            "status": self.status,
            "status_text": self.status_text,
            "superseded_by": self.superseded_by,
            "path": self.path,
        }


@dataclass(frozen=True)
class _ParsedStatus:
    kind: str
    replacement_number: str | None = None
    replacement_target: str | None = None


def _parse_status(status_text: str) -> _ParsedStatus | None:
    simple = _SIMPLE_STATUSES.get(status_text)
    if simple is not None:
        return _ParsedStatus(simple)

    if _IMPLEMENTED_STATUS.fullmatch(status_text):
        return _ParsedStatus("implemented")

    replacement = _REPLACEMENT_STATUS.fullmatch(status_text)
    if replacement is None:
        return None
    kind = "partially_superseded" if replacement.group("kind") == "Partially superseded" else "superseded"
    return _ParsedStatus(
        kind,
        replacement_number=replacement.group("number"),
        replacement_target=replacement.group("target"),
    )


def _read_header(path: Path) -> tuple[str | None, str | None]:
    lines = path.read_text(encoding="utf-8").splitlines()
    title = lines[0][2:].strip() if lines and lines[0].startswith("# ") else None

    status_lines: list[str] = []
    for line in lines[1:]:
        if line.startswith("## "):
            break
        if line.startswith("Status:"):
            status_lines.append(line.removeprefix("Status:").strip())
    status = status_lines[0] if len(status_lines) == 1 else None
    return title, status


def inventory_adrs(root: Path) -> tuple[AdrRecord, ...]:
    """Return a deterministic, validated inventory without changing ADR files."""

    root = root.resolve()
    adr_dir = root / "docs" / "adr"
    if not adr_dir.is_dir():
        raise AdrValidationError(f"ADR directory does not exist: {adr_dir}")

    paths = sorted(
        (path for path in adr_dir.iterdir() if path.is_file() and _ADR_FILENAME.fullmatch(path.name)),
        key=lambda path: path.name,
    )
    errors: list[str] = []
    parsed: list[tuple[Path, str, str, str, _ParsedStatus]] = []
    paths_by_number: dict[str, list[Path]] = {}

    for path in paths:
        match = _ADR_FILENAME.fullmatch(path.name)
        assert match is not None
        number = match.group("number")
        paths_by_number.setdefault(number, []).append(path)

        title, status_text = _read_header(path)
        if title is None:
            errors.append(f"{path.name}: missing first-line '# ' title")
        if status_text is None:
            errors.append(f"{path.name}: expected exactly one Status line before the first section")
            continue
        status = _parse_status(status_text)
        if status is None:
            errors.append(f"{path.name}: unrecognized status: {status_text!r}")
            continue
        if title is not None:
            parsed.append((path, number, title, status_text, status))

    for number, numbered_paths in sorted(paths_by_number.items()):
        if len(numbered_paths) > 1:
            names = ", ".join(path.name for path in numbered_paths)
            errors.append(f"duplicate ADR number {number}: {names}")

    path_by_number = {
        number: numbered_paths[0]
        for number, numbered_paths in paths_by_number.items()
        if len(numbered_paths) == 1
    }
    for path, _, _, _, status in parsed:
        if status.replacement_number is None:
            continue
        target = path_by_number.get(status.replacement_number)
        if target is None:
            errors.append(f"{path.name}: replacement ADR {status.replacement_number} does not exist")
            continue
        linked_target = (path.parent / (status.replacement_target or "")).resolve()
        if linked_target != target.resolve():
            errors.append(
                f"{path.name}: replacement link for ADR {status.replacement_number} points to "
                f"{status.replacement_target!r}, expected {target.name!r}"
            )

    if errors:
        raise AdrValidationError("ADR validation failed:\n- " + "\n- ".join(errors))

    records = [
        AdrRecord(
            number=number,
            title=title,
            status=status.kind,
            status_text=status_text,
            superseded_by=status.replacement_number,
            path=path.relative_to(root).as_posix(),
        )
        for path, number, title, status_text, status in parsed
    ]
    return tuple(sorted(records, key=lambda record: (record.number, record.path)))
