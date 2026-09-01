---
name: spec-chat-review
description: "Run the spec-chat review loop - park on a visual HTML spec's annotation spool, address human comments as they arrive, edit the spec in place, and reply through the review channel. Use this whenever the user wants to review, annotate, or discuss a .spec.html file; says things like start review mode, watch for annotations, address the annotations, or I'll comment in the browser; mentions spec-chat, spec.html, hand-off batches, or a *.review/ directory; or asks about the status of a spec review. Also use it when the user authored a spec earlier in the session and now wants feedback round-trips on it, even if they don't name the tool - and when they ask to see, understand, or be walked through a spec.html, since the walkthrough should happen visually in the rendered page rather than as terminal text."
---

# spec-chat review loop

spec-chat specs are visual HTML documents (`*.spec.html`) the user annotates in a browser. Annotations arrive as one-file-per-event JSON in an actor-segregated spool next to the spec:

```
<spec>.spec.html.review/
  human/   ← browser writes here; you NEVER do
  agent/   ← you write here; the browser renders these live
```

Your job in review mode: reconcile hand-off batches, apply each comment to the spec, reply through the spool, and leave review in one truthful terminal control state.

## The loop

1. **Reconnect before parking.** On every newly started or resumed spec-chat turn, first run one immediate, read-only collection scan:

   ```
   scripts/watch-specs.sh <spec-root> .cursor-<cli-or-session> 0 3
   ```

   Exit 0 prints tab-separated `<html-path> <human-event-filename>` rows for the first ready page; exit 3 means the backlog is empty. A ready result contains every unprocessed event through the newest completed hand-off, in filename/event order. Do not reuse, poll, or wait on a watcher/tool session cancelled with the prior turn, and do not start detached Codex processing automatically—the detached process could race the interactive thread.

2. **Drain the reported batch.** Read each event file in the printed order. Rehydrate context from FILES — the current spec, the unresolved events, `<spec>.review/context.md` — not from what you remember of the chat. Chat history is never the review database; files are what survive compaction, session changes, CLI switches, and stopped turns. Fold each thread before acting: human `reply` events continue the existing conversation, and human `edit` events replace the message named by `supersedes`. Ignore superseded text.

3. **Apply each comment** to the spec in place, honoring the dialect (see below). A comment may also be a question rather than a change request — informational replies with `change: "no spec change"` are a normal part of the protocol; answer through the channel, don't force an edit.

4. **Publish accepted spec changes before reply.** When the active shaping contract requires durable publication, commit and push every accepted spec change before the browser receives its reply or refreshed Git focus. `spec-chat-shape` owns the exact issue and change-request order. Review-only work follows its caller's publication contract.

5. **Reply to each newest human message** with the bundled emitter (one event per comment, follow-up reply, or edit addressed):

   ```
   scripts/emit-reply.sh <spec>.review/ <respondsTo-id> <anchorId> '<target-json>' acknowledged '<change-summary>' '<reply text>'
   ```

   `respondsTo-id` must be the exact newest human `comment`, `reply`, or `edit` id—not automatically the root comment id. Field exactness matters: the browser runtime renders `respondsTo`, `text`, `status`, and `change` — a missing or renamed field means the user sees nothing. End replies that made an edit with an offer to resolve ("OK to resolve?").

6. **Externalize agreements** after the spec changes and replies succeed: durable decisions go into the spec itself and a one-line note in `<spec>.review/context.md`. This is what lets a different session — or a different CLI — pick up the review cold.

7. **Advance that spec's cursor only after the entire reported batch was successfully processed and every reply was emitted**, by APPENDING exactly the filenames the scan reported:

   ```
   printf '%s\n' <file1> <file2> >> <spec>.review/.cursor-<cli-or-session>
   ```

   Never regenerate the cursor with `ls` — events that arrived while you were processing would be silently marked as seen and skipped. Never advance it after a partial or failed drain; leaving it unchanged makes the next turn recover the same durable batch. This append-only rule is the lossless commit point.

8. **Reconcile to empty, then select one terminal control state.** Repeat the zero-wait scan and steps 2–7 until it exits 3, then choose exactly one:

   - `turn-yielded`: run `scripts/review-control.sh yielded <spec-root> .cursor-<cli-or-session> 3600 3` through a verified same-turn yield and keep this turn open. A final response is forbidden.
   - `external-wake`: run `scripts/review-control.sh external <spec-root> .cursor-<cli-or-session> <owner-id> <owner-session> <adapter> [args...]` in a persistent foreground host-owned terminal. Final is allowed only after the adapter verifies the exact owner identity.
   - `manual-resume`: run `scripts/review-control.sh manual`, return a final response that explicitly requires a new human chat message, and claim no automatic wake.

   `<spec-root>` is normally the repository's shared `docs/` collection root.
   `review-control.sh` holds one local kernel lock per canonical collection root and cursor, so a second yielded or external owner fails visibly instead of racing the first.
   A raw `watch-specs.sh` or `watch.sh` long wait is detection-only and now fails unless invoked by `review-control.sh`.
   A background shell, unified exec session, watcher PID, or returned tool session never proves host attachment.
   Before any final response, transition out of `turn-yielded` into verified `external-wake` or explicit `manual-resume`.
   Detached Codex processing is disabled because it can race the interactive owner.

   For Herdr, run the external monitor in its own visible Herdr pane with `scripts/wake-herdr.py` as the adapter.
   Bind the exact owner pane as `<owner-id>` and its current Herdr `terminal_id` as `<owner-session>`.
   The adapter validates both before using modal-safe Herdr prompt transport.
   Herdr owns only wake; the reactivated authoring pane performs the zero-wait scan and batch transaction.

