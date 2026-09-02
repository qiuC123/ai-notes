# Separate the co-learning core from the legacy AIHOT pipeline

Status: Implemented for the manual vertical slice on 2026-09-02

New co-learning behavior is implemented in a new `ai_notes` Python package instead of extending or first renaming the legacy `aihot` Release pipeline. The legacy package remains temporarily available as a twelve-source candidate input and rollback path while the new vertical slice is validated; it is not a second long-term product core.

Codex orchestrates two internal deterministic commands: `prepare-learning` resolves a GitHub input to an exact version and creates a bounded queue, while `finalize-learning` validates a contract-bound decision and commits allowed runtime state. Python does not call the Codex API. The interface is limited to `learning-queue.v1`, `learning-decisions.v1` and `learning-run-manifest.v1` rather than a complete project database.

Learning cards are delivered in the long-lived conversation. Machine-readable JSON, manifests and necessary external evidence snapshots are retained for thirty days; cards are not automatically emitted as Markdown knowledge reports. Durable owned-project evidence stores only path/symbol or compact line location, repository or working-tree fingerprint and a non-source description. Full source and diffs are never copied into the ledger.

Runs report `success`, `partial`, `review_failed` or `failed`; a complete run may successfully find no connection, while incomplete or invalid review states cannot masquerade as an empty result. Forks default to their upstream identity, archived or license-risk projects are learning-only, and unverifiable or malicious identities are rejected before deep reading.

The first real run uses Ai Notes as the owned project and `openai/codex` as the external learning project. Schemas, adversarial fixtures, prepare/finalize commands, Codex Review integration and Hermes removal are implemented. An initial run completed as an honest `partial` because an optional deep-evidence request exhausted the anonymous GitHub limit. After reset, run `20260902T084445Z-09d89053` completed at commit `eb10d91e48ccbd0930427461fb392337addb1ac0` with no missing scopes or validation errors. It is the first nominated run in the ten-run manual trial; the remaining validations and explicit feedback still precede any user-approved Skill or schedule.
