# spec-chat — annotatable visual specs for AI-agent collaboration

**Repo:** https://github.com/0xTomDaniel/spec-chat · **Mockup:** https://claude.ai/code/artifact/72c0096a-d2b8-4067-b8b3-44f8b84f2b51

## Concept

Specs authored as **visual HTML documents** (charts, diagrams, math, animation — not just prose) that humans annotate in the browser; a coding agent (Claude Code, Codex CLI, pi.dev) addresses annotations and edits the spec in place as agreements are reached. Discussion happens *on* the visualization instead of in chat prose.

Hard constraints: agent-agnostic/universal; plain files + CLI + agent skills over MCP/hooks/servers; all inference through the coding CLI session (subscription auth, no separate billing); no alt-tabbing back to the terminal to trigger the agent.

## v1 scope — ratified cut line (round 3, anti-overengineering pass)

Joint position (Claude ↔ GPT-5.5): **v1 is a deliberately small local review loop.** A canonical `spec.html` using semantic islands; a committed dotdir runtime with vendored launch libraries (ECharts + Mermaid + house pin runtime — vendoring is load-bearing, NOT optional: CDN-only rendering makes visual meaning depend on network/corporate filters/CDN behavior exactly where fidelity matters); browser annotations written through an FSA directory handle into actor-segregated event spools; an explicit hand-off button; a bounded polling skill (~20-line ls-diff + sleep script, no inotify dependency, ~240s bounded, exits on first event) that wakes the coding CLI, edits the spec in place, and writes agent events back.

**Deferred until real usage proves the need (re-add triggers in parens):**
- Derived `log.jsonl` + advisory locks — spool files are the log, `jq -s` over the dir (trigger: reviews at hundreds of events, or a second ordered-history consumer)
- Anchor-migration events — v1 shows orphaned pins with quoted-text fallback (trigger: orphan rate annoying)
- Formatter/linter as a tool — skill conventions only; fire this trigger aggressively on first observed dialect drift
- Runtime integrity manifests — git content-addresses the committed dotdir (safe only because libs are vendored)
- Event fields `specSha`/`sequenceHint` remain deferred; `respondsTo`, `threadId`, and `supersedes` are now required by human↔agent follow-up replies and append-only editing (trigger for the remaining fields: staleness disputes)
- `review-wait` binary + per-CLI adapter table — the polling script replaces it (trigger: polling latency/cost measurably hurting)
- PR bridge: anchor index, PR-body summaries, screenshots (trigger: first team adopting this in PR review culture)
- Stdlib beyond the launch three — JSXGraph/KaTeX/Cytoscape+dagre/GSAP/rough-notation are pre-approved additions the agent vendors per-spec when a spec needs them (the trigger IS the mechanism)

## Annotation UX & user flows

The browser IS the app — no extension, no desktop shell. The runtime in the dotdir renders the review UI inside every spec page. Clickable mockup: https://claude.ai/code/artifact/72c0096a-d2b8-4067-b8b3-44f8b84f2b51

**Surfaces (v1):** numbered comment pins anchored to blocks AND elements within blocks · right-rail thread panel (status pills, human↔agent replies, editable unanswered human messages) · active-thread ring on the associated page element · floating toolbar (comment-mode toggle `C`, agent-presence status) · hand-off bar with draft count · toasts for spool writes and spec reloads · changed-section badges.

