---
name: spec-chat-shape
description: Shape a feature prompt into a current issue, canonical Spec Chat HTML spec, and dependency-linked implementation tickets. Also use for any existing-spec review that materially changes behavior or information architecture. Use spec-chat-review alone only for questions and atomic corrections.
---

# Spec Chat Shape

Create durable current truth before deep investigation.

## Invariants

- The repository-selected issue skill owns tracker operations; Spec Chat contains no tracker dependency.
- The issue owns current intent, outcomes, criteria, non-goals, dependencies, and governing links.
- The canonical spec owns current stories, detailed behavior, constraints, edge cases, and interaction contracts.
- Implementation tickets own independently assignable outcomes and blocking relations.
- Chat and memory never override durable sources; stop on source conflict.
- Accepted spec changes are committed and pushed before refreshed Git focus or review replies.
- `spec-chat-review` alone owns browser review, spool transactions, transport, wake, and recovery.

## Shape

1. Resolve the repository-selected issue skill, create a concise placeholder issue immediately, and create the work branch using target conventions. Missing issue access is a visible blocker.
2. Read only target instructions, the spec index, clearly related specs or ADRs, and directly relevant product docs. Update the governing spec or create a minimal spec containing only real behavior; append the issue to its source-issues list and keep only governing links, not a research bibliography. Commit, push, and open one evolving draft change request.
3. Deepen only from relevant code, tests, configuration, and change-request state. Resolve discoverable questions before asking the human. Keep the issue always-most-recent and remove stale prose or resolved TBDs.
4. Keep every changed user outcome current in the target-declared story source or governing spec. For cross-module behavior, reconcile stable responsibilities, seams, and dependency direction with the target-declared architecture source while keeping issue-specific detail in the spec.
5. For every new spec or material restructure, read [references/authoring.md](references/authoring.md) completely before editing. Existing specs are not grandfathered. Before publication, run `python3 scripts/validate-style.py <repository> <spec-html> <exact-change-request-base>` and stop on failure.
6. Classify acceptance criteria as clear, gap, or not needed. Back clear criteria with identified rules, mark material gaps `data-spec-tbd`, and remove unnecessary criteria.
7. Add an ADR only for a hard-to-reverse, surprising decision with a real tradeoff. For behavior changes, name the deep-module seam, smallest first failing test, and observable evidence; docs-only work skips this, while unsuitable tests require a narrow waiver and alternative proof.

## Implementation graph

Before finishing review, use the selected issue skill to reconcile implementation tickets:

- one independently assignable code outcome per ticket
- exact governing spec anchors and observable completion evidence
- real blockers expressed through tracker relations, never artificial serialization
- every incomplete unblocked ticket on the ready frontier; obsolete tickets removed or closed

Shaping never marks implementation work In Progress or Done.

## Review shaping

Material uncertainties become temporary anchored TBDs.
Ask small dependency-aware batches in Spec Chat, resolve each answer into current spec and issue truth, and reconcile tickets after material changes.
Invoke `spec-chat-review` with `focus=changes&base=<exact-change-request-base>`; it owns publication mechanics and the review loop.
Possession of the unguessable HTTPS review URL is the only authentication; never publish it into the issue or change request.

## Finish

Finish shaping only when:

- no draft, pending, acknowledged, unresolved, or material TBD work remains
- issue, spec, applicable ADRs, stories, acceptance criteria, architecture, and implementation graph agree
- `spec-chat-review` has completed the browser review and invalidated its public capability URL

Review completion is not implementation authorization, acceptance, merge approval, or deployment approval.

## Burden

Add no persistent coordination machinery, duplicate state, tracker abstraction framework, or new event protocol.
Plain files and Git remain the recovery contract; same-session continuation is only an optimization.
