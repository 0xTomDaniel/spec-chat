---
name: spec-chat-review
description: Run the spec-chat review loop - park on a visual HTML spec's annotation spool, address human comments as they arrive, edit the spec in place, and reply through the review channel. Use this whenever the user wants to review, annotate, or discuss a .spec.html file; says things like "start review mode", "watch for annotations", "address the annotations", "I'll comment in the browser"; mentions spec-chat, spec.html, hand-off batches, or a *.review/ directory; or asks about the status of a spec review. Also use it when the user authored a spec earlier in the session and now wants feedback round-trips on it, even if they don't name the tool - and when they ask to see, understand, or be walked through a spec.html, since the walkthrough should happen visually in the rendered page rather than as terminal text.
---

# spec-chat review loop

spec-chat specs are visual HTML documents (`*.spec.html`) the user annotates in a browser. Annotations arrive as one-file-per-event JSON in an actor-segregated spool next to the spec:

```
<spec>.spec.html.review/
  human/   ← browser writes here; you NEVER do
  agent/   ← you write here; the browser renders these live
```

Your job in review mode: wait for hand-off batches, apply each comment to the spec, reply through the spool, repeat — without the user ever returning to the terminal. The user's browser polls the spool, so your reply events and spec edits appear on their page within seconds.

## The loop

1. **Park** on the whole spec collection with the bundled multi-spec watch script through the host's **same-thread yielded/background wait primitive**:

   ```
   scripts/watch-specs.sh <spec-root> .cursor-<cli-or-session> 3600 3
   ```

   `<spec-root>` is normally the repository's shared `spec/` or `specs/` directory, even when review started from a page nested below it. When that collection links other reviewable HTML documents such as whitepapers, use the narrowest common ancestor containing all of them. One watcher recursively discovers every `*.html.review/` spool below that root, serializes ready batches, and keeps an independent cursor inside each spool. This deliberately follows review spools rather than requiring a `.spec.html` suffix, so non-spec visual documents can use the same channel without pretending to be normative specs. It exits 0 printing tab-separated `<html-path> <human-event-filename>` rows for the first ready page, or 3 on quiet timeout. On timeout, just re-park silently — an empty wakeup should cost almost nothing.

   The host attachment is part of the contract. During an explicitly active review window, keep the authoring turn open and wait on the yielded watcher so its completion re-invokes this same agent thread. The parked wait must be silent: do not emit idle heartbeats, periodic commentary, custom tool output, or spinner-producing polls into the chat. The shell may poll the spool internally, but the host wakes the agent only when the watcher exits with a real batch (or an actionable error).

   A yielded wait still leaves some hosts visibly in a long-running “working” state. Do not use it as an indefinite background service when the human expects the turn to look finished while idle. That experience requires a host-native event trigger or same-task automation capable of reactivating the existing thread. If the current surface does not expose one, state the limitation and ask the human to choose between an open active-review wait and detached unattended processing; do not claim that a shell process solves both. Merely leaving a process running, returning its session id, and finishing the turn cannot wake the chat. A detached `codex exec`, even one resumed with the same session id, also cannot stream activity into the already-open chat surface.

   Use `scripts/watch.sh <spec>.review/ <cursor-file> 3600 3` only when the human explicitly narrows review to one page or while debugging a page-specific problem. Per-page watching is not the default.

2. **Drain the batch.** Read each new event file listed. Rehydrate context from FILES — the current spec, the unresolved events, `<spec>.review/context.md` — not from what you remember of the chat. Chat history is never the review database; files are what survive compaction, session changes, and CLI switches.

3. **Apply each comment** to the spec in place, honoring the dialect (see below). A comment may also be a question rather than a change request — informational replies with `change: "no spec change"` are a normal part of the protocol; answer through the channel, don't force an edit.

4. **Reply per comment** with the bundled emitter (one event per comment addressed):

   ```
   scripts/emit-reply.sh <spec>.review/ <respondsTo-id> <anchorId> '<target-json>' acknowledged '<change-summary>' '<reply text>'
   ```

   Field exactness matters: the browser runtime renders `respondsTo`, `text`, `status`, and `change` — a missing or renamed field means the user sees nothing. End replies that made an edit with an offer to resolve ("OK to resolve?").

5. **Advance that spec's cursor by APPENDING exactly the filenames the watch reported**:

   ```
   printf '%s\n' <file1> <file2> >> <spec>.review/.cursor-<cli-or-session>
   ```

   Never regenerate the cursor with `ls` — events that arrived while you were processing would be silently marked as seen and skipped. This race was observed live; append-only is the fix.

6. **Externalize agreements** before re-parking: durable decisions go into the spec itself and a one-line note in `<spec>.review/context.md`. This is what lets a different session — or a different CLI — pick up the review cold.

7. **Re-park** (step 1). Continue until the user ends review mode.

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

`draft` → (hand-off) → `pending` → your reply makes it `acknowledged` → the human resolves (a `status` event with `resolved`). You never mark threads resolved yourself; you propose it.

