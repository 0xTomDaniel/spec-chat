# Spike 2 results — parked-agent loop

Run 2026-07-03, Claude Code (Fable 5), Linux. Harness: `generate.sh` (synthetic annotator) + `watch.sh` (bounded batch wait) against `specs/order-pipeline.spec.html`. Covers spike 3 (edit fidelity); spike 5 harness ready but needs a human browser.

## Timeline (actual)

| t | event |
|---|---|
| 19:08:07 | c1 emitted (datum anchor: `#latency-budget › enqueue`) |
| 19:08:13 | c2 emitted (element anchor: `#retry-policy › p[1]`) |
| 19:08:17 | hand-off marker emitted; watch exits 0 within poll interval (≤2 s) |
| 19:09:01 | both spec edits applied + 2 agent reply events written; cursor advanced |
| 19:09:10–30 | re-parked watch times out empty: exit 3, zero output |

Human→reply latency: **~44 s**, dominated by agent reasoning, not transport. Wake latency: **≤ poll interval (2 s)**.

## Findings vs. spike questions

**(a) Can the skill cycle bounded waits indefinitely?** Yes, with one shape change: **Claude Code blocks foreground sleep loops** — the parked watch must run as a *background* task; the harness re-invokes the agent on exit. This is better than blocking anyway: the session stays interactive while parked. Skill protocol amended accordingly. Permission prompts: this session's sandbox ran the scripts unprompted; on default permission modes the packaged skill's watch script will need one allowlist entry (`Bash(*/watch.sh*)`) or it prompts per cycle — verify on a stock setup.

**(b) Codex leg: PASS (run 2026-07-03, gpt-5.5 via `codex exec`).** One drain cycle end-to-end with zero friction: parked on `watch.sh` (sleep-poll ran fine in the sandbox), woke on hand-off, applied the c3 edit in correct dialect, wrote a schema-correct reply event, updated its cursor. ~20 s hand-off→reply; 42,482 tokens for the whole cycle. Two conditions to record:
- **`--sandbox workspace-write` is required for `codex exec`** (headless defaults to read-only). NOT a user-facing step: the spec-chat wrapper that invokes `codex exec` per batch supplies this flag itself — users install the skill + CLI and do nothing else. (Interactive Codex sessions on trusted projects are workspace-write by default already.) First row of the per-CLI adapter table.
- **Codex's natural shape is one `codex exec` per drain cycle**, not an indefinitely parked session. At 42 k tokens/cycle, empty wakeups are too expensive to spend inside Codex — so for Codex the watch belongs in a thin wrapper *outside* the session, invoking `codex exec` (or `resume`) only when a batch actually arrives. This is the headless per-batch pattern the design anticipated as fallback; for Codex it should be the default.

**(b2) Codex multi-cycle, one session: PASS (run 2026-07-03).** One `codex exec` session sustained TWO consecutive drain cycles: parked → drained c4 → edited spec → replied → re-parked → drained c5 → edited → replied. ~50 k tokens for both cycles vs 42 k for one cold cycle → marginal per-cycle cost is small once thread context loads. **This corrects the earlier conclusion:** the in-session watch is Codex's primary shape too (same open thread = full authoring context, the product invariant); the external wrapper applies only to detached/async review of a closed thread.

**Protocol bug found live:** c5/h4 arrived while Codex was processing c4 — `ls > cursor` would have silently skipped them. Codex detected it and restored the cursor to the processed boundary. Fix mandated in the skill: cursor updates APPEND exactly the processed filenames, never regenerate with `ls`.

**pi leg:** NOT RUN — remaining. Extension route, in-session per the invariant.

**(c) Cost:** empty wakeup ≈ one short tool result + notification (trivially small). Batch of 2 ≈ a few thousand tokens (read events, 2 edits, 2 reply writes). Not precisely instrumented; fine for a spike.

**(d) On-task over 30+ cycles:** not yet stress-tested (2 cycles). Rehydrate-from-files protocol was followed and sufficed; long-session drift test still owed.

**(e) Latency:** see timeline. Batch semantics (wake only on hand-off) worked; `LIVE=1` path untested.

## Spike 3 — edit fidelity: PASS

Both edits landed first try via exact string matching: `markLine` added inside the pretty-printed island JSON, alerting sentence added to prose. The dialect (pretty-printed islands, one-sentence-per-line) is what made the match targets unambiguous. Anchors untouched; diff is clean and readable.

## Spike 5 — external-write visibility: harness ready

`spool-viewer.html` holds an FSA directory handle and poll-reads `human/` + `agent/` every 1.5 s. To run: open it in Chromium, pick `specs/order-pipeline.spec.html.review/`, re-run the generator and an agent cycle, confirm agent-written replies appear without re-granting. Verdict pending a human at a browser.

## Design consequences

1. Skill protocol: parked watch = background task + wake-on-exit, not a blocking foreground call (Claude Code). Exit codes (0 = batch, 3 = quiet timeout) are a sufficient contract.
2. Empty wakeups are cheap enough that the ~240 s production timeout looks comfortable.
3. No changes needed to the event schema or spool layout — both worked as designed under a real agent.