**Universal anchoring (prod requirement): EVERYTHING is annotatable — any kind of element.** Every click resolves through an **anchor resolution cascade**, strongest tier available, degrading gracefully:
1. **Semantic** — renderer-owned internals: chart datums, **value labels, axis tick numbers, axis titles, gridline/target lines, legend items**, diagram **nodes, edges/arrows, and floating notes**. Click the inventory bar → `{block: "latency-budget", target: {type: "datum", key: "inventory"}}`; click an arrow → `{type: "edge", key: "schema-check→inventory-check"}`; click a tick → `{type: "axis-y", key: "700"}`. A small adapter per stdlib library maps click→semantic key (ECharts exposes all of this natively via click events with `triggerEvent: true` on axes/labels + `convertToPixel`; SVG diagrams are DOM, so every mark is directly targetable — thin strokes get widened invisible hit areas). **Acceptance criterion: no dead zones — every visible mark on the page is annotatable, and its companion (from dogfooding 2026-07-03): hover affordance coverage must equal anchorable coverage — if you can comment on it, it must light up on hover (CSS outlines for DOM, emphasis borders for chart series, ring overlays for canvas text like axis labels).** "Every visible mark" includes the document title, breadcrumbs/meta line, dividers/rules, figures, captions, and whitespace-adjacent structure; the ONLY exempt surface is the commenting shell itself (pins, thread panel, toolbar, hand-off bar). Dialect consequence: every block-level container in a spec — including the header — carries `data-anchor`, so the delegated click surface is the whole document, not a list of registered regions.
2. **Structural** — *any other DOM element*: nearest `data-anchor` ancestor + structural path relative to it (`#retry-policy › p[2]`, `#pricing › table[1]/tr[3]/td[2]`), content quote always captured. Key insight: **the spec HTML is the source file, so a structural path relative to a stable anchor IS a source location** — the agent navigates directly to the line. No reverse-mapping (agentation needed React-fiber forensics for this; we get it by construction).
3. **Text selection** — `target: {type: "text", key: <quote>}` within the enclosing element.
4. **Spatial** — region-select marquee for images/whitespace/"this area": percentage coords within the nearest anchor + snapshot. The only tier where pixels are legitimate. (Post-skeleton; tiers 1–3 are v1.)

Every event carries the quote/context triple regardless of tier → orphan recovery works uniformly. Pins position themselves off the live element at render time (recomputed on resize/re-render), so anchors survive re-renders and data edits. Dialect rule: the generator stamps `data-anchor` liberally; the linter (when it exists) enforces anchors on blocks + islands, structural paths cover the rest. Event schema unchanged — `anchorId` becomes `{blockId, target?}`.

**Multi-spec organization (prod v1 requirement — single spec.html is prototype-only).**
- `specs/` root: one spec file per capability/feature (`checkout.spec.html`, `fulfillment.spec.html`); a spec should be readable in one sitting — extract shared definitions into their own spec and cross-link (`checkout.spec.html#latency-budget`).
- One shared dotdir at the root (`specs/.viz/` — runtime + vendored libs, versioned once for all specs).
- Per-spec review dirs (`<name>.spec.html.review/`) keep annotation locality; gitignore pattern `*.review/`.
- **One FSA grant covers everything**: the browser asks for the `specs/` root handle once; every spec page and every review dir is reachable under it.
- **One agent watcher covers everything by default**: a recursive collection watcher parks on the `specs/` root, discovers new review spools dynamically, and serially dispatches ready specs while preserving a cursor and session state per spec. Per-page watchers are explicit narrowing/debugging mode only.
- `index.spec.html` overview: links to all specs with open-thread counts — computed client-side by enumerating review dirs through the root handle, no build step. In-page file switcher on every spec (see mockup).
- Skill encodes the conventions: when a spec grows past ~a screen-read or gains a second audience, split it.

**Flows:**
1. *Author*: in the CLI — "write a visual spec for X". Skill scaffolds dotdir if missing, writes `spec.html`, opens browser.
2. *Connect*: open `spec.html` (`file://`) → page renders view-only → "Connect review folder" button → FSA directory picker grants an ancestor of the spec → existing threads render, annotation enabled. The handle is persisted to IndexedDB as a convenience. When write permission expires, **Reconnect review folder** opens the picker again because Arc was live-verified leaving restored-handle `requestPermission()` pending without surfacing permission UI.
3. *Annotate*: press `C` → hovering highlights anchored sections (anchor chip lights up) → click drops a pin + composer. Comments write to `review/human/` immediately (crash-safe) but sit as **drafts**.
4. *Hand off*: button writes the hand-off event → watching agent drains the batch → statuses flip draft → acknowledged → replied; agent edits `spec.html`, page detects change (mtime poll via handle), re-renders with "updated by agent" badges on touched sections.
5. *Converge*: reply directly to an agent response, edit any still-unanswered human message through an append-only `supersedes` event, or ✓ Resolve. Selecting a thread highlights its associated page element; resolved threads collapse but remain browsable — the spec doubles as its own decision record.
6. *Async*: no agent running? Annotate anyway (spool is durable); next `/review-spec` in the CLI drains pending batches immediately, then watches.

Agent presence: page shows "agent watching / last event Ns ago" derived from agent-event recency; if a hand-off gets no ack in ~30 s, prompt "is `/review-spec` running in your CLI?".