## Event schema

Full field-by-field reference for reading and writing the spool: `references/event-schema.md`. Read it rather than reverse-engineering the schema from `runtime.js`.

## Per-CLI attachment

The loop is identical on every CLI; only how the collection watch is hosted differs. Interactive review must use an in-session attachment that re-invokes the open authoring thread. `scripts/codex-review.sh <spec-root>` is an explicitly detached, context-degraded fallback for unattended review after the authoring thread has closed; it is not interactive review. Details, plus the per-spec session-continuity and concurrency rules: `references/cli-adapters.md`.

## Transports (agent side is identical)

You only ever read and write spool files — the transport is the browser's problem. Two situations you may need to set up:

- **Local browser, same machine**: nothing to run; the page connects to the folder directly (file:// + FSA). Browser security does not reliably persist write permission. When an IndexedDB handle returns `prompt`, the runtime shows **Reconnect review folder** and opens a fresh directory picker; re-select the remembered ancestor and accept the browser's write confirmation. This explicit reconnect is deliberate: Arc can leave `requestPermission()` pending forever without showing permission UI even during trusted transient activation. The grant accepts ANY ancestor folder of the spec — pick it in the dialog or drag it from Finder onto the page; the runtime walks down to the spec's folder itself and remembers the ancestor. Caveats: Chromium refuses grants on the top-level roots themselves (home, Documents, Desktop, Downloads — children beneath them are fine), so suggest a workspace/projects folder one level down; if the granted tree contains two same-named specs at matching sub-paths the runtime refuses to guess and asks for a narrower grant. The spec's exact path also lands on the clipboard when the picker opens (⌘⇧G + paste in the macOS panel). If the user wants zero prompts (or uses Safari/Firefox, which lack FSA), run `assets/review-serve.py` locally exactly as in the remote setup, minus the tunnel: the http transport auto-connects.
- **Remote/SSH**: start `assets/review-serve.py <repo-root> 7160` as a background task and tell the user to tunnel with an explicit IPv4 destination — `ssh -L 7160:127.0.0.1:7160 user@host` (using `localhost` as the destination breaks: sshd tries ::1 first and the server binds IPv4 only). Page URL: `http://localhost:7160/<path-to-spec>`.

## Scaffolding spec-chat into a repo

The skill is self-contained: `assets/` carries the browser runtime (`viz/runtime.js` + vendored ECharts) and the remote-transport server (`review-serve.py`). If the target repo has no spec-chat infrastructure yet:

1. Create `specs/` at the repo root and copy `assets/viz/` to `specs/.viz/` (runtime + vendor, committed with the repo — vendoring is deliberate: specs must render without network).
2. Gitignore the spools: add `*.review/` to `.gitignore`.
3. Specs reference the runtime with `<script defer src="./.viz/runtime.js">` — a classic script, never `type="module"` (browsers CORS-block module scripts on `file://`, which is the primary local transport).

If the repo already has `specs/.viz/`, leave it alone — its version is the repo's contract. Copy `assets/review-serve.py` to `tools/` only if remote transport is needed and the repo lacks it.

**Exception — module-loading migration**: if an existing repo's runtime is loaded with `<script type="module">` (or its `runtime.js` still contains `import.meta.url`), it predates the classic-script fix and is broken on `file://` (browsers CORS-block module scripts there — the annotation layer silently never loads). On contact, replace the vendored `.viz/runtime.js` with this skill's copy and switch every spec's tag to `<script defer src="./.viz/runtime.js">`.

## Starting a review when asked

1. Confirm the page exists and identify the shared collection root (normally the repository's `spec/` or `specs/` directory, not the page's immediate subdirectory; use the narrowest common ancestor when linked reviewable whitepapers or other HTML documents live outside it).
2. Set up transport if remote (above).
3. Park one collection watcher with `scripts/watch-specs.sh <spec-root> .cursor-<cli-or-session> 3600 3` using the host's same-thread yielded/background wait, keep the turn open, and tell the user the page URL and that every reviewable HTML document below the root is covered. The watcher discovers a spool as soon as the browser creates it; its ready output must wake this same thread for the drain cycle.
4. Use `scripts/codex-review.sh <spec-root>` only when the human explicitly chooses unattended detached review after the interactive thread closes. State that detached mode will not wake or show live activity in the authoring chat. Passing a specific HTML file remains an explicit single-page override.

If asked only for **status** (no review mode), read the spool, summarize threads by status, and don't edit anything.

## Get out of the terminal — visual-first

When the user asks to see, understand, or walk through a spec ("what's in this spec?", "walk me through it"), don't answer with a terminal summary — the whole point of spec-chat is that the spec is better experienced rendered. Set up the visual surface (open the file locally, or start the serve + tunnel if remote), start review mode, and offer to have the conversation in-page: they can pin questions on the elements they're asking about and your walkthrough arrives as replies anchored to the exact marks. A terminal summary is the fallback when the user can't open a browser, not the default.
