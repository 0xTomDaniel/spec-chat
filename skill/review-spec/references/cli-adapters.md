# CLI adapters — how the review loop attaches per agent

The loop protocol (park → drain → apply → reply → re-park) is identical everywhere; only *how the watch is hosted* differs. The invariant: the review loop runs in the user's open authoring thread whenever possible, because the spec was born there and that context is what makes annotations addressable.

## Claude Code (primary)

One collection watch runs **in-session as a background task** — Claude Code forbids foreground sleep loops, and backgrounding keeps the session interactive. The harness re-invokes the agent when `watch-specs.sh` exits. Continuity is native; nothing extra to install. A page-specific `watch.sh` process is an explicit narrowing mode, not the default.

## Codex

Codex spends ~40k+ tokens per drain cycle, so an idle in-session watch is wasteful. Two modes:

- **Interactive session open**: run `watch-specs.sh <spec-root> .cursor-codex-session ...` in-session like Claude Code. One watcher covers the collection and one Codex session sustains multiple drain cycles (verified). Full authoring context.
- **Detached (session closed)**: use `scripts/codex-review.sh <spec-root>` — it runs one collection watch *outside* the session (free) and calls `codex exec -s workspace-write` only when a batch lands. Ready specs drain serially. Before each dispatch the wrapper reads that spec's own `review/state.json`, resuming its `sessionId` when present; otherwise it uses a cold exec (the file digest carries context). `-s workspace-write` is required (headless Codex defaults to read-only) and is supplied by the wrapper — not a user step. Flag order matters: sandbox flags precede the subcommand (`codex exec -s workspace-write resume <id>`). Passing a `.spec.html` file explicitly retains legacy single-page mode.

Concurrency: a detached wrapper must not resume a thread an interactive session still owns. Guard with a lease in each spec's `review/state.json` (`ownerKind`, `pid`, `leaseUntil`, `heartbeatAt`); stale lease → takeover writes a `detached-takeover` event. A single collection wrapper serializes batches across specs while preserving independent cursors and session state.

## pi.dev

Watch runs in-session via a pi extension (pi's TypeScript extension API is the natural host), or the same collection-wrapper pattern as detached Codex — `codex-review.sh` is a near-template (swap `codex exec` for the pi non-interactive invocation).

**Status: documented, not live-verified (by decision, 2026-07-05).** The loop is pure file I/O, transport-agnostic, and validated end-to-end on two independent CLIs (Claude Code in-session, Codex via wrapper); pi would run the identical `watch-specs.sh` / `emit-reply.sh` / spec-edit flow. Not tested live because pi/omp are BYOK — separate inference billing that cuts against the Pro-account-inference model. Revisit if pi becomes the primary driver.

## Session continuity (all CLIs)

Files are the contract; session resume is an optimization. Every drain cycle externalizes durable agreements — into the spec and `review/context.md` — so a different session, or a different CLI, can resume cold. Long-lived threads should rotate (checkpoint to files, start fresh) after enough batches, because a resumed session grows until compaction makes the preserved context probabilistic. Resume buys latency and nuance; it is never durable memory.
