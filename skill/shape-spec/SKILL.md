---
name: spec-chat-shape
description: Shape a new work unit from a plain-language feature prompt into a current issue and canonical Spec Chat HTML seed, then run Git-focused in-page review. Use when starting new product or development work in a Spec Chat repository. Use spec-chat-review alone for review-only work on an existing spec.
---

# Spec Chat Shape

Create durable current truth before deep investigation.
Keep instructions, artifacts, and questions concise and human-readable.

## Contract

- The target repository selects a separate issue skill that owns tracker-specific create, read, replace-current-content, and link operations.
- The issue owns current high-level intent, outcome, criteria, non-goals, dependencies, and governing source links.
- Canonical specs own current user stories, detailed behavior, constraints, edge cases, and interaction contracts.
- Every changed spec links each issue whose accepted work materially changed that file.
- The highlighted current HTML spec is the required human diff-review surface.
- Chat and model memory never override current durable sources.
- Every accepted spec change is committed and pushed before the refreshed focus view is presented.
- The review protocol remains owned by `spec-chat-review`; do not copy it here.

## Start

1. Receive the feature prompt.
2. Resolve the repository-selected issue skill.
3. Immediately create a fresh placeholder issue from the best concise current interpretation.
4. Keep the issue always-most-recent: replace stale prose, remove resolved TBDs, and never retain the raw prompt or accumulated summaries.
5. Create the work branch using target conventions; its local Git history supplies the focus baseline.

Missing issue-surface access is a visible blocker.

## Short seed pass

Read only the smallest durable context needed to choose the spec surface:

- target instructions
- spec index
- clearly related specs and ADRs
- directly relevant product or workflow docs

Defer code, tests, configuration, and packages unless durable docs conflict or cannot identify the correct spec surface.
Keep only governing source links in the issue, never a research bibliography.

Then write:

- one concise current issue seed
- one concise current spec seed

Edit an existing canonical spec when one governs the area.
Otherwise create a minimal capability or domain spec with only sections that contain real behavior.
Append the issue link to the spec's source-issues list.

## Author the spec

Read [references/information-shape.md](references/information-shape.md) for every new spec or material restructure.
Read [references/visual-doctrine.md](references/visual-doctrine.md) when selecting or generating visual media.

Canonical `*.spec.html` documents use the non-React doctrine.
React is limited to an existing embedded application or an explicitly noncanonical throwaway prototype.
Keep stable `data-anchor` identities, one sentence per prose line, and pretty semantic-island JSON.

## Publish the seed

1. Commit and push the first issue-backed spec seed.
2. Open one evolving draft change request using target conventions.
3. Start the existing HTTP review server against the narrow review collection, never the repository root.
4. Publish an unguessable HTTPS capability URL through the repository or host's configured public transport.
5. Require no SSH, VPN, or separate login; possession of the URL is the only review authentication.
6. Tell the human the URL is a secret and never publish it into the issue or change request.
7. Open the spec with `focus=changes&base=<exact-local-change-request-base>` so stacked branches compare against their real base and the runtime derives changed current blocks from local Git.
8. Start `spec-chat-review` in its active in-session attachment mode.

Never fetch or mutate Git from the review server.
If no safe public capability transport exists, stop rather than exposing the repository or substituting SSH.

## Grill in the spec

Put unresolved material questions into temporary anchored TBD blocks carrying `data-spec-tbd`.
Ask a small batch only when its questions are internally orthogonal.
Keep questions sequential when one answer could materially redirect another.
Never dump the complete grill at once.

The human answers through ordinary Spec Chat comments.
For each handed-off batch:

1. Fold current comments and edits through `spec-chat-review`.
2. Apply the smallest coherent orthogonal spec changes.
3. Replace changed high-level issue truth.
4. Commit and push every accepted spec change.
5. Emit replies only after the durable spec write succeeds.
6. Advance the exact cursor only after the complete batch succeeds.
7. Repark the same authoring turn.

Replace or remove each resolved TBD immediately.

## Finish review

Finish only after no draft, pending, acknowledged, unresolved, or material TBD work remains.
An empty hand-off means explicit completion of the browser review window.
Reconcile issue and spec, push any final change, advance the cursor, stop the watcher, close the public transport, and invalidate its capability URL.

Finish review does not mean implementation authorization, acceptance, tracker lifecycle transition, merge approval, or deployment approval.

## Burden limit

Do not add a database, job ledger, capsule file, daemon, generic supervisor, heartbeat, presence sentinel, lease system, fencing system, tracker adapter registry, new event protocol, copied task context, or background infrastructure.
Same-session continuation is an optimization; current files are the recovery contract.
