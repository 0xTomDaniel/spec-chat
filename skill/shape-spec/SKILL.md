---
name: spec-chat-shape
description: Shape a feature prompt into a canonical Spec Chat HTML spec and any needed ADRs, committed locally. Also use for any existing-spec review that materially changes behavior or information architecture. Use spec-chat-review alone only for questions and atomic corrections.
---

# Spec Chat Shape

Create durable current truth before deep investigation.

## Invariants

- A spec describes how the system should work. Nothing in it is ever outdated: no current-state audits, before/after, history, status, ticket links, or migration notes; those go in the PR body.
- Before shaping, list every spec and ADR in the target repo (at least their titles) and read the ones that own the behavior you are changing.
- Put behavior in the spec that already owns it. Create a new spec only for a genuinely new part of the system, never a spec named after a change, fix, or lane.
- A change that alters behavior another spec describes updates that spec in the same PR.
- A contradiction between specs means one is out of date: update it in the same PR; a real design choice goes to the human as an open TBD.
- Shaping commits locally only. It never pushes, opens a pull request, or creates or edits an issue or ticket, whatever target instructions say; publishing and tracker work are the caller's.
- Specs and ADRs name no ticket and no pane; the caller's tracker keeps the ticket to spec link.
- The canonical spec owns what the result must do: current stories, behavior, edge cases, interaction contracts, and acceptance.
- An ADR owns one hard-to-reverse decision: its context, the decision, why, alternatives rejected, and consequences, kept short; it holds no policy section and no mechanics.
- A repo-wide rule lives once, in its home spec, as an acceptance criterion whose text says it applies to every feature, so Jev checks it as a project-wide rule. An ADR may name a rule it decided on only by linking that criterion, as in `see peer-seams#acceptance-ci`, and never restates it: the why lives in the ADR, the what in the spec criterion, and the mechanics in code and the change request.
- Implementation mechanics no caller depends on, such as storage tables, retry counts, internal keys, timeouts, and delivery details, live in code and the change request; neither a spec nor an ADR states them.
- Chat and memory never override durable sources; stop on source conflict.
- Use swimlane diagrams for ownership and handoffs, and Sankey diagrams for flows that split or merge, wherever they clarify the spec.
- Accepted spec changes are committed locally before refreshed Git focus or review replies.
- `spec-chat-review` alone owns browser review, server startup and shutdown, spool transactions, transport, wake, recovery, and hosting verification.
- A remote or cross-machine shaping handoff is blocked until `spec-chat-review` has an `assets/review-serve.py` process serving the narrow collection, never the repository root: exposed as box setup chose (`spec-chat-review` `### Box setup`); lanes pass no exposure argument.
- The host performs exact served-resource and `/api/baseline` checks internally before printing the URL. A public URL has no login and is not an authentication boundary. Keep review URLs out of issues, pull requests, and other public durable records. After ordinary edits, the same server and URL remain alive; rerun both checks against the same selected base. Restart only for a root, collection, process, port, runtime, or ownership change, or when the server is dead. A private handoff includes the printed `ssh -L` tunnel; no other probe, VPN, or reviewer-machine setup is part of it.
- The selected port must not be hard-coded. Collision safety, direct service ownership, URL handling, and spec acceptance remain mandatory. Shape owns this blocking condition; `spec-chat-review` owns its mechanics.
- Remote hosting lifecycle is defined by `skill/review-spec/SKILL.md`; spec acceptance remains the spool fact and does not manage host rows.

## Shape

