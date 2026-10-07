---
name: repair-specs
description: Bring a repo's existing specs and ADRs to the current shaping contract. Removes non-target text, moves behavior to owning specs, resolves contradictions, reduces ADRs to the ADR model, and rewrites acceptance criteria to the shaping contract's criterion rules and Jev project rules. Runnable any time or as onboarding catch-up.
---

# Repair specs

One skill that brings every spec and ADR in a repo to the current [shaping contract](../shape-spec/SKILL.md).
Reads only Spec Chat's `onboarding.toml` and the repo's specs and ADRs; no peer status file or peer name.

## Inputs

The caller names the repo root and optionally a project slug for onboarding context.

When invoked as onboarding catch-up, read `$XDG_STATE_HOME/spec-chat/onboarding.toml` for the project's table: warm-up state, rules found, and N specs to reconcile.
When N is zero or the table is absent, there is nothing to reconcile; stop.

## Repair types

Run in this fixed order: clean, home, contradict, ADR, criteria.
Each type reads every spec and ADR as the previous type left it.

### 1. Clean

Remove non-target text from every spec:
- Sections anchored or named as audit, history, before/after, or status.
- Source-issue links and ticket links in headers or body.
- Any current-state prose, migration notes, or changelog entries.
- QA harness setup: provider stubs, capture files, readback tooling, and fixture extensions.

Preserve every behavior clause, acceptance criterion, and modular boundary.
List each piece of removed QA harness setup in the repair commit message for the target's qa.toml or QA docs.

### 2. Home

Move behavior to its owning spec:
- A behavior clause in spec A that spec B already owns (per B's title, boundaries, and stories) moves to spec B and is removed from spec A.
- A spec named after a change, fix, or lane whose behavior belongs in an existing owning spec: merge its behavior into the owning spec and delete the change-named file.

### 3. Contradict

Resolve contradictions between specs:
- When two specs' clauses contradict and one side is out of date, update the out-of-date side.
- When the contradiction is a real design choice, write an open TBD (`data-spec-tbd`) at the clause instead.

### 4. ADR

Bring every ADR to the [ADR model](../../docs/specs/prompt-first-shaping.spec.html#source-adr): context, decision, why, alternatives rejected, and consequences only.
An ADR already in that model is left unchanged.
For an ADR with a policy or rules section, a restated rule, mechanics, or amendments (superseded blocks, dated amendments, a decision log):
- Move each rule into its home spec as an acceptance criterion that says it applies to every feature, unless a spec already states it.
- Write one new short ADR holding the current decision and why, linking those criteria without restating them and naming every ADR it supersedes.
- Add only the line `Superseded by ADR NNNN (date)` to each superseded ADR; where ADRs carry no number, the line links the new ADR's file.
- List removed mechanics in the repair commit message for the code and the change request.

### 5. Criteria

Rewrite acceptance criteria to the shaping contract's [criterion rules](../../docs/specs/prompt-first-shaping.spec.html#complete-criterion-capturable), then to any missed Jev project rule.
Criterion-rule repair runs whether Jev is on or off.

Criterion rules:
- For each criterion that breaks a criterion rule, rewrite it to meet every rule.
- A Given or When that joins alternatives splits into one criterion per alternative; the first keeps the original anchor, each new one gets a new stable anchor, and each meets every criterion rule.
- When a criterion's When skips a step to its Then that the specs do not state, write an open TBD (`data-spec-tbd`) at that criterion naming the missing step; never invent a step.
- When the visible end state or the providing fixture cannot be read from the specs, write an open TBD (`data-spec-tbd`) at that criterion naming what is missing; never invent an end state or a fixture.
- When a Given does not say who the user is and the specs do not say it either, write an open TBD (`data-spec-tbd`) at that criterion asking who the user is; never invent a user.
- When a criterion holds a concrete value, selector, or path, rewrite it in user terms (`types "Q3 scripts" into Folder name` becomes `types a folder name`) and list each value moved out in the repair commit message as a value for the target's qa.toml hint file.
- When a Given names a fixture identifier or fixture user and the specs say which role it is, rewrite the Given to that role (`Given tf-admin is signed in` becomes `Given an internal admin acting in a client account`) and list the fixture user in the repair commit message as that role's fixture user for the target's qa.toml.
- When a Then names an artifact path, capture file, command, or test output, rewrite it to the outcome in product terms; when no screen shows the outcome, the Then reads `proven by a unit test: no screen shows it` (`posthog-captures.jsonl holds one funnel event` becomes `the funnel step is recorded once, proven by a unit test: no screen shows it`). List each artifact path moved out in the repair commit message for the target's qa.toml.

Jev project rules, only when Jev is on:
- Read the project's Jev rules: project-wide rules are acceptance criteria from other specs whose scope is `every feature`, as [project-rules](../../docs/specs/project-rules.spec.html) defines.
- For each spec whose criteria miss a rule, rewrite to satisfy the rule using the rule's own wording.
- The skill names no project's rules and applies whatever Jev found; no rule wording or format is hardcoded.

When Jev is off, skip only the Jev project-rule repair; clean, home, contradict, ADR, and criterion-rule repairs still run.

## Editing contract

Before editing any spec, load [shape-spec authoring](../shape-spec/references/authoring.md) and follow it completely.

Every edit follows the authoring contract:
- One sentence per line in prose.
- Stable `data-anchor` on every block; never remove or rename an existing anchor.
- Pretty-printed semantic-island JSON beside its render target.
- Preserve existing visual artifacts, section order, and layout unless the repair requires a structural change.

## One retry rule

If a repair edit raises a new contradiction or rule miss on the same clause, write an open TBD (`data-spec-tbd`) at that clause instead of editing again.
The TBD blocks spec acceptance until the human settles it.
Same rule as the shaping agent's [fix-raises-new-mark](../../docs/specs/project-rules.spec.html#agent-once-stop).

## Output

When repairs complete:

1. Commit each changed spec and ADR on the current branch.
2. Register each changed spec with the review server so the reviewer sees changes with Git focus.

Commits are local only; the skill never pushes, opens a pull request, or creates an issue.

## Onboarding catch-up

When `onboarding.toml` shows specs to reconcile for a project:

1. Run all five repair types on that project's specs.
2. Update the project's table in `onboarding.toml` when done (reconciled count, timestamp).

## Burden

No persistent coordination machinery, duplicate state, or new event protocol.
Plain files and Git remain the recovery contract.
