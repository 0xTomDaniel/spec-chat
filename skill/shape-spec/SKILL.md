---
name: spec-chat-shape
description: Shape a feature prompt into a current issue, canonical Spec Chat HTML spec, any needed ADRs, and dependency-linked implementation tickets. Also use for any existing-spec review that materially changes behavior or information architecture. Use spec-chat-review alone only for questions and atomic corrections.
---

# Spec Chat Shape

Create durable current truth before deep investigation.

## Invariants

- The repository-selected issue skill, named by target instructions, owns tracker operations; Spec Chat contains no tracker dependency.
- The issue owns current intent, outcomes, criteria, non-goals, dependencies, and governing links.
- The canonical spec owns what the result must do: current stories, behavior, edge cases, interaction contracts, and acceptance.
- An ADR owns a hard-to-reverse decision, its tradeoff, and the policy and mechanics that follow from it.
- Implementation tickets own independently assignable outcomes, blocking relations, and delivery steps.
- Chat and memory never override durable sources; stop on source conflict.
- Use swimlane diagrams for ownership and handoffs, and Sankey diagrams for flows that split or merge, wherever they clarify the spec.
- Accepted spec changes are committed and pushed before refreshed Git focus or review replies.
- `spec-chat-review` alone owns browser review, server startup and shutdown, spool transactions, transport, wake, recovery, and hosting verification.
- A remote or cross-machine shaping handoff is blocked until `spec-chat-review` has a direct public `assets/review-serve.py` process serving the narrow collection, never the repository root, on a free approved ingress port discovered and probed by the host.
- The host performs exact served-resource and `/api/baseline` checks internally before printing the public URL. The public URL is not a secret in any security sense. It is not an authentication boundary. Keep it out of Linear, pull requests, and other public durable records. After ordinary edits, the same server and URL remain alive; rerun both checks against the same selected base. Restart only for a root, collection, process, port, runtime, or ownership change, or when the server is dead. No second probe, tunnel, VPN, laptop setup, or reviewer-machine setup is part of the handoff.
- The selected port must not be hard-coded. Collision safety, direct service ownership, public URL handling, and spec acceptance remain mandatory. Shape owns this blocking condition; `spec-chat-review` owns its mechanics.
- Remote hosting lifecycle is defined by `skill/review-spec/SKILL.md`; spec acceptance remains the spool fact and does not manage host rows.

## Shape

1. Resolve the repository-selected issue skill, create a concise placeholder issue immediately, and create the work branch using target conventions. Missing issue access is a visible blocker.
2. Read only target instructions, the spec index, clearly related specs or ADRs, and directly relevant product docs. Update the governing spec or create a minimal spec containing only real behavior; append the issue to its source-issues list and keep only governing links, not a research bibliography. Commit, push, and open one evolving draft change request. This durable seed is a checkpoint; browser review readiness still requires the authoring gate and verified review surface.
3. Deepen only from relevant code, tests, configuration, and change-request state. Resolve discoverable questions before asking the human. Keep the issue always-most-recent and remove stale prose or resolved TBDs.
4. Keep every changed user outcome current in the target-declared story source or governing spec, including the guided-journey declaration defined by [references/authoring.md](references/authoring.md). Do not duplicate canonical story declarations into the issue or generated Markdown catalog. For cross-module behavior, reconcile stable responsibilities, seams, and dependency direction with the target-declared architecture source while keeping issue-specific detail in the spec.
5. For every new spec or material restructure, read [references/authoring.md](references/authoring.md) completely before editing. Existing specs are not grandfathered. Before presenting a new or materially revised draft for browser review, run `python3 scripts/validate-style.py <repository> <spec-html> <exact-change-request-base>` and the authoring browser gate; stop on failure.
6. Add `data-spec-contract="shaped-sections-v1"` to every new or materially revised governing article, then include exactly one visible `User stories`, `Acceptance criteria`, and `Modular boundaries` section from [references/authoring.md](references/authoring.md). Keep the exact Acceptance criteria heading. Scope may come from optional descriptive metadata, anchors, and surrounding source context; it is not an MVP-labelled heading. Every modular boundary names responsibility, caller-facing seam, dependency direction, and observable scope, including self-contained single-module specs. Run the validator as a structural gate, not only a style check.
7. Classify acceptance criteria as clear, gap, or not needed. Back clear criteria with identified rules, mark material gaps `data-spec-tbd`, and remove unnecessary criteria. A deferred criterion does not satisfy the governing Acceptance criteria section.
8. Add an ADR only for a hard-to-reverse, surprising decision with a real tradeoff. For behavior changes, name the deep-module seam, smallest first failing test, and observable evidence; docs-only work skips this, while unsuitable tests require a narrow waiver and alternative proof.
9. Before browser evidence or the first remote or cross-machine review handoff, use `spec-chat-review` to start the direct server for the narrow collection and verify the served resource and exact baseline. Keep that server and URL for the review lifetime. For a same-machine reviewer, use the supported local transport instead; no public server is required.

## Implementation graph

Before finishing review, use the selected issue skill to reconcile implementation tickets:

- one independently assignable code outcome per ticket
- exact governing spec anchors and observable completion evidence
- real blockers expressed through tracker relations, never artificial serialization
- every incomplete unblocked ticket on the ready frontier; obsolete tickets removed or closed

Shaping never marks implementation work In Progress or Done.

## Review shaping

Material uncertainties become temporary anchored TBDs marked `data-spec-tbd`; any value other than `later` is open, blocks spec acceptance, and is highlighted in review. Mark a TBD deliberately left for a later slice `data-spec-tbd="later"`: it stays visible but neither blocks spec acceptance nor is highlighted.
Ask small dependency-aware batches in Spec Chat, resolve each answer into current spec and issue truth, and reconcile tickets after material changes.
When resolving a review finding, prefer removing or deferring scope over adding spec text; keep v1 minimal.
Finish each batch's behavior, acceptance, and necessary layout changes together before final browser inspection and ticket reconciliation; apply the authoring reference's proportional recheck rule to later corrections.
Invoke `spec-chat-review` with `focus=changes&base=<exact-review-base>`; it owns publication mechanics, review hosting, baseline selection, and the review loop.
An operator-selected previously reviewed snapshot may differ from the change request's base; keep the latter for stylesheet provenance validation.
For remote or cross-machine review, block the shaping handoff until the direct public review server serves the narrow collection, never the repository root, on a free approved ingress port discovered and probed by the host. The launcher binds the selected port, verifies exact resource bytes and the selected exact Git baseline internally, and prints the public URL only after those checks pass. The public URL is not a secret in any security sense. It is not an authentication boundary. The handoff contains the active public URL, resource path, and exact baseline; keep the URL out of Linear, pull requests, and other public durable records. After each ordinary edit, rerun exact served-resource and `/api/baseline` checks for the same selected base. Resource rows remain until lane teardown, when `review-host remove` deletes them. No second probe, tunnel, VPN, laptop setup, or reviewer-machine setup is required.

## Finish

Finish shaping only when:

- no draft, pending, acknowledged, unresolved, or open TBD work remains; `data-spec-tbd="later"` does not block
- issue, spec, applicable ADRs, stories, acceptance criteria, architecture, and implementation graph agree

The processed spec acceptance hand-off is human acceptance of the reviewed canonical spec and permits implementation dispatch under that spec. It is not implementation PR acceptance, merge approval, preproduction promotion, or live traffic approval. Those gates remain explicit.

## Burden

Add no persistent coordination machinery, duplicate state, tracker abstraction framework, or new event protocol.
Plain files and Git remain the recovery contract; same-session continuation is only an optimization.
