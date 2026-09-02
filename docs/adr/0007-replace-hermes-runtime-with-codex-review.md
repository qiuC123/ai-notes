# Replace the Hermes runtime reviewer with Codex semantic review

Status: Implemented on 2026-09-02

Hermes participated in Ai Notes because the original implementation was developed around Hermes, not because the product requires two cooperating Agents. The GitHub co-learning product therefore standardizes on Codex as its only semantic Agent and does not introduce Codex–Hermes debate.

Python retains deterministic collection, untrusted-content normalization, queue creation, state management, JSON Schema validation, exact evidence matching and finalization. Codex reads the bounded review queue as data, judges substantive changes, understands projects and proposes evidence-gated connections. External content cannot change task instructions, expand permissions or authorize execution.

The Hermes CLI reviewer has been removed from runtime code. The Codex task explicitly runs Python Collect, reads the bounded JSON queue, writes contract-valid JSON decisions, and runs Python Finalize; Python does not call the Codex API or hold model credentials. Release and historical backtest flows now stop at the queue boundary when no decision file is supplied, then accept a strict `--decisions` artifact on retry. Historical implementation documents and results remain labelled as legacy evidence rather than rewritten as if Hermes had never been used.

This decision reduces runtime dependencies, avoids duplicated model judgment and keeps the user’s long-lived Codex conversation as the single learning context. It also means Codex review safety must be enforced by bounded inputs, read-only automation policy and deterministic final validation rather than by a second Agent.
