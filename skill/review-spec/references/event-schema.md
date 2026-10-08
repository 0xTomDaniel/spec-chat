# Event schema — reading and writing the spool

One JSON object per file. Read this instead of reverse-engineering the schema from `runtime.js`. Human events land in `<spec>.review/human/`, agent events in `<spec>.review/agent/`. The writer names each file `<createdAt ns>-<event>-<id>.json` (the page for human events, `emit-reply.sh` for agent events); names sort into chronological order. Files are immutable: never renamed, rewritten, or deleted.

## Storing over HTTP

The page posts a human event to `POST /api/events?dir=<spec>.review&actor=human&name=<file name>`, the name chosen before the first send. A resend after a lost response stores nothing new: the same name with the same bytes answers `200 {"ok":true,"name":...}`; the same name with different bytes is refused `409`. A name whose `<event>-<id>` differs from the body is `400`. `actor=agent`, or a body `actor` other than `human`, is `403`: agent events are written only on disk. A post without `name` takes the service clock (pages from before page-named events).

## Spec versions

`<spec>.review/versions/<sha256>.html` holds each spec text the service served, by the SHA-256 of its bytes, which is also the page's ETag. Written once by the service; readers (the mark resolver) read it as a plain file.

## Common fields

| field | who | notes |
|---|---|---|
| `id` | both | unique per event |
| `event` | both | `comment` \| `handoff` \| `reply` \| `edit` \| `status` |
| `actor` | both | `human` \| `agent` |
| `createdAt` | both | ISO 8601 |
| `schemaVersion` | both | currently `1` |
| `browser` | human | random id the page creates once and keeps in this browser |
| `author` | human | fruit name assigned to that browser; a label, not an identity |
| `version` | human | SHA-256 of the spec text the page showed (its ETag); names `versions/<version>.html`. Absent on older events |

## `comment` (human)

```json
{"id":"u1","event":"comment","anchorId":"latency-budget",
 "target":{"type":"datum","key":"enqueue"},
 "quote":"bar: enqueue · 1180","text":"add an 800ms target line",
 "actor":"human","createdAt":"...","schemaVersion":1}
```

- `anchorId`: the `data-anchor` block the pin lives in.
- `target`: narrows to an element within the block, or `null` for the whole block. Types: `datum` / `axis-x` / `axis-y` / `target` (chart marks, `key` is the datum/tick/markLine value) · `node` / `edge` / `note` (diagram parts) · `element` (`key` is a structural path — which is also a source location, since the spec IS the source file) · `text` (`key` is the selected quote).
- `element` key grammar: `p[2]`-style positional (nth tag within the anchored block) · `button#cycle-play`-style id-based (used when the element has a unique id — survives reordering) · `svg[1]/g[2]/path[5]`-style slash-separated paths for children of inline SVG figures (each segment is `tag[n]` among same-tag siblings, or `tag#id`).
- `datum` targets may additionally carry `seriesIndex`/`dataIndex` (pin-positioning hints) and `chartKey` (disambiguates when one anchored block holds several charts). `key` stays the greppable value — interpret anchors from it; the extra fields are for the browser runtime.
- `legend` (`key` is the legend/series name): a comment on a chart's legend entry — the series as a whole, not one datum. `target` also covers markPoint/markArea values, not just markLines.
- `quote`: captured surrounding text — the fallback if the anchor later moves.

## `handoff` (human)

Marks a batch ready. `anchorId` empty, `target` null. The watch wakes on this.

```json
{"id":"h1","event":"handoff","events":["u1","e1"],"anchorId":"","target":null,
 "text":"","actor":"human","browser":"...","author":"Mango","createdAt":"...","schemaVersion":1}
```

- `events`: the event ids it hands off, its browser's drafts. Only those become `pending`; other reviewers' drafts stay `draft`.
- No `events` (written before this field): hands off every draft before it.
- The batch the zero-wait scan prints and host wake counts is each hand-off not in the cursor plus the events it lists (`assets/spool.py`).

## `reply` (human or agent)

A human reply follows an agent message without creating a detached thread:

```json
{"id":"u2","event":"reply","respondsTo":"r1","threadId":"u1",
 "anchorId":"latency-budget","target":{"type":"datum","key":"enqueue"},
 "quote":null,"text":"Use 750ms instead.","actor":"human",
 "createdAt":"...","schemaVersion":1}
```

- `respondsTo`: the exact agent reply being answered.
- `threadId`: the root human comment id.
- A new human reply is a `draft` until the next hand-off, then `pending` until the agent answers that reply id.

An agent reply is written by `emit-reply.sh`:

```json
{"id":"r...","event":"reply","respondsTo":"u1","anchorId":"latency-budget",
 "target":{...echo the comment's target...},"text":"...",
 "status":"acknowledged","change":"edited #latency-budget: +target line",
 "actor":"agent","createdAt":"...","schemaVersion":1}
```

- `respondsTo`: the exact human `comment`, `reply`, or `edit` id being answered. Do not respond to the root id when a newer human message is pending.
- `status`: `acknowledged` (addressed, awaiting human resolve) or `orphaned` (anchor gone — quote the stored quote, don't guess).
- `change`: short summary the page badges, or `"no spec change"` for informational replies.

## `edit` (human)

Human-authored comments and follow-up replies remain append-only on disk. Editing an unanswered `draft` or `pending` message writes a replacement event:

```json
{"id":"e1","event":"edit","supersedes":"u2","threadId":"u1",
 "anchorId":"latency-budget","target":{"type":"datum","key":"enqueue"},
 "quote":null,"text":"Use 725ms instead.","actor":"human",
 "createdAt":"...","schemaVersion":1}
```

- `supersedes`: the human message id whose displayed text this event replaces.
- The edit repeats the effective anchor, target, quote, and full replacement text so each event is self-contained.
- An edit becomes the newest human message id. It returns the thread to `draft`; after hand-off it becomes `pending`, and the agent replies to the edit id.
- Collapse each supersession chain before acting. Never apply or answer text that a later edit supersedes.

## `status` (human) — resolution

```json
{"id":"s1","event":"status","respondsTo":"u1","status":"resolved",
 "actor":"human","createdAt":"...","schemaVersion":1}
```

Only the human resolves; the agent proposes it ("OK to resolve?"). Lifecycle: `draft` (new human comment/reply/edit, pre-hand-off) → `pending` → `acknowledged` (agent replied to the newest human message) → `resolved`.

## Resolved place (read side)

`/api/events` returns each event as `{"actor","name","body","place"}`. `place` is derived, never stored: `null` for every event but a `comment` with an `anchorId` (a thread is placed by its root comment; replies and edits never move it), else `{"anchorId","start","end","quote","state"}` from `assets/place.py`. It locates the mark in `<spec>.review/versions/<body.version>.html` and maps it through a character diff to the current spec (an event without `version` is located in the current spec). `start`/`end` are code-point offsets into the current spec source; `quote` is that range's visible text for a text target. `state`: `kept` (every character survives), `changed` (some survive), `gone` (none; `anchorId` is the nearest surviving anchored block, range and quote null). The zero-wait scan prints the same place on stderr.

## Thread folding rules

Two folds apply these rules and agree: the page's `foldThreads` in the runtime and `fold_threads` in `assets/spool.py`, which every service and agent reader uses (review index, Jev).

- A human `comment` starts a thread; its id is the `threadId`.
- Human and agent `reply` events join the thread containing `respondsTo`.
- `edit` replaces the effective human message named by `supersedes`, while the original file remains immutable.
- Sort by event filename, collapse edits, then derive status from the newest effective human message. An agent reply acknowledges the thread only when it responds to that message id.