## The spec dialect (how to edit)

- The spec HTML IS the canonical document. One sentence per line in prose; stable `data-anchor` attributes on every block — never remove or rename them (pins anchor to them).
- Visual state lives in semantic islands: `<script type="application/spec+json" data-render="chart" data-lib="echarts">` with pretty-printed JSON, rendered into a sibling `[data-render-target]`. Edit the island JSON, not rendered output. Pretty-printing is what makes your string-match edits land unambiguously — keep it.
- New meaningful elements get sensible anchors; new sections get `data-anchor` + an `<h2>`.

## Anchors in events

`anchorId` names the block; `target` narrows to an element within it:
- `{"type":"datum","key":"enqueue"}` — a chart mark; grep the key in the island JSON
- `{"type":"axis-y","key":"800"}` / `{"type":"target","key":"800"}` — axis ticks / markLines
- `{"type":"element","key":"p[2]"}` — 2nd `<p>` within the anchored block (structural path = source location, since the spec is the source file)
- `{"type":"text","key":"<quote>"}` — a text selection; the quote tells you the passage

If an anchor or target no longer exists (the spec moved under the pin), reply with `status: "orphaned"`, quoting the event's stored quote — don't guess at intent.

## Statuses

Every new human comment, follow-up reply, or edit is `draft` → (hand-off) → `pending` → your reply to that exact message id makes the thread `acknowledged` → the human resolves (a `status` event with `resolved`). You never mark threads resolved yourself; you propose it.

A resolved thread remains expandable. When its latest message is from the agent, the browser offers **Reply and reopen**. Saving that response writes the existing human `reply` event and naturally derives `draft`; there is no reopen status or new event type.

If a hand-off remains unacknowledged past the existing timeout, the browser states that automatic wake did not occur and instructs the human to send a new chat message to resume.

When every thread is resolved and no material TBD remains, the no-draft action becomes **Finish review**. It writes the existing empty hand-off. Reconcile it, settle any final durable change, advance the exact cursor, stop the watcher, close any public capability transport, and end the active review window. Finish review is not implementation authorization, acceptance, merge approval, or deployment approval.

## Event schema

Full field-by-field reference for reading and writing the spool: `references/event-schema.md`. Read it rather than reverse-engineering the schema from `runtime.js`.

## Per-CLI attachment

The loop is identical on every CLI; only the verified wake adapter differs. Read `references/cli-adapters.md` before selecting `turn-yielded`, `external-wake`, or `manual-resume`. `scripts/codex-review.sh` is a compatibility tombstone and never launches detached processing.

## Git-derived focus

Prompt-first shaping opens the HTTP page with `focus=changes&base=<exact-local-change-request-base>`. The runtime reads baseline HTML through the review server, compares stable current anchor signatures, keeps added or modified current blocks clear, and recedes unchanged current blocks. A new spec remains entirely clear. A normal URL renders every block at normal clarity. Automatic base discovery is only a fallback for direct unstacked review.

The review server reads only local Git. It never fetches, checks out, stages, commits, or writes repository state. If no baseline is available, the browser shows a visible warning and the complete current spec without stale focus.

The highlighted current spec is the diff viewer. Do not require pull-request review, a side-by-side page, deleted-content ghosts, issue metadata, anchor lists, or a stored focus manifest.

## Transports (agent side is identical)

You only ever read and write spool files — the transport is the browser's problem. Two situations you may need to set up:

