# Event schema — reading and writing the spool

One JSON object per file. Read this instead of reverse-engineering the schema from `runtime.js`. Human events land in `<spec>.review/human/`, agent events in `<spec>.review/agent/`. A scope's hosted threads (`comments/<scope>/` in the review service's state, see `## Hosted threads`) are a spool of the same shape with no spec. The writer names each file `<createdAt ns>-<event>-<id>.json` (the page for human events, `emit-reply.sh` for agent events); names sort into chronological order. Files are immutable: never renamed, rewritten, or deleted.

## Storing over HTTP

The page posts a human event to `POST /api/events?dir=<spec>.review&actor=human&name=<file name>`, the name chosen before the first send. A resend after a lost response stores nothing new: the same name with the same bytes answers `200 {"ok":true,"name":...}`; the same name with different bytes is refused `409`. A name whose `<event>-<id>` differs from the body is `400`. `actor=agent`, or a body `actor` other than `human`, is `403`: agent events are written only on disk. A `status` event whose `status` is not `resolved` is `400`. A post without `name` takes the service clock (pages from before page-named events).

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

A comment written on a host page through the comment layer also carries these fields from the element's anchor, and no `version` (on a spec it resolves by anchor and quote in the current spec):

| field | notes |
|---|---|
| `kind` | the host's words for what kind of thing the subject is: its routing word, carried and shown, never compared |
| `label` | words naming the subject as the host page shows it, or `null` |
| `from` | the address that reopens what the human saw |
| `subject` | only in hosted threads, in place of `anchorId`: what the thread attaches to, compared whole |

On a subject naming a spec its scope serves, the comment sits in that spec's spool with that anchor as `anchorId` and no `subject`. A spec page's comment carries none of these fields. The layer's replies, edits, and hand-offs carry no host field; they join their thread by reference.

```json
{"id":"u7","event":"comment","subject":"agent-turn:w1:p3:42","kind":"agent turn","label":"Turn 42",
 "from":"https://host.example/panes/w1:p3#turn-42","target":{"type":"text","key":"exit status 2"},
 "quote":"exit status 2","text":"Question: why exit 2?","actor":"human","browser":"...","author":"Mango",
 "createdAt":"...","schemaVersion":1}
```

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

## Hosted threads

A scope's hosted threads hold every host page comment whose subject names no spec its scope serves: `comments/<scope>/{human,agent}/` and the owners' cursors, in `$XDG_STATE_HOME/spec-chat/hosting/${HERDR_SESSION:-default}`. The wake prompt names the path and cursor. The owner reads them on disk like a spec's spool, with no running service:

- Scan: `watch-specs.sh <path> <cursor> 0 3` prints `<path> <event-filename>` rows; there is no spec, so no `place` line.
- Host line (stderr, spec spools too): `host <event-filename> <subject> <kind> <label> <from> <quote>` for each reported event of a thread written on a host page, the thread's root comment's fields from the spool fold (`assets/spool.py`), so a follow-up reply or edit routes as its thread does. Empty for an absent field; `subject` is empty in a spec's spool, where the `place` line gives the anchor; whitespace runs read as one space. `kind` is the host's routing word; Spec Chat names no host and no routing policy.
- Reply: `emit-reply.sh <path> <respondsTo-id> '' '<target-json>' ...`: `anchorId` is `""`; the reply joins its thread by `respondsTo`, and every host page anchoring the subject in that scope shows it.
- Cursor: append the scan's file names to `<path>/<cursor>`.

## Thread folding rules

Two folds apply these rules and agree: the page's `foldThreads` in the runtime and `fold_threads` in `assets/spool.py`, which every service and agent reader uses (review index, Jev).

- A human `comment` starts a thread; its id is the `threadId`.
- Human and agent `reply` events join the thread containing `respondsTo`.
- `edit` replaces the effective human message named by `supersedes`, while the original file remains immutable.
- Sort by event filename, collapse edits, then derive status from the newest effective human message. An agent reply acknowledges the thread only when it responds to that message id.
- A status an event names counts only when it is `acknowledged`, `orphaned`, or `resolved`: an agent reply naming any other value acknowledges, and a `status` event naming one changes nothing.
