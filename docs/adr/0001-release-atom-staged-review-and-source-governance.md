# Use Release Atom discovery, staged review, and governed source promotion

Status: Accepted
Date: 2026-08-31
Related design: [`Ai Notes 高质量 AI 信息源优化设计`](../AI_NOTES_SOURCE_QUALITY_DESIGN.md)

Ai Notes needs a daily source that is official, credential-free, replayable, and resistant to release noise. GitHub REST exposes richer metadata but anonymous requests are rate-limited; direct LLM ingestion would make collection, judgment, and retry behavior inseparable; automatic source admission would allow discovery-channel popularity to silently redefine quality.

## Decision

1. Use each registered GitHub repository's official `releases.atom` as the normal daily Release discovery source. Apply project-specific tag rules and universal prerelease exclusions. The daily pipeline has one fixed GitHub REST dependency: the public global-security-advisories endpoint. Release REST is used only for history, diagnostics, or bounded-window gap recovery. A security-API failure or an unresolved Atom gap produces `partial`, not a false complete success.
2. Keep one user-facing `daily` command, but implement it as three artifact-backed stages: deterministic collection, Hermes substantive-change review, and deterministic validation/finalization.
3. Do not let AIHOT, search, community signals, Star counts, or Trending automatically add a core source. They may only nominate a source candidate, which must pass identity checks, historical backtesting, and a 14-day trial before promotion.

## Consequences

- Daily collection needs no GitHub credential and remains usable when the REST rate limit is exhausted.
- Release collection normally avoids REST, but the pipeline must track Feed/ledger overlap and use REST to recover releases that may have slid out of the bounded Atom window.
- An unavailable security endpoint or unresolved release gap preserves healthy results while making incompleteness explicit in the manifest.
- The source registry must maintain tag rules for multi-package and high-frequency repositories.
- Projects whose meaningful releases are mainly RC or continuous builds remain on the watchlist until a better official announcement source exists.
- Intermediate queues and decisions become durable artifacts, making Hermes failures retryable and evidence checks deterministic.
- Hermes review adds model cost and latency, but the 90-day metadata backtest indicates only a small number of formal releases per day after hard filtering.
- Source quality changes become explicit, reviewable decisions rather than hidden consequences of discovery popularity.