- **Local browser, same machine**: nothing to run; the page connects to the folder directly (file:// + FSA). Browser security does not reliably persist write permission. When an IndexedDB handle returns `prompt`, the runtime shows **Resume review** and requests write permission on the already-selected handle; **Choose different folder** remains a separate picker fallback for a moved tree, wrong prior scope, or Chromium shell that does not surface the regrant prompt. Chromium can follow the native directory picker with a separate **Allow this site to edit files?** browser window; the runtime must name that step and visibly wait for it because shells such as Arc may not layer it over the spec window. The grant accepts ANY ancestor folder of the spec — pick it in the dialog or drag it from Finder onto the page; the runtime walks down to the spec's folder itself and remembers the ancestor. Caveats: Chromium refuses grants on the top-level roots themselves (home, Documents, Desktop, Downloads — children beneath them are fine), so suggest a workspace/projects folder one level down; if the granted tree contains two same-named specs at matching sub-paths the runtime refuses to guess and asks for a narrower grant. The spec's exact path also lands on the clipboard when the picker opens (⌘⇧G + paste in the macOS panel). If the user wants zero prompts or uses Safari or Firefox, run `assets/review-serve.py` on loopback; the HTTP transport auto-connects.
- **Public capability review**: for prompt-first shaping or any review that must open from anywhere, start `assets/review-serve.py` on loopback against the narrow review collection, never the repository root. Publish that origin through the repository or host's configured public HTTPS capability transport. The unguessable URL is the only authentication: require no SSH, VPN, or separate login; tell the human to treat it as a secret; never publish it into the issue or change request. Stop the transport when review ends so the URL becomes invalid. The protocol does not depend on a specific tunnel provider.

## Scaffolding spec-chat into a repo

The skill is self-contained: `assets/` carries the browser runtime (`viz/runtime.js` + vendored ECharts) and the remote-transport server (`review-serve.py`). If the target repo has no spec-chat infrastructure yet:

1. Create `docs/specs/` and `docs/adr/` at the repo root. Copy `assets/viz/` to `docs/specs/.viz/` (runtime + vendor, committed with the repo — vendoring is deliberate: specs must render without network). Put shared visual-spec styles in `docs/specs/.style/` when the collection uses them.
2. Gitignore the spools: add `*.review/` to `.gitignore`.
3. Reference shared assets relative to the page: a root `docs/specs/*.spec.html` page uses `<script defer src="./.viz/runtime.js">`; a nested page such as `docs/specs/domains/*.spec.html` uses `../.viz/runtime.js`; and `docs/adr/*.spec.html` uses `../specs/.viz/runtime.js` (plus the equivalent `.style/` path). The runtime is a classic script, never `type="module"` (browsers CORS-block module scripts on `file://`, which is the primary local transport).

Before every initial or resumed review, run the self-contained compatibility preflight from this skill:

```
python3 scripts/preflight.py <target-repository-root> <spec-html>
```

Preflight preserves target runtime and server assets that declare every capability required by the current bundle.
It replaces only incompatible assets from the bundle, including vendored visual dependencies when runtime migration is required, and rejects semantic islands without a same-parent `data-render-target`.
Treat a preflight failure as a review blocker.
Commit and push migrated assets before presenting a shaping review.
If the server was already running when migration occurred, restart it on the same local port before retaining its public relay.

**Exception — module-loading migration**: if an existing repo's runtime is loaded with `<script type="module">` (or its `runtime.js` still contains `import.meta.url`), it predates the classic-script fix and is broken on `file://` (browsers CORS-block module scripts there — the annotation layer silently never loads). On contact, replace the vendored `.viz/runtime.js` with this skill's copy and switch every page to a classic `<script defer>` tag using the correct relative path described above.

## Starting a review when asked

1. Confirm the page exists, run `scripts/preflight.py`, and identify the shared collection root (normally the repository's `docs/` directory, not the page's immediate `docs/specs/`, `docs/specs/<domain>/`, or `docs/adr/` directory; use the narrowest common ancestor for a legacy or explicitly different layout).
2. Start or restart the local server when HTTP review is required, then verify the served runtime advertises the required capabilities and `/api/baseline` succeeds for the exact page and change-request base.
3. Set up the public capability transport when the page must open outside the file host.
4. On both an initial start and any resumed/reconnected turn, run `scripts/watch-specs.sh <spec-root> .cursor-<cli-or-session> 0 3`; drain, reply, and cursor each ready batch, then repeat until exit 3.
5. After reconciliation is empty, establish `turn-yielded`, verified `external-wake`, or `manual-resume` through `scripts/review-control.sh`.
6. State the selected control state truthfully. Never say watching, attached, or active after final unless `external-wake` is verified.

## Mobile review contract

The HTTP review transport supports phone and tablet annotation as a first-class
surface. At narrow viewports the toolbar must stay inside the visual viewport,
all review controls must provide at least a 44px touch target, textareas must use
at least 16px text to avoid browser input zoom, and the review panel must use a
safe-area-aware full-viewport sheet without shifting the spec sideways.

Entering Comment mode on a narrow viewport closes the sheet so the human can tap
the document target. Tapping an anchored target then reopens the sheet with the
composer focused. Preserve this target-first sequence when changing panel or
toolbar behavior; a full-screen sheet that remains open during target selection
makes mobile annotation impossible.

The mobile toolbar keeps Hand off beside Comment. It stays disabled with no
drafts, displays the current draft count, and emits the same handoff event as the
panel action.

Prefer a page viewport declaration containing
`width=device-width, initial-scale=1, viewport-fit=cover`. The runtime still
contains itself when `viewport-fit=cover` is absent, but the declaration lets its
safe-area padding protect controls on notched devices.

If asked only for **status** (no review mode), read the spool, summarize threads by status, and don't edit anything.

## Get out of the terminal — visual-first

When the user asks to see, understand, or walk through a spec ("what's in this spec?", "walk me through it"), don't answer with a terminal summary — the whole point of spec-chat is that the spec is better experienced rendered. Set up the visual surface (open the file locally, or start the serve + tunnel if remote), start review mode, and offer to have the conversation in-page: they can pin questions on the elements they're asking about and your walkthrough arrives as replies anchored to the exact marks. A terminal summary is the fallback when the user can't open a browser, not the default.