## Target architecture (v3 consensus, 2026-07-03)

Reached via adversarial design review: Claude (Fable 5) ↔ GPT-5.5 (xhigh), three rounds. The full design below is the target; v1 implements the cut line above.

### 1. HTML is canonical — constrained dialect
The HTML file IS the spec (no markdown counterpart, no sync loop), but only with discipline:
- **Semantic islands**: visual state committed as semantic input, not rendered debris — `<script type="application/spec+json" data-chart="echarts">{...}</script>` + `<div data-render="chart">` targets. Prose in lean semantic sections.
- Formatter/linter enforces the dialect: one-sentence-per-line prose, stable `data-anchor` IDs on every meaningful element, pretty-printed islands → readable git diffs.
- Machinery (annotation runtime, chart libs) loaded from a shared local dotdir, never inlined. **Runtime version + integrity contract**: the spec declares runtime version/hashes so two reviewers at the same commit see identical rendering.
- Escape hatch: markdown-canonical repos can treat artifacts as views; same annotation mechanics.

### 2. Annotations: actor-segregated event spools (NOT a shared file)
Original shared-JSONL-sidecar idea is dead — the File System Access API has no atomic append (writable streams do whole-file/seek-write on a swap file), so browser and agent appending to one file silently clobber each other. Instead:

```
spec.html.review/
  human/000001-<uuid>.json   ← browser writes here only
  agent/000002-<uuid>.json   ← agent writes here only
  state.json                 ← cursor/state
  log.jsonl                  ← derived, agent-owned, non-authoritative
```

- One file per event; event fields: id, actor, createdAt, parentId/threadId, anchorId, specSha, schemaVersion, sequenceHint (mandatory per writer session), supersedes/respondsTo.
- **Causality is reference-based** (parentId, supersedes, anchor-moved) — timestamps order display only, never semantics.
- Derived `log.jsonl` restores greppability: agent is sole writer, deterministic rebuild from spools, advisory lock + idempotent event IDs guard concurrent agent sessions. Non-authoritative.
- **Anchor migration**: events carry anchorId + fallback (quoted text, DOM path, section hash); agent emits `anchor-moved` / `anchor-obsolete` when edits displace pins.

### 3. Browser runtime: review-folder handle, permissions as re-auth UX
- "Open review folder" is a first-class button; one directory handle covers all event files. Handles persisted to IndexedDB as convenience — **expect re-permission on reopen** (Chrome does not guarantee persistence). Chromium-only accepted; export-button fallback elsewhere.
- If `file://` origin behavior proves flaky in testing, a tiny `review serve` static viewer is the sanctioned fallback — zero-server is a preference, not dogma.
- **Remote dev is a first-class deployment mode, not a fallback (added 2026-07-03).** FSA requires browser and files on the SAME machine; SSH boxes, devcontainers, and Codespaces — where coding agents actually live — break that. There, `tools/review-serve.py` (stdlib-only, localhost-bound: static files + GET/POST `/api/events`) over an SSH-forwarded port is the transport. Same spools, same protocol; the server only does the file I/O the browser can't reach across machines. Bonus: also covers non-Chromium browsers. Local Chromium keeps zero-server FSA.