1. Create the work branch using target conventions.
2. For every new spec or material restructure, read [references/authoring.md](references/authoring.md) completely before editing. Existing specs are not grandfathered.
3. Read only target instructions, the spec index, clearly related specs or ADRs, and directly relevant product docs. Update the governing spec or create a minimal spec containing only real behavior; keep only governing links, not a research bibliography.
4. Add `data-spec-contract="shaped-sections-v1"` to every new or materially revised governing article, then include exactly one visible `User stories`, `Acceptance criteria`, and `Modular boundaries` section from [references/authoring.md](references/authoring.md), in that order. Keep the exact Acceptance criteria heading. Scope may come from optional descriptive metadata, anchors, and surrounding source context; it is not an MVP-labelled heading. Every modular boundary names responsibility, caller-facing seam, dependency direction, and observable scope, including self-contained single-module specs. Run the validator as a structural gate, not only a style check.
5. Commit the seed locally, run `python3 scripts/validate-style.py <repository> <spec-html> <exact-review-base>`, and stop on failure.
6. For a remote or cross-machine reviewer, use `spec-chat-review` to start the direct server for the narrow collection and verify the served resource and exact baseline; keep that server and URL for the review lifetime. For a same-machine reviewer, use the supported local transport instead; no host is required. Run the Jev check below, then send the review link at once, before inspecting code, tests, or configuration.
7. Deepen only from relevant code, tests, configuration, and branch state. Resolve discoverable questions before asking the human. Keep the spec always-most-recent and remove stale prose or resolved TBDs.
8. Keep every changed user outcome current in the target-declared story source or governing spec, including the guided-journey declaration defined by [references/authoring.md](references/authoring.md). Do not duplicate canonical story declarations into a tracker or generated Markdown catalog. For cross-module behavior, reconcile stable responsibilities, seams, and dependency direction with the target-declared architecture source while keeping issue-specific detail in the spec.
9. Classify acceptance criteria as clear, gap, or not needed. Back clear criteria with identified rules and write them to the QA-capturable criterion rules in [references/authoring.md](references/authoring.md): user-story level, never a click script: one scenario each, a Then naming the observable outcome in product terms with no artifact path, capture file, command, or test output, a Given naming the user as a role in a fixture-backed situation and never a fixture identifier or fixture user (the target's qa.toml maps roles to fixture users), an outcome no screen shows stated in product terms and read "proven by a unit test: no screen shows it", no QA harness (stubs, capture files, readback tooling, fixture extensions belong to the target's qa.toml or QA docs), no hidden user steps, no concrete values, selectors, or paths (the target's qa.toml hint file carries them), gestures and timings as explicit plain-word steps, states that cannot occur live marked "needs an induced failure: <what fails>", and capitalized unlabeled Given, When, Then, each its own clause in a table cell. Mark material gaps `data-spec-tbd`, and remove unnecessary criteria. A deferred criterion does not satisfy the governing Acceptance criteria section.
10. Add an ADR only for a hard-to-reverse, surprising decision with a real tradeoff. An ADR is immutable once committed to the base branch: a changed decision is a new ADR that names the ADR it supersedes, the old ADR gains only the line `Superseded by ADR NNNN (date)`, and git holds the history; where ADRs carry no number, the line names the new ADR by a link to its file. For behavior changes, name the deep-module seam, smallest first failing test, and observable evidence; docs-only work skips this, while unsuitable tests require a narrow waiver and alternative proof.

## Jev check

After registering with `spec-chat-review`'s lane command and before the link is sent (project-rules#agent-read), read the review link's Jev checks with `spec-chat-review`'s `scripts/jev-read.py '<review-url>' --wait 120`: one line per important mark, warning counts by kind, rules checked, pending count, or `off`. `--anchor <id>` prints one anchor's full detail.

- `off`: send the link at once.
- Key only on each mark's `level`; keep no list of kinds.
- End every `important` mark in exactly one of: a spec fix, a reason line in the spec (for example `Onboarding: not needed, this changes no screen`), or, when it is bigger than a simple edit, an open TBD you write at that clause (Review shaping TBD rule, open value), never a thread; it blocks `Accept spec` until the human settles it and counts as solved for hand-off. A contradiction between specs means one is out of date: update that one in the same PR. If it is a real design choice, open a TBD for the human. If Jev is wrong, don't edit either spec; list it in the hand-off as dismissed with a reason. Fixes take the direction of the reconcile rule under Review shaping. Commit and read again after each fix.
- If a fix raises a new important mark on the same clause, write an open TBD at that clause instead of editing it again.
- Never act on warnings, never hide them, and never edit a spec only to change a Jev answer or its confidence. Marks are advice and never block `Accept spec`.
- Start the hand-off message with `N warnings not acted on: <the read's counts by kind>`, then name the rules checked as home spec and anchor, for visibility only.

## Review shaping

Material uncertainties become temporary anchored TBDs marked `data-spec-tbd`; any value other than `later` is open, blocks spec acceptance, and is highlighted in review. Mark a TBD deliberately left for a later slice `data-spec-tbd="later"`: it stays visible but neither blocks spec acceptance nor is highlighted.
Ask small dependency-aware batches in Spec Chat and resolve each answer into current spec truth.
When resolving a review finding, prefer removing or deferring scope over adding spec text; keep v1 minimal.
For a reconcile request (one Jev conflict flag or a list), treat each flag's kind as a hint only. For each flag, you know what this change intends: if intended, update the other spec; if accidental, fix this one; if unclear or bigger than a simple edit, don't edit: open a thread with the tension and your recommendation.
Finish each batch's behavior, acceptance, and necessary layout changes together, then rerun the validator before its replies.
Invoke `spec-chat-review` with `focus=changes&base=<exact-review-base>`; it owns commit order, review hosting, baseline selection, and the review loop.
For remote or cross-machine review, block the shaping handoff until the review server serves the narrow collection, never the repository root: exposed as box setup chose; lanes pass no exposure argument. The launcher verifies exact resource bytes and the selected exact Git baseline internally and prints the URL only after those checks pass. A public URL has no login and is not an authentication boundary. The handoff contains the active URL (plus the `ssh -L` tunnel when private), resource path, and exact baseline; keep the URL out of issues, pull requests, and other public durable records. After each ordinary edit, rerun exact served-resource and `/api/baseline` checks for the same selected base. Resource rows remain until lane teardown, when `review-host remove` deletes them.

## Finish

Finish shaping only when:

- no draft, pending, acknowledged, unresolved, or open TBD work remains; `data-spec-tbd="later"` does not block
- spec, applicable ADRs, stories, acceptance criteria, and architecture agree

The processed spec acceptance hand-off is human acceptance of the reviewed canonical spec and permits implementation dispatch under that spec. It is not implementation PR acceptance, merge approval, preproduction promotion, or live traffic approval. Those gates remain explicit.

## Burden

Add no persistent coordination machinery, duplicate state, tracker abstraction framework, or new event protocol.
Plain files and Git remain the recovery contract; same-session continuation is only an optimization.
