# Event schema — reading and writing the spool

One JSON object per file. Read this instead of reverse-engineering the schema from `runtime.js`. Human events land in `<spec>.review/human/`, agent events in `<spec>.review/agent/`; filenames are `<ns-timestamp>-<event>-<id>.json` and sort into chronological order.

## Common fields

| field | who | notes |
|---|---|---|
| `id` | both | unique per event |
| `event` | both | `comment` \| `handoff` \| `reply` \| `edit` \| `status` |
| `actor` | both | `human` \| `agent` |
| `createdAt` | both | ISO 8601 |
| `schemaVersion` | both | currently `1` |

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

Marks a batch ready. `text` typically lists the comment ids. `anchorId` empty, `target` null. The watch wakes on this.

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

## Thread folding rules

- A human `comment` starts a thread; its id is the `threadId`.
- Human and agent `reply` events join the thread containing `respondsTo`.
- `edit` replaces the effective human message named by `supersedes`, while the original file remains immutable.
- Sort by event filename, collapse edits, then derive status from the newest effective human message. An agent reply acknowledges the thread only when it responds to that message id.
