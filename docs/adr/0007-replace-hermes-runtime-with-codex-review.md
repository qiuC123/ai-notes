# Replace the Hermes runtime reviewer with Codex semantic review

Status: Accepted

Hermes participated in Ai Notes because the original implementation was developed around Hermes, not because the product requires two cooperating Agents. The GitHub co-learning product therefore standardizes on Codex as its only semantic Agent and does not introduce Codex–Hermes debate.

Python retains deterministic collection, untrusted-content normalization, queue creation, state management, JSON Schema validation, exact evidence matching and finalization. Codex reads the bounded review queue as data, judges substantive changes, understands projects and proposes evidence-gated connections. External content cannot change task instructions, expand permissions or authorize execution.

The existing Hermes CLI reviewer remains a temporary rollback path only while the Codex replacement is being implemented and checked against the same queue and output contracts. The Codex task explicitly runs Python Collect, reads the bounded JSON queue, writes contract-valid JSON decisions, and runs Python Finalize; Python does not call the Codex API or hold model credentials. Migration preserves the Collect → Review → Finalize boundary, makes the Review contract Agent-neutral, and verifies current schema/evidence checks, prerelease and security boundaries, retry behavior, representative historical Releases, and prompt-injection cases. Semantic agreement with Hermes is not required, but policy compliance and exact supporting evidence are. Only then are Hermes runtime, profile and configuration dependencies removed. Historical implementation documents and results remain labelled as legacy evidence rather than rewritten as if Hermes had never been used.

This decision reduces runtime dependencies, avoids duplicated model judgment and keeps the user’s long-lived Codex conversation as the single learning context. It also means Codex review safety must be enforced by bounded inputs, read-only automation policy and deterministic final validation rather than by a second Agent.