### 4. Agent loop: bounded drain cycles (NOT indefinite blocking)
Indefinite `inotifywait` dies on real runtimes (tool timeouts, missing binaries, read-only/approval-gated sandboxes — Codex's default sandbox included). Instead, a skill loop:

```
review-wait specs/ --cursor-name .cursor-agent --timeout auto --max-events 20   # exits on first ready spec or timeout
→ reason, edit spec, emit agent events, update cursor, re-wait
```

- `--timeout auto` = host-CLI tool ceiling minus margin (~240s default). Exits instantly on events, so long timeouts cost nothing in latency; empty wakeups are cheap short turns. Long-but-bounded, never unbounded.
- **Rehydrate from files** (current spec + unresolved-event index + last summary) — chat history is never the review database; survives context compaction over long sessions.
- **Default cadence: explicit hand-off batches** (human annotates freely, hands off a batch). Live per-annotation mode is opt-in — mid-thought agent edits create churn.
- **Default scope: the whole spec collection.** The watcher selects one ready spec at a time and uses that spec's independent cursor/context/session files; a file-scoped watcher is an opt-in override.
- In-session CLI inference throughout (subscription auth). Identical mechanism on Claude Code / Codex / pi.

### 5. Git & review culture
- **Gitignore raw `spec.html.review/` by default.** Committed artifacts: the spec itself, anchor index, curated/rebuildable summaries. Opt-in `review export` for audit-heavy repos.
- Spec diffs: git conventions (dialect above) do the heavy lifting; agent change-summary events let the page badge changed sections with before/after.
- **v1 PR bridge is textual**: anchor index + change summary in PR body. Screenshots/visual diffs later (headless-browser dependency).

### 6. Product shape
Standalone tool, not a Warp-like environment (page-embedded terminal possible later as cosmetic layer; stagewise/Warp both drifted standalone→environment — revisit post-v1).

## Visual stdlib for agent generation
Pinned versions, vendored in the dotdir (offline + integrity): **ECharts** (charts, Apache-2.0) · **Mermaid** (boxes-and-arrows default, MIT) · **Cytoscape.js + dagre** (interactive graphs, MIT) · **JSXGraph + KaTeX** (math, MIT) · **GSAP** (animation, free non-OSI; Motion if OSI required) · **rough-notation** (emphasis, MIT) · **house comment-pin runtime** (no viable off-the-shelf lib exists).
Optional: D3, Observable Plot, Konva, markmap, leader-line, function-plot, Floating UI. Rejected: Mafs, React Flow, Motion-as-default, Liveline, tldraw, Excalidraw-as-target (React-only / license / zero training data / poor LLM generation).

## Borrowing from agentation: ideas yes, code no
PolyForm Shield 1.0.0 forbids competing use — clean-room only. Reimplement: greppable metadata over screenshots, annotation lifecycle states, self-driving review loop (ours via files+shell, theirs via MCP), tiered verbosity, skills-as-onboarding. Skip entirely: React-fiber forensics — we generate the artifact, anchors are baked in at generation time.

## Session continuity: the review loop lives in the authoring thread (rounds 4–5, 2026-07-03)

**Invariant (owner-set):** a spec is born in one deep-context thread, and that SAME open thread addresses the annotations. The context that makes "make this bar match what we discussed" meaningful lives there. Primary attachment is therefore always **in-session**, on every CLI:

- **Claude Code**: watch parked as a background task in the open session — proven live.
- **Codex**: watch runs inside the open session — **proven: one Codex session sustained two consecutive drain cycles** (both batches addressed in-dialect; ~50 k tokens for BOTH cycles vs 42 k for one cold cycle → marginal cost per cycle is small once thread context is loaded). The earlier "watch belongs outside Codex" conclusion is hereby scoped to detached/async mode only — it was an artifact of testing cold starts.
- **pi**: extension inside the open session (leg pending).

**Files remain the contract** — every cycle externalizes agreements (spec edits + resolved threads + `review/context.md` digest) — but this backs the secondary modes and cross-CLI portability; it is not a substitute for the live thread.

**Secondary modes (async only — the thread is closed):** detached resume of the SAME recorded thread (`review/state.json` `sessionId` + `threadName`, parsed from `codex exec --json` or bound via `review bind`; lease-locked `{ownerKind, pid, hostname, leaseUntil, heartbeatAt}` so a detached wrapper NEVER resumes a thread an interactive session owns — stale lease → explicit `detached-takeover` event) → cold + digest as last resort. Rotation applies to long-lived threads: checkpoint to files, rotate after N batches or suspected compaction — resume is never durable memory. Sandbox flag precedes the subcommand: `codex exec -s workspace-write resume <id>`.

**Protocol fix from the multi-cycle run:** cursor updates must append exactly the processed filenames — regenerating via `ls` races against events arriving mid-processing and silently skips them. (Codex hit this race live with c5/h4, caught it, and self-corrected — the skill now mandates append-only cursor updates.)

## Spike plan (ordered by how much architecture each can still bend)

1. ~~**FSA on `file://`**~~ — DONE (2026-07-03). Browser-side directory-handle grant + spool writes. ⚠️ Findings not yet recorded here — capture verdict on handle persistence across reopens, and whether `review serve` fallback is needed.
2. **Parked-agent loop** — ✅ **Claude Code leg DONE (2026-07-03)**, Codex/pi legs remaining. Live run (harness + numbers in `spikes/parked-loop/RESULTS.md`): wake ≤2 s after hand-off, ~44 s human→reply (reasoning-dominated), empty timeout exits silently at trivial token cost. **Design consequence found:** Claude Code blocks foreground sleep loops → the parked watch runs as a *background* task and the harness wakes the agent on exit — better than blocking; skill protocol amended (`skill/review-spec.md`). ✅ **Codex leg DONE (2026-07-03): PASS, zero friction** — one `codex exec` drain cycle (gpt-5.5; headless Codex needs `--sandbox workspace-write`, supplied by spec-chat's own wrapper — no user-facing setup beyond skill + CLI): parked watch ran in-sandbox, dialect-correct edit, schema-correct reply event, cursor updated; ~20 s hand-off→reply, 42 k tokens/cycle. Consequence: at that per-cycle cost, Codex's watch belongs OUTSIDE the session — a thin wrapper invokes `codex exec` per batch (the anticipated headless pattern becomes Codex's default). Still owed: pi leg (extension or external-watch wrapper); (d) 30+-cycle drift test; permission-prompt behavior on a stock setup.
3. **Round-trip edit fidelity** — ✅ **DONE (2026-07-03), PASS.** Island JSON (markLine) + prose edits landed first-try via exact string matching; the dialect's pretty-printed islands and one-sentence-per-line prose made match targets unambiguous; anchors untouched, diff clean.
4. **Generation quality** — can a skill get the agent to author a correct spec.html first try (islands, data-anchor on every block, one-sentence-per-line) with prompt discipline alone? If output drifts immediately, the deferred linter's trigger fires pre-v1.
5. **External-write visibility** — 🔶 harness ready (`spikes/parked-loop/spool-viewer.html`): open in Chromium, pick the `*.review/` dir, run the generator + an agent cycle, confirm agent-written replies render without re-granting. Verdict pending a human at a browser.
6. **Anchor robustness** (can wait) — annotate, agent aggressively restructures the section, count orphaned pins. Only decides whether deferred anchor-migration moves forward.

Efficient path: one harness covers 2+3+5 (synthetic events → parked CLI → real spec edits → browser renders replies) and retires both remaining review risks plus the FSA read-side unknown.

**🏁 Milestone 2 (2026-07-03): walking skeleton built.** `specs/.viz/runtime.js` is real: ECharts island hydration (vendored, animation off → deterministic renders), dialect document styling, the full annotation layer (pins/threads/composer/hand-off, universal anchoring incl. chart datums + axis marks via `triggerEvent`), and both transports — FSA primary (`file://`, IndexedDB-persisted handle, per user: local is the common path) with review-serve secondary (`http://`, auto-selected by protocol). Headlessly verified on the live spool: 6 threads → 6 pins, 1 chart. Remaining to verify by hand: FSA mode on a local Chromium; interactive composer/hand-off flows.

**🏁 Milestone 1 (2026-07-03): first real round-trip, over the remote transport.** Human annotated from a laptop browser through an SSH-tunneled `review-serve` → spool → parked Claude Code session → in-place spec edit → reply rendered back in the viewer (~60 s, context-bearing: the reply used same-thread knowledge from an earlier comment to propose the owner). Also observed: the channel naturally carries Q&A, not just change requests — informational replies with `change: "no spec change"` are part of the protocol now.

## Unresolved risks (flagged in review)
1. FSA reliability on `file://` origins — needs a spike before anything else. (Mitigation path: `review serve`.)
2. Per-CLI timeout/sandbox variance for `review-wait` — needs an adapter table (Claude Code / Codex / pi).
3. UX tolerance of the one-time "start live review mode" terminal step.

## Open questions
- Naming/branding.
- Do we ever annotate live apps (agentation/stagewise territory) or stay strictly on generated specs?
- Multi-user / async review — out of scope for v1?

## References
- agentation: https://github.com/benjitaylor/agentation (PolyForm Shield — ideas only)
- Plannotator: https://github.com/backnotprop/plannotator · stagewise: https://stagewise.io
- Warp block model: https://www.warp.dev/blog/block-model-behind-warps-agentic-development-environment
- tldraw Make Real: https://makereal.tldraw.com · v0 Design Mode: https://v0.app/docs/design-mode
- pi extensions: https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md · oh-my-pi: https://github.com/can1357/oh-my-pi
- FSA caveats: https://developer.mozilla.org/en-US/docs/Web/API/File_System_API · https://developer.chrome.com/docs/capabilities/web-apis/file-system-access
