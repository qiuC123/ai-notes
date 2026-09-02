# Architecture decision records

Run the read-only inventory and structural validation from the repository root:

```bash
python -m ai_notes adr-status --root .
```

The command reads only numbered Markdown files in this directory. It reports each ADR's exact status text, normalized status and explicit `superseded_by` target. Validation fails for a duplicate number, an unrecognized `Status:` form, a missing replacement ADR or a replacement link that does not match its ADR number. It does not infer lifecycle state from body text.

Supported status forms are intentionally small:

- `Proposed`, `Accepted`, `Rejected` or `Deprecated`;
- `Implemented`, optionally followed by an implementation date or a scoped `for ... on YYYY-MM-DD` note;
- `Superseded by` or `Partially superseded by` an explicit linked ADR, optionally followed by a semicolon note.

## ADR 0009 maintenance note

ADR 0009 says Codex orchestrates “two internal deterministic commands”. The current interface has three orchestration steps—Prepare, Validate and Finalize—and separate read-only status commands. This is documentation drift, not a machine-detectable contradiction in ADR status or replacement metadata.

Keep ADR 0009 unchanged as historical evidence. If this drift is only an implementation detail, describe the current command surface in the main README (as it is today). If the architecture changes—for example, the queue boundary or the separation between validation and state mutation changes—record a new ADR and mark ADR 0009 explicitly superseded or partially superseded by it. Do not add fuzzy body-text rules to this validator.
