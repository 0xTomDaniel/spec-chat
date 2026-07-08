# Event schema — reading and writing the spool

One JSON object per file. Read this instead of reverse-engineering the schema from `runtime.js`. Human events land in `<spec>.review/human/`, agent events in `<spec>.review/agent/`; filenames are `<ns-timestamp>-<event>-<id>.json` and sort into chronological order.

## Common fields

| field | who | notes |
|---|---|---|
| `id` | both | unique per event |
| `event` | both | `comment` \| `handoff` \| `reply` \| `status` |
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
- `quote`: captured surrounding text — the fallback if the anchor later moves.

## `handoff` (human)

Marks a batch ready. `text` typically lists the comment ids. `anchorId` empty, `target` null. The watch wakes on this.

## `reply` (agent) — written by `emit-reply.sh`

```json
{"id":"r...","event":"reply","respondsTo":"u1","anchorId":"latency-budget",
 "target":{...echo the comment's target...},"text":"...",
 "status":"acknowledged","change":"edited #latency-budget: +target line",
 "actor":"agent","createdAt":"...","schemaVersion":1}
```

- `respondsTo`: the comment `id`. The runtime threads replies by this.
- `status`: `acknowledged` (addressed, awaiting human resolve) or `orphaned` (anchor gone — quote the stored quote, don't guess).
- `change`: short summary the page badges, or `"no spec change"` for informational replies.

## `status` (human) — resolution

```json
{"id":"s1","event":"status","respondsTo":"u1","status":"resolved",
 "actor":"human","createdAt":"...","schemaVersion":1}
```

Only the human resolves; the agent proposes it ("OK to resolve?"). Lifecycle: `draft` (pre-hand-off) → `pending` → `acknowledged` (agent replied) → `resolved`.
