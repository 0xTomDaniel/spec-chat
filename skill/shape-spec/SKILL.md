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
- `spec-chat-review` alone owns browser review, server startup and shutdown, spool transactions, transport, wake, recovery, and hosting verification.
- A remote or cross-machine shaping handoff is blocked until `spec-chat-review` has a direct public `assets/review-serve.py` process serving the narrow spec collection, never the repository root, with evidence that the exact spec URL returns the exact current bytes and `/api/baseline` resolves the selected exact Git base.
- The shaping handoff is also blocked until approved ingress ports are discovered or explicitly configured, a free approved port is selected, and a non-loopback probe verifies HTTP 200 plus exact spec and `/api/baseline` responses. The selected port must not be hard-coded as a universal assumption.
- A same-machine reviewer may use the supported local transport without a public server. Shape owns this blocking handoff condition; `spec-chat-review` owns its mechanics.

## Shape

1. Resolve the repository-selected issue skill, create a concise placeholder issue immediately, and create the work branch using target conventions. Missing issue access is a visible blocker.
2. Read only target instructions, the spec index, clearly related specs or ADRs, and directly relevant product docs. Update the governing spec or create a minimal spec containing only real behavior; append the issue to its source-issues list and keep only governing links, not a research bibliography. Commit, push, and open one evolving draft change request. This durable seed is a checkpoint; browser review readiness still requires the authoring gate and verified review surface.
3. Deepen only from relevant code, tests, configuration, and change-request state. Resolve discoverable questions before asking the human. Keep the issue always-most-recent and remove stale prose or resolved TBDs.
4. Keep every changed user outcome current in the target-declared story source or governing spec, including the guided-journey declaration defined by [references/authoring.md](references/authoring.md). Do not duplicate canonical story declarations into the issue or generated Markdown catalog. For cross-module behavior, reconcile stable responsibilities, seams, and dependency direction with the target-declared architecture source while keeping issue-specific detail in the spec.
5. For every new spec or material restructure, read [references/authoring.md](references/authoring.md) completely before editing. Existing specs are not grandfathered. Before presenting a new or materially revised draft for browser review, run `python3 scripts/validate-style.py <repository> <spec-html> <exact-change-request-base>` and the authoring browser gate; stop on failure.
6. Add `data-spec-contract="shaped-sections-v1"` to every new or materially revised governing article, then include exactly one visible `User stories`, `Acceptance criteria`, and `Modular boundaries` section from [references/authoring.md](references/authoring.md). Keep the exact Acceptance criteria heading. Scope may come from optional descriptive metadata, anchors, and surrounding source context; it is not an MVP-labelled heading. Every modular boundary names responsibility, caller-facing seam, dependency direction, and observable scope, including self-contained single-module specs. Run the validator as a structural gate, not only a style check.
7. Classify acceptance criteria as clear, gap, or not needed. Back clear criteria with identified rules, mark material gaps `data-spec-tbd`, and remove unnecessary criteria. A deferred criterion does not satisfy the governing Acceptance criteria section.
8. Add an ADR only for a hard-to-reverse, surprising decision with a real tradeoff. For behavior changes, name the deep-module seam, smallest first failing test, and observable evidence; docs-only work skips this, while unsuitable tests require a narrow waiver and alternative proof.
9. Before browser evidence or the first remote or cross-machine review handoff, use `spec-chat-review` to start the direct public server for the narrow collection and verify the served spec and exact baseline. Keep that server and URL for the review lifetime. For a same-machine reviewer, use the supported local transport instead; no public server is required.

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
Finish each batch's behavior, acceptance, and necessary layout changes together before final browser inspection and ticket reconciliation; apply the authoring reference's proportional recheck rule to later corrections.
Invoke `spec-chat-review` with `focus=changes&base=<exact-review-base>`; it owns publication mechanics, review hosting, baseline selection, and the review loop.
An operator-selected previously reviewed snapshot may differ from the change request's base; keep the latter for stylesheet provenance validation.
For remote or cross-machine review, block the shaping handoff until the direct public review server serves the narrow collection, never the repository root, and verification proves that the exact spec URL returns the exact current bytes and `/api/baseline` resolves the selected exact Git base. Use approved-port discovery or explicit host configuration, bind to a free approved port, and capture the printed URL. A random port remains valid only when the selected port is externally reachable. The handoff contains exactly the secret review URL, spec path, exact baseline, and local plus non-loopback verification results. The URL itself is the secret; never publish it into the issue or change request. Keep the same server, URL, and checker alive through review, including empty spool, timeout, and manual-resume states; a file path, loopback-only proof, or stale URL is not a substitute. Refuse URL delivery and checker parking when the external probe cannot reach the exact spec and `/api/baseline` with HTTP 200.
Failure checks are explicit: missing or non-observable hosting, unavailable approved ports, a fixed-port assumption, wrong collection root, failed external reachability, mismatched spec bytes, or an unresolved or substituted baseline blocks handoff and means shaping cannot report ready. `spec-chat-review` performs startup, approved-port discovery, URL capture, local and external byte/baseline verification, watcher/checker, wake/recovery, and shutdown; shape only enforces this gate.

## Finish

Finish shaping only when:

- no draft, pending, acknowledged, unresolved, or material TBD work remains
- issue, spec, applicable ADRs, stories, acceptance criteria, architecture, and implementation graph agree
- `spec-chat-review` has completed the browser review and stopped its public review server when remote review was used

Review completion is not implementation authorization, acceptance, merge approval, or deployment approval.

## Burden

Add no persistent coordination machinery, duplicate state, tracker abstraction framework, or new event protocol.
Plain files and Git remain the recovery contract; same-session continuation is only an optimization.
