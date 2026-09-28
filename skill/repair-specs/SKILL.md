---
name: repair-specs
description: Bring a repo's existing specs to the current shaping contract. Removes non-target text, moves behavior to owning specs, resolves contradictions, and rewrites acceptance criteria to satisfy Jev project rules. Runnable any time or as onboarding catch-up.
---

# Repair specs

One skill that brings every spec in a repo to the current [shaping contract](../shape-spec/SKILL.md).
Reads only Spec Chat's `onboarding.toml` and the repo's specs; no peer status file or peer name.

## Inputs

The caller names the repo root and optionally a project slug for onboarding context.

When invoked as onboarding catch-up, read `$XDG_STATE_HOME/spec-chat/onboarding.toml` for the project's table: warm-up state, rules found, and N specs to reconcile.
When N is zero or the table is absent, there is nothing to reconcile; stop.

## Repair types

Run in this fixed order: clean, home, contradict, criteria.
Each type reads every spec as the previous type left it.

### 1. Clean

Remove non-target text from every spec:
- Sections anchored or named as audit, history, before/after, or status.
- Source-issue links and ticket links in headers or body.
- Any current-state prose, migration notes, or changelog entries.

Preserve every behavior clause, acceptance criterion, and modular boundary.

### 2. Home

Move behavior to its owning spec:
- A behavior clause in spec A that spec B already owns (per B's title, boundaries, and stories) moves to spec B and is removed from spec A.
- A spec named after a change, fix, or lane whose behavior belongs in an existing owning spec: merge its behavior into the owning spec and delete the change-named file.

### 3. Contradict

Resolve contradictions between specs:
- When two specs' clauses contradict and one side is out of date, update the out-of-date side.
- When the contradiction is a real design choice, write an open TBD (`data-spec-tbd`) at the clause instead.

### 4. Criteria

Rewrite acceptance criteria to satisfy Jev project rules.
Skip this type entirely when Jev is off.

- Read the project's Jev rules: project-wide rules are acceptance criteria from other specs whose scope is `every feature`, as [project-rules](../../docs/specs/project-rules.spec.html) defines.
- For each spec whose criteria miss a rule, rewrite to satisfy the rule using the rule's own wording.
- The skill names no project's rules and applies whatever Jev found; no rule wording or format is hardcoded.

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

1. Commit each changed spec on the current branch.
2. Register each changed spec with the review server so the reviewer sees changes with Git focus.

Commits are local only; the skill never pushes, opens a pull request, or creates an issue.

## Onboarding catch-up

When `onboarding.toml` shows specs to reconcile for a project:

1. Run all four repair types on that project's specs.
2. Update the project's table in `onboarding.toml` when done (reconciled count, timestamp).

## Burden

No persistent coordination machinery, duplicate state, or new event protocol.
Plain files and Git remain the recovery contract.
