---
name: spec-chat-shape
description: Shape a feature prompt into a canonical Spec Chat HTML spec and any needed ADRs, committed locally and linked to the issue the caller names. Also use for any existing-spec review that materially changes behavior or information architecture. Use spec-chat-review alone only for questions and atomic corrections.
---

# Spec Chat Shape

Create durable current truth before deep investigation.

## Invariants

- Shaping commits locally only. It never pushes, opens a pull request, or creates or edits an issue or ticket, whatever target instructions say; publishing and tracker work are the caller's.
- When the caller names an issue, the spec links it; otherwise the spec links none and shaping proceeds unchanged.
- The canonical spec owns what the result must do: current stories, behavior, edge cases, interaction contracts, and acceptance.
- An ADR owns a hard-to-reverse decision, its tradeoff, and the policy and mechanics that follow from it.
- Chat and memory never override durable sources; stop on source conflict.
- Use swimlane diagrams for ownership and handoffs, and Sankey diagrams for flows that split or merge, wherever they clarify the spec.
- Accepted spec changes are committed locally before refreshed Git focus or review replies.
- `spec-chat-review` alone owns browser review, server startup and shutdown, spool transactions, transport, wake, recovery, and hosting verification.
- A remote or cross-machine shaping handoff is blocked until `spec-chat-review` has an `assets/review-serve.py` process serving the narrow collection, never the repository root: private on loopback by default, or public on a free approved ingress port only when the developer asks for `--public <host>`.
- The host performs exact served-resource and `/api/baseline` checks internally before printing the URL. A public URL has no login and is not an authentication boundary. Keep review URLs out of issues, pull requests, and other public durable records. After ordinary edits, the same server and URL remain alive; rerun both checks against the same selected base. Restart only for a root, collection, process, port, runtime, or ownership change, or when the server is dead. A private handoff includes the printed `ssh -L` tunnel; no other probe, VPN, or reviewer-machine setup is part of it.
- The selected port must not be hard-coded. Collision safety, direct service ownership, URL handling, and spec acceptance remain mandatory. Shape owns this blocking condition; `spec-chat-review` owns its mechanics.
- Remote hosting lifecycle is defined by `skill/review-spec/SKILL.md`; spec acceptance remains the spool fact and does not manage host rows.

## Shape

1. Create the work branch using target conventions.
2. For every new spec or material restructure, read [references/authoring.md](references/authoring.md) completely before editing. Existing specs are not grandfathered.
3. Read only target instructions, the spec index, clearly related specs or ADRs, and directly relevant product docs. Update the governing spec or create a minimal spec containing only real behavior; append the caller-named issue, if any, to its source-issues list and keep only governing links, not a research bibliography.
4. Add `data-spec-contract="shaped-sections-v1"` to every new or materially revised governing article, then include exactly one visible `User stories`, `Acceptance criteria`, and `Modular boundaries` section from [references/authoring.md](references/authoring.md), in that order. Keep the exact Acceptance criteria heading. Scope may come from optional descriptive metadata, anchors, and surrounding source context; it is not an MVP-labelled heading. Every modular boundary names responsibility, caller-facing seam, dependency direction, and observable scope, including self-contained single-module specs. Run the validator as a structural gate, not only a style check.
5. Commit the seed locally, run `python3 scripts/validate-style.py <repository> <spec-html> <exact-review-base>`, and stop on failure.
6. For a remote or cross-machine reviewer, use `spec-chat-review` to start the direct server for the narrow collection and verify the served resource and exact baseline; keep that server and URL for the review lifetime. For a same-machine reviewer, use the supported local transport instead; no host is required. Send the review link at once, before inspecting code, tests, or configuration.
7. Deepen only from relevant code, tests, configuration, and branch state. Resolve discoverable questions before asking the human. Keep the spec always-most-recent and remove stale prose or resolved TBDs.
8. Keep every changed user outcome current in the target-declared story source or governing spec, including the guided-journey declaration defined by [references/authoring.md](references/authoring.md). Do not duplicate canonical story declarations into the issue or generated Markdown catalog. For cross-module behavior, reconcile stable responsibilities, seams, and dependency direction with the target-declared architecture source while keeping issue-specific detail in the spec.
9. Classify acceptance criteria as clear, gap, or not needed. Back clear criteria with identified rules, mark material gaps `data-spec-tbd`, and remove unnecessary criteria. A deferred criterion does not satisfy the governing Acceptance criteria section.
10. Add an ADR only for a hard-to-reverse, surprising decision with a real tradeoff. For behavior changes, name the deep-module seam, smallest first failing test, and observable evidence; docs-only work skips this, while unsuitable tests require a narrow waiver and alternative proof.

## Review shaping

Material uncertainties become temporary anchored TBDs marked `data-spec-tbd`; any value other than `later` is open, blocks spec acceptance, and is highlighted in review. Mark a TBD deliberately left for a later slice `data-spec-tbd="later"`: it stays visible but neither blocks spec acceptance nor is highlighted.
Ask small dependency-aware batches in Spec Chat and resolve each answer into current spec truth.
When resolving a review finding, prefer removing or deferring scope over adding spec text; keep v1 minimal.
Finish each batch's behavior, acceptance, and necessary layout changes together, then rerun the validator before its replies.
Invoke `spec-chat-review` with `focus=changes&base=<exact-review-base>`; it owns commit order, review hosting, baseline selection, and the review loop.
For remote or cross-machine review, block the shaping handoff until the review server serves the narrow collection, never the repository root: private on loopback by default, public on a free approved ingress port only after the developer asks for `--public <host>`. The launcher verifies exact resource bytes and the selected exact Git baseline internally and prints the URL only after those checks pass. A public URL has no login and is not an authentication boundary. The handoff contains the active URL (plus the `ssh -L` tunnel when private), resource path, and exact baseline; keep the URL out of issues, pull requests, and other public durable records. After each ordinary edit, rerun exact served-resource and `/api/baseline` checks for the same selected base. Resource rows remain until lane teardown, when `review-host remove` deletes them.

## Finish

Finish shaping only when:

- no draft, pending, acknowledged, unresolved, or open TBD work remains; `data-spec-tbd="later"` does not block
- spec, applicable ADRs, stories, acceptance criteria, and architecture agree

The processed spec acceptance hand-off is human acceptance of the reviewed canonical spec and permits implementation dispatch under that spec. It is not implementation PR acceptance, merge approval, preproduction promotion, or live traffic approval. Those gates remain explicit.

## Burden

Add no persistent coordination machinery, duplicate state, tracker abstraction framework, or new event protocol.
Plain files and Git remain the recovery contract; same-session continuation is only an optimization.
