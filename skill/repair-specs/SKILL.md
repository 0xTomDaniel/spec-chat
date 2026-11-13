---
name: repair-specs
description: Bring a repo's existing specs, mockups, and ADRs to the current shaping contract, whose rules it links at their source and never restates. Removes non-target text, moves behavior to owning specs, resolves contradictions, reduces ADRs to the ADR model, brings every acceptance criterion, spec page, and mockup to the contract's rules (mockups to production fidelity, writing no product code), validates each spec whole, proposes splitting a spec that holds separate domains, then checks each spec against its code for drift. Its catch-up runs the Jev project rule checks over every spec in batch. Runnable any time or as onboarding catch-up.
---

# Repair specs

One skill that brings every spec, mockup, and ADR in a repo to the current [shaping contract](../shape-spec/SKILL.md).
Reads the repo's specs and ADRs with their mockup sets, Jev's checks through the agent read (`spec-chat-review`'s `scripts/jev-read.py`), for the split check the repo's Git history, and for mockup repair and the drift check the repo's code and Git history; reads no onboarding status or peer status file and names no peer.

## Inputs

The caller names the repo root and optionally catch-up.
A repair run is all seven repair types, any rule no type covers, validation, the split check, and the drift check; a catch-up run is the [catch-up](#catch-up) steps only.

## Rule source

Repair's rules are the shaping contract's: the [shaping skill](../shape-spec/SKILL.md), its [authoring reference](../shape-spec/references/authoring.md), and each spec they link for a rule.
Read them completely before the first pass; every edit follows them.
Each pass below links the rules it repairs to at their source; this skill restates none, so a rule the contract gains, changes, or drops is the rule this run applies.
Where the contract applies a rule only to what a change adds or edits, repair applies it to every spec, ADR, criterion, figure, and mockup: none is grandfathered.

This skill holds only how repair brings existing text to those rules:
- Keep every anchor: never remove or rename one; a split the human chose moves an anchor, unchanged, to its new spec.
- Keep existing visual artifacts, section order, and layout unless the repair requires a structural change.
- Keep every proof clause a criterion carries word for word ([rule](../shape-spec/references/authoring.md#required-shaped-spec-sections), [prompt-first shaping](../../docs/specs/prompt-first-shaping.spec.html#complete-criterion-proof-clause)), and leave word for word each criterion and mockup that already meets every rule.
- Never invent a fact a rule needs: when the specs do not state it, such as a criterion's visible end state, a step its When skips, its providing fixture, who its user is, what the screen shows after a dismissal, or which thing a position names, write an open TBD (`data-spec-tbd`) at that clause naming what is missing.
- List in the repair commit message what a pass moves out of a spec, for where it now lives:

| Moved out | Listed for |
| --- | --- |
| A concrete value, selector, or path | The target's qa.toml hint file |
| A fixture user | The target's qa.toml, as that role's fixture user |
| An artifact path, capture file, command, or test output | The target's qa.toml |
| QA harness setup: stubs, capture files, readback tooling, fixture extensions | The target's qa.toml or QA docs |
| An ADR's mechanics | The code and the change request |

## Repair types

Run in this fixed order: clean, home, contradict, ADR, criteria, presentation, mockups.
Each type reads every spec, mockup, and ADR as the previous type left it, so presentation and mockups draw from final text.

### 1. Clean

Rules: the target-state and no-ticket [invariants](../shape-spec/SKILL.md#invariants), and the [QA harness rule](../shape-spec/references/authoring.md#required-shaped-spec-sections) ([prompt-first shaping](../../docs/specs/prompt-first-shaping.spec.html#complete-criterion-no-harness)).
Remove every piece of text they keep out of a spec; keep every behavior clause, acceptance criterion, and modular boundary.

### 2. Home

Rules: the owning-spec [invariant](../shape-spec/SKILL.md#invariants).
- A clause another spec owns, per that spec's title, boundaries, and stories, moves there and leaves the first spec.
- A spec named after a change, fix, or lane: merge its behavior into the owning spec, then delete its file.

### 3. Contradict

Rules: the contradiction [invariant](../shape-spec/SKILL.md#invariants), in one spec or across two, a clause against its own spec's non-goals included.
Resolve each contradiction as it states.

### 4. ADR

Rules: the [ADR model](../../docs/specs/prompt-first-shaping.spec.html#source-adr), the ADR and repo-wide rule [invariants](../shape-spec/SKILL.md#invariants), and the supersede rule ([shape step 10](../shape-spec/SKILL.md#shape)).
- An ADR that meets them is unchanged.
- An ADR that does not: move each rule it holds to its home spec as the repo-wide rule invariant states, unless a spec already states it; then write one new ADR in the model holding the current decision and naming every ADR it supersedes, as the supersede rule states.
- Each superseded ADR keeps every word and gains only the supersede rule's line.

### 5. Criteria

Rules: the [criterion rules](../shape-spec/references/authoring.md#required-shaped-spec-sections) ([prompt-first shaping](../../docs/specs/prompt-first-shaping.spec.html#complete-criterion-capturable)), on every criterion, whether Jev is on or off.
- Rewrite each criterion that breaks a rule to meet every rule.
- When a rule splits a criterion, the first keeps the original anchor, each new one gets a new stable anchor, and each meets every rule.

Project-rule misses are not a criteria repair: [catch-up](#catch-up) proposes them for the human to settle.
With Jev off, clean, home, contradict, ADR, criterion-rule, presentation, and mockup repairs still run; only the Jev project-rule checks of catch-up are skipped.

### 6. Presentation

Rules: the [visual quality contract](../../docs/specs/prompt-first-shaping.spec.html#visual-quality), and authoring's [information plan](../shape-spec/references/authoring.md#information-plan), [visual system](../shape-spec/references/authoring.md#visual-system), [shared markup](../shape-spec/references/authoring.md#shared-markup), and [runtime](../shape-spec/references/authoring.md#runtime).
- Change how a spec page reads, never what it says: every behavior sentence, acceptance criterion, and boundary keeps its wording.
- The text governs any figure that disagrees with it: change the figure, never the text.
- Never turn relational prose into new figures; that changes behavior text and belongs to shaping.
- Specs only: Markdown ADRs have no figures or page styling to repair.
- A runtime lacking a capability the bundled runtime requires is replaced only through the review [preflight](../review-spec/SKILL.md) (`scripts/preflight.py`), which commits the migration alone, touching only the shared runtime assets.

The review chrome is the runtime's, owned by [spec page design](../../docs/specs/spec-page-design.spec.html), and reaches a spec only through preflight.

### 7. Mockups

Rules: the mockup rule of [shape step 4](../shape-spec/SKILL.md#shape), authoring's [mockups](../shape-spec/references/authoring.md#mockups) and every subsection under it, and [production fidelity](../../docs/specs/mockups.spec.html#fidelity), on every screen a spec states.
Repairs a screen with no mockup in its spec's mockup set and a mockup that breaks a rule.
Read the product's code for the screen's route; never edit it.
- A screen the product ships: its mockup is a [prototype mockup](../shape-spec/references/authoring.md#a-running-prototype-first) naming the shipped screen's route, with no code to write, its app's route or a harness page the product already serves; its regions and steps illustrate each spec anchor the old mockup illustrated.
- Chrome the project does not own: a [drawn mockup](../shape-spec/references/authoring.md#a-drawn-mockup-only-for-chrome-the-project-does-not-own), redrawn from that chrome's design source, with its fidelity note and the product whose chrome it draws.
- A screen the project owns that its product does not serve yet: leave its mockup as committed, since the change that builds the screen shapes its prototype; the whole-spec check only [warns](../../docs/specs/mockups.spec.html#checks-warn) on it.
- Keep the mockup's file and each anchor of a part the screen still shows.
- Write no product code, a prototype included.

### Any rule no type covers

After mockups, a rule the shaping contract holds that no repair type above covers runs as a pass of its own, keeping what the [rule source](#rule-source) keeps.

## Validate

After the repairs, commit the repaired specs, mockups, and ADRs, then validate each spec with the shaping contract's structural validator in its whole-spec check, against the commit the run started from:

```sh
python3 <spec-chat-shape>/scripts/validate-style.py --whole-spec <repo-root> <spec.html> <run-base>
```

It runs every check it makes on what a change adds or edits on every criterion and mockup of the spec, and fails a proof clause dropped since the run's base.
On failure, fix what it names under the repair type that owns it, commit, and validate again; answer each warning, on any criterion, with one of the two answers [shape step 5](../shape-spec/SKILL.md#shape) gives: split the criterion per the one-screen rule, or write why its Then reads from one screen as `data-one-screen="<reason>"`.

## Split check

After the seven repairs pass [validation](#validate) and before the drift check, check each spec for separate domains.
Shaping runs the same check on the spec a change lands in ([prompt-first shaping](../../docs/specs/prompt-first-shaping.spec.html#flow-split)).

| Signal in a spec | Proposal |
| --- | --- |
| Its stories fall into groups that no criterion joins, each group served by sections of its own | Split it, one new spec per group |
| Its Git history shows unrelated changes repeatedly editing it at the same time, each only in sections the other never touches | Split it along those sections |
| It is long, or holds many sections or criteria, and neither signal above holds | None: size alone never splits a spec |

For the history signal, judge from the spec's Git history whether unrelated changes edit it at the same time, each only in sections the other never touches.
One way to read it: where branches merge, `git log --merges -- <spec>` lists merges in which both sides changed the spec, and diffing each side against its merge base (`git merge-base <merge>^1 <merge>^2`) shows the sections each side touched.
Where history is squashed or linear, read the spec's commits (`git log -p -- <spec>`) for unrelated changes landing close together or interleaved, each in sections the other never touches.

For each spec a signal holds for, present the human one proposal naming the signal, each new spec's file and title, and the stories, criteria, sections, and boundaries each new spec takes.
Name each new spec for its domain, never for a change, fix, or lane.
Show in the proposal every line a new spec needs that the original does not hold, such as its title, its context, or a modular boundary, so the human decides it with the split.
Ask the human to settle each proposal: split, or leave it.

On split:
- Move each named block to its new spec with its wording and its anchor unchanged, so every moved anchor still resolves in its new home.
- Give each new spec the shaped sections; every spec, the original included, passes the [validator](#validate).
- Point every link in the repo to a moved anchor at its new spec, and list each moved anchor with its old and new spec in the split commit message for links outside the repo.
- Commit the split as one commit of its own; it changes no behavior sentence, acceptance criterion, or boundary.

On leave, change no spec.

Repair asks in the run, never in the spec: it never splits a spec without the human's choice, and writes nothing about a split into a spec as a finding, note, or TBD.
In repair, the split check, and so the run, is not done until the human has settled every proposal; asked whether the run is done before then, answer that the split check is waiting on the unsettled proposal and report no completion.

## Drift check

After the seven repairs and the split check, check each spec against the code that implements it.
Drift is a criterion whose behavior the code does differently, or a code behavior no criterion of the owning spec states, including code no pointer connects to any spec whose behavior the spec covers in spirit.
Read code; never edit it.

Find the code a spec points to, in order, stopping at the first step that yields code:
1. Code the spec links.
2. Code committed together with the spec (its shared commits in Git history).
3. Code named by its modular boundary seams.

Then always review code no pointer connects to any spec, and judge whether its behavior falls within this spec's scope in spirit.
Write no spec-to-code index; the spec's links and Git history already are the pointers.

For each drift, judge whether the code's behavior makes sense and fits the spirit of the spec, and present the human one suggestion naming the spec anchor, the code location, the mismatch, and which side to change:

| Drift, as judged | Suggestion |
| --- | --- |
| Code does a criterion's behavior differently; it makes sense and fits the spirit of the spec (the common case, a spec behind its code) | Update the spec to match the code (default) |
| Code does a criterion's behavior differently; it looks broken or against the spirit of the spec | Fix the code, saying why; stays a recommendation |
| Code has a behavior no criterion states, whether the spec points to that code or only covers it in spirit | Add a criterion for it to the owning spec (name the code location and the behavior) |

Ask the human to decide each suggestion: adjust the spec, fix the code later, or leave it.
- Adjust the spec: make exactly the edit the human chose and no other change for that suggestion, then commit it.
- Fix the code later: change neither spec nor code; the code fix stays a recommendation to the human.
- Leave it: change neither spec nor code.

Nothing about drift is written into a spec as a finding, note, or TBD; a spec changes only by the edits the human chose.
The drift check, and so the run, is not done until the human has settled every suggestion; asked whether the run is done before then, answer that the drift check is waiting on the unsettled suggestion and report no completion.
A spec whose code the check cannot find gets no suggestion and no change; tell the human that spec was not checked against code.

## One retry rule

If a repair edit raises a new contradiction or rule miss on the same clause, write an open TBD (`data-spec-tbd`) at that clause instead of editing again.
The TBD blocks spec acceptance until the human settles it.
Same rule as the shaping agent's [fix-raises-new-mark](../../docs/specs/project-rules.spec.html#agent-once-stop).

## Output

1. Commit on the current branch at each step: the repaired specs, mockups, and ADRs as validation starts, each fix validation needs, each split the human chose as one commit of its own, and the drift edits the human chose.
2. When the drift check completes, register each changed or new spec with the review server so the reviewer sees changes with Git focus.
   A shipped screen's mockup frames the product the caller runs: the caller registers the repaired specs with its address ([prototype address](../../docs/specs/mockups.spec.html#prototype-address)), as shaping's caller does.

Commits are local only; the skill never pushes, opens a pull request, or creates an issue.

## Catch-up

Catch-up is the batch entry point of the [project rule checks](../../docs/specs/project-rules.spec.html): the same scope and rule-check questions the review page and the agent read use, over every spec of the project at once.
Onboarding runs it once when a box is set up; anyone may run it again at any time as a check.
The skill names no project's rules and keeps no rule list: rules are the criteria the specs mark with `data-project-rule`, and Jev's checks find the rest.

Read Jev through the agent read, one review link per spec:
1. Serve the project's specs with `spec-chat-review` (its lane command), so the read sees every other spec's marked rules.
2. For each spec, take a base the spec differs from, such as the repo's first commit (`git rev-list --max-parents=0 HEAD`), since rules are checked only on a spec that differs from its base; a spec unchanged since that base is not rule-checked, so tell the human.
3. Run `scripts/jev-read.py '<review-url>' --wait 300` per spec; `--anchor <id>` gives an item's full detail.
4. When the read says `off`, report that the rule checks need Jev, propose nothing, and change no spec.

Then present the proposals in two steps, one line per proposal, and let the human settle each:

| Step | Proposes | The human settles each as | Recorded |
| --- | --- | --- | --- |
| 1. Candidates | Each candidates entry whose path is the spec read: an unmarked criterion Jev reads as applying to every feature | Mark it with a short name; Not a project rule; or skip | Mark: add `data-project-rule="<short name>"` to the criterion and commit. Not a project rule: `--not-a-rule <anchor>`. Skip: `--ignored <anchor>`; it is proposed again next run |
| 2. Misses | Each `rule` important line, one per spec and rule | Cover it; add a reason line; or Not for this spec | Cover: rewrite the spec's criteria to satisfy the rule using its own wording, and commit. Reason line: add it to the spec, commit, and `--resolve <anchor> <clause> <record> reason --reason '<reason>'`, the three as the line lists them. Not for this spec: `--not-for-spec <home> <record>`, home and record from `--anchor` |

Step 2 runs after step 1: read every spec again once step 1 is settled and committed, so a rule the human just marked is checked in the same run.
Catch-up is idempotent: a marked criterion, one answered Not a project rule, and a covered, reasoned, or dismissed miss are absent from the read, so a second run proposes only what is still open, and reports nothing to settle when nothing is.
The run is not done until the human has settled or skipped every proposal; make only the spec edits the human chose, under the one retry rule.

## Burden

No persistent coordination machinery, duplicate state, or new event protocol.
Plain files and Git remain the recovery contract.
