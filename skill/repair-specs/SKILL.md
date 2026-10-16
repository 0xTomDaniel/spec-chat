---
name: repair-specs
description: Bring a repo's existing specs and ADRs to the current shaping contract. Removes non-target text, moves behavior to owning specs, resolves contradictions, reduces ADRs to the ADR model, and rewrites acceptance criteria to the shaping contract's criterion rules, brings spec pages to the visual quality contract, proposes splitting a spec that holds separate domains, then checks each spec against its code for drift. Its catch-up runs the Jev project rule checks over every spec in batch. Runnable any time or as onboarding catch-up.
---

# Repair specs

One skill that brings every spec and ADR in a repo to the current [shaping contract](../shape-spec/SKILL.md).
Reads the repo's specs and ADRs, Jev's checks through the agent read (`spec-chat-review`'s `scripts/jev-read.py`), for the split check the repo's Git history, and for the drift check the repo's code and Git history; reads no onboarding status or peer status file and names no peer.

## Inputs

The caller names the repo root and optionally catch-up.
A repair run is all six repair types, the split check, and the drift check; a catch-up run is the [catch-up](#catch-up) steps only.

## Repair types

Run in this fixed order: clean, home, contradict, ADR, criteria, presentation.
Each type reads every spec and ADR as the previous type left it, so presentation draws from final text.

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

Resolve contradictions between two clauses, in one spec or in two, including a clause against its own spec's non-goals (Jev draft checks never compare clauses inside one spec):
- When two clauses contradict and one side is out of date, update the out-of-date side.
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

Rewrite acceptance criteria to the shaping contract's [criterion rules](../../docs/specs/prompt-first-shaping.spec.html#complete-criterion-capturable).
Criterion-rule repair runs whether Jev is on or off.

Criterion rules:
- For each criterion that breaks a criterion rule, rewrite it to meet every rule.
- A Given or When that joins alternatives splits into one criterion per alternative; the first keeps the original anchor, each new one gets a new stable anchor, and each meets every criterion rule.
- When a Given holds a state the user reaches by a gesture, move that gesture into the When as its opening step and keep in the Given only state a fixture can set and the user's device and appearance context (`Given a creator with the sidebar collapsed` becomes `Given a creator on the projects page, When the creator collapses the sidebar`).
- When a When step says the user looks at or reads something, replace it with the step the user does to reach that screen, as the specs state it, and move what the user sees into the Then (`When the learner looks at the graph and the script control, Then the script control reads Zhuyin` becomes `When the learner opens the graph, Then its script control reads Zhuyin`).
- When a Then needs two screens, split it into one criterion per screen; the first keeps the original anchor, the new one gets a new stable anchor and a When reaching its screen, and each meets every criterion rule.
- When a When ends by dismissing something, such as pressing Escape, and the Then says only that something closed, rewrite the Then to name what the screen shows after the dismissal, as the specs state it.
- When a Then compares the screen before and after the When, such as `stays where it was` or `no longer shows`, and a comma alone separates two When steps, join them with `then` and keep the Then's wording (`opens the popover, clicks its link` becomes `opens the popover, then clicks its link`).
- When a criterion names a thing by screen position or order, such as `the first card`, rewrite it to name the thing by what it is, as the specs state it (`the first card` becomes `the lane with a QA bundle`).
- When the specs do not say what the screen shows after a dismissal, which thing a position names, or how the user reaches what a When only looks at, write an open TBD (`data-spec-tbd`) at that criterion naming what is missing; never invent it.
- When a criterion's When skips a step to its Then that the specs do not state, write an open TBD (`data-spec-tbd`) at that criterion naming the missing step; never invent a step.
- When the visible end state or the providing fixture cannot be read from the specs, write an open TBD (`data-spec-tbd`) at that criterion naming what is missing; never invent an end state or a fixture.
- When a Given does not say who the user is and the specs do not say it either, write an open TBD (`data-spec-tbd`) at that criterion asking who the user is; never invent a user.
- When a criterion holds a concrete value, selector, or path, rewrite it in user terms (`types "Q3 scripts" into Folder name` becomes `types a folder name`) and list each value moved out in the repair commit message as a value for the target's qa.toml hint file.
- When a Given names a fixture identifier or fixture user and the specs say which role it is, rewrite the Given to that role (`Given tf-admin is signed in` becomes `Given an internal admin acting in a client account`) and list the fixture user in the repair commit message as that role's fixture user for the target's qa.toml.
- When a Then names an artifact path, capture file, command, or test output, rewrite it to the outcome in product terms; when no screen shows the outcome, the Then reads `proven by a unit test: no screen shows it` (`posthog-captures.jsonl holds one funnel event` becomes `the funnel step is recorded once, proven by a unit test: no screen shows it`). List each artifact path moved out in the repair commit message for the target's qa.toml.

Project-rule misses are not a criteria repair: [catch-up](#catch-up) proposes them for the human to settle.
With Jev off, clean, home, contradict, ADR, criterion-rule, and presentation repairs still run; only the Jev project-rule checks of catch-up are skipped.

### 6. Presentation

Bring every spec page to the shaping contract's [visual quality contract](../../docs/specs/prompt-first-shaping.spec.html#visual-quality) and the [authoring](../shape-spec/references/authoring.md) figure and layout rules.
Presentation repair changes how a spec page reads, never what it says: every behavior sentence, acceptance criterion, and boundary keeps its wording.
The text governs any figure that disagrees with it.
Applies to specs only; Markdown ADRs have no figures or page styling to repair.

- An ASCII-art figure in a preformatted block, a placeholder figure, or a stale figure: redraw it as a purpose-fit diagram of the mechanism or screen the adjacent text states, with a caption and stable anchor.
- A figure that contradicts the spec text: make the figure match the text; leave the text unchanged.
- A figure painting its own fixed colors: take its colors from the shared stylesheet so labels and edges read at the contract's [contrast](../../docs/specs/prompt-first-shaping.spec.html#visual-quality-contrast) in light and dark color schemes.
- A page-level style block: remove it and link the repo's shared spec stylesheet; the spec must pass the structural validator.
- Skipped heading levels, unwrapped wide tables, or ad hoc callouts: put headings in order, wrap wide tables to scroll inside the page, and use the shared stylesheet's callout markup.
- A runtime lacking a capability the bundled runtime requires: replace it through the review [preflight](../review-spec/SKILL.md) (`scripts/preflight.py`), which commits the migration alone, touching only the shared runtime assets.
- Never turn relational prose into new figures; that changes behavior text and belongs to shaping.

The review chrome is the runtime's, owned by [spec page design](../../docs/specs/spec-page-design.spec.html), and reaches a spec only through preflight.

## Split check

After the six repairs and before the drift check, commit the repaired specs and ADRs, then check each spec for separate domains.
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
- Give each new spec the shaped sections; every spec, the original included, passes the structural validator.
- Point every link in the repo to a moved anchor at its new spec, and list each moved anchor with its old and new spec in the split commit message for links outside the repo.
- Commit the split as one commit of its own; it changes no behavior sentence, acceptance criterion, or boundary.

On leave, change no spec.

Repair asks in the run, never in the spec: it never splits a spec without the human's choice, and writes nothing about a split into a spec as a finding, note, or TBD.
In repair, the split check, and so the run, is not done until the human has settled every proposal; asked whether the run is done before then, answer that the split check is waiting on the unsettled proposal and report no completion.

## Drift check

After the six repairs and the split check, check each spec against the code that implements it.
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

## Editing contract

Before editing any spec, load [shape-spec authoring](../shape-spec/references/authoring.md) and follow it completely.

Every edit follows the authoring contract:
- One sentence per line in prose.
- Stable `data-anchor` on every block; never remove or rename an existing anchor. A split the human chose moves an anchor, unchanged, to its new spec.
- Pretty-printed semantic-island JSON beside its render target.
- Preserve existing visual artifacts, section order, and layout unless the repair requires a structural change.

## One retry rule

If a repair edit raises a new contradiction or rule miss on the same clause, write an open TBD (`data-spec-tbd`) at that clause instead of editing again.
The TBD blocks spec acceptance until the human settles it.
Same rule as the shaping agent's [fix-raises-new-mark](../../docs/specs/project-rules.spec.html#agent-once-stop).

## Output

1. Commit on the current branch at each step: the repaired specs and ADRs as the split check starts, each split the human chose as one commit of its own, and the drift edits the human chose.
2. When the drift check completes, register each changed or new spec with the review server so the reviewer sees changes with Git focus.

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
| 2. Misses | Each `rule` important line, one per spec and rule | Cover it; add a reason line; or Not for this spec | Cover: rewrite the spec's criteria to satisfy the rule using its own wording, and commit. Reason line: add it to the spec, commit, and `--resolve <record> reason --reason '<reason>'`. Not for this spec: `--not-for-spec <home> <record>`, home and record from `--anchor` |

Step 2 runs after step 1: read every spec again once step 1 is settled and committed, so a rule the human just marked is checked in the same run.
Catch-up is idempotent: a marked criterion, one answered Not a project rule, and a covered, reasoned, or dismissed miss are absent from the read, so a second run proposes only what is still open, and reports nothing to settle when nothing is.
The run is not done until the human has settled or skipped every proposal; make only the spec edits the human chose, under the one retry rule.

## Burden

No persistent coordination machinery, duplicate state, or new event protocol.
Plain files and Git remain the recovery contract.
