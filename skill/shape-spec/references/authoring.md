# Spec authoring

Optimize for maximum contract-bearing information per unit of attention and page space, constrained by high readability.
Treat the spec as a coherent visual contract, not prose placed inside cards.

## User stories

Every governing HTML spec contains explicit story elements with `data-user-story`, a stable `data-anchor`, and `data-user-facing="true|false"`.
Every changed user-facing story declares `data-guided-journey="yes|no"`.
A no declaration needs no exemption rationale and does not imply that every feature belongs in onboarding.
A yes declaration contains exactly one repository-relative `<a data-guided-journey-step href="...#step-anchor">`, plus `data-guided-journey-mode="passive|required"` on the story.
A required declaration also names its feature-owned semantic success milestone in `data-guided-journey-milestone`.
The declaration is canonical only in HTML; Linear carries lifecycle and links, while Markdown story catalogs remain generated QA output.
Spec Chat review judges whether the declaration, step, mode, and milestone are semantically correct; the validator checks only presence and reference integrity.
Runtime ownership stays independent: a central registry orders the journey, while feature modules own targets and success milestones.

## Required shaped-spec sections

New or materially revised governing specs opt into the structural contract with
`data-spec-contract="shaped-sections-v1"` on the article. Untouched specs without
that marker remain on the legacy path until an explicit migration.

Every marked spec has exactly one visible, named section for each contract
surface, in this order: `User stories`, `Acceptance criteria`, then
`Modular boundaries`. Every acceptance criterion carries `data-story` naming one
or more space-separated `data-user-story` anchors in the same spec, and every
story is named by at least one criterion.

```html
<article class="spec" data-spec-contract="shaped-sections-v1">
  <section data-spec-section="user-stories" data-anchor="user-stories">
    <h2>User stories</h2>
    <p data-user-story data-anchor="story-name" data-user-facing="true" data-guided-journey="no">As a ...</p>
  </section>
  <section data-spec-section="acceptance" data-acceptance-scope="traceability" data-anchor="acceptance">
    <h2>Acceptance criteria</h2>
    <p data-acceptance-criterion data-anchor="acceptance-name" data-story="story-name">
      <span data-acceptance-scenario>Given ... When ...</span>
      <span data-acceptance-observable>Then the page ...</span>
    </p>
  </section>
  <section data-spec-section="modular-boundaries" data-anchor="modular-boundaries">
    <h2>Modular boundaries</h2>
    <p data-modular-boundary data-anchor="boundary-review">
      <span data-boundary-responsibility>Review owns ...</span>
      <span data-boundary-seam>The caller-facing seam is ...</span>
      <span data-boundary-dependency>Direction is self-contained or external-provider ...</span>
      <span data-boundary-scope>Observable scope: ...</span>
    </p>
  </section>
</article>
```

The validator requires exact `h2` headings `User stories`, `Acceptance criteria`,
and `Modular boundaries`. Every story must be inside the User stories section,
so stories cannot be buried in behavior prose. Acceptance criteria prove the
requirements of the governing spec. Their scope is carried by the descriptive
`data-acceptance-scope` when useful, anchors, and surrounding source context;
these scope hints are optional and descriptive, and no MVP-labelled heading is required. A deferred or `data-spec-tbd`
criterion cannot satisfy the governing Acceptance criteria section.

Write each acceptance criterion as an observable Given/When/Then: the
scenario states the starting situation (Given) and the action (When), and the
observable states the visible result (Then). A criterion in this shape is
clear to read and checkable: anyone can set up the Given, do the When, and see
whether the Then holds. Use plain language; quote a UI name when it helps.
Write every clear criterion at user-story level, what a person does and sees and never a click script, so a QA capture program can drive it without guessing; these rules apply at shaping to each criterion the change adds or edits, and at repair to every criterion.
Shaping never edits a criterion only to bring it to these rules: a criterion the change does not otherwise edit keeps its text word for word, whatever rule it misses.
One scenario per criterion: no Given or When joins alternatives with "or"; each alternative becomes its own criterion.
Then names the observable outcome in product terms, what the user sees at the end, as in "the account's cost page shows the generation under user-driven cost"; it names no artifact path, capture file, command, or test output.
A Then that states only an absence also names something visible that proves the end state was reached, and a Then never defers to other contracts, as in "remains governed by existing contracts".
Every Given names who the user is as a role, such as "a creator" or "an internal reviewer viewing a client account", and a situation that a fixture declared in the target repository provides; when none does, the Given says a fixture must be added to provide it.
A Given never names a fixture identifier or fixture user; the target's qa.toml maps each role to its fixture user.
A Given holds only state a fixture can set, such as who the user is and what data exists, and the user's context, such as a phone, tablet, or desktop and light or dark appearance; a state the user reaches by a gesture is a When step, so "Given a creator with the sidebar collapsed" becomes "Given a creator on the projects page, When the creator collapses the sidebar".
A When step is something the user does, such as opening, pressing, or typing; looking or reading is not a step, so what the user sees belongs in the Then: "When the learner looks at the graph and the script control, Then the script control reads Zhuyin" becomes "When the learner opens the graph, Then its script control reads Zhuyin".
The Then is visible on the screen the last When step leaves: a When that ends by dismissing something, such as pressing Escape, also names what the user then sees, and the Then names it, never only that something closed.
When a Then compares what the screen shows before and after the When, as in "stays where it was" or "no longer shows", the When joins each step to the next with "then", as in "opens the spec, then opens a criterion's popover, then clicks its bundle link", never with a comma alone.
A Then reads from one screen or state; an outcome that needs two screens splits into one criterion per screen, each with its own When reaching that screen.
A Then needs two screens when it reads past a navigation, such as to another tab, back to an earlier screen, or to another page, or reads two separate surfaces, as in "the Transcript tab marks the hook and the overview card names it" or "the clip plays and Back returns to Discover".
An outcome no screen shows follows the no-screen recipe below instead.
A criterion names each thing by what it is, as in "the lane with a QA bundle", never by screen position or order, as in "the first card".
Shaping, review, and repair judge these criterion rules; the structural validator fails on none of them, because each is a judgment on meaning a markup check cannot make, and only warns on a Then that looks to span two screens.
Its own checks of a criterion's markup, a scenario holding a When and an observable outcome, run only on each criterion the change adds or edits; a criterion whose text is unchanged from the base passes as it is.
It fails a change that drops a proof clause: each clause beginning "proven by" that a criterion at the base carries reads word for word in a criterion of the changed spec, and the failure names the base criterion and its clause.
Repair's whole-spec check, `validate-style.py --whole-spec`, runs those markup checks, and the two-screen warning, on every criterion, and runs the story and section checks on the spec even when it is unchanged.
It counts a base proof clause as kept when a criterion of any spec of the repository carries it word for word, so a criterion repair moves to its owning spec keeps its clause, and it fails a clause of a spec deleted since the base that no spec carries.
On each criterion the change adds or edits, the validator warns when its Then names two different screens, tabs, pages, or routes, or joins two visible outcomes with a navigation step, such as opening another page or going back; the warning names the criterion and the words that raised it, and validation still passes.
A navigation step is the user moving to another screen, never focus moving within one, as in "focus returns to the Save button"; a Then whose only outcome before the move is something closing, as in "the menu closes and the user returns to the list", reads from the screen the move reaches.
Answer each warning by splitting that criterion as the one-screen rule states, or, when its Then reads from one screen despite those words, by writing the reason on the criterion, as in `data-one-screen="both tabs show side by side on one screen"`; an empty reason counts as none.
A criterion whose Then says no screen shows it, and a criterion unchanged from the base, raise no warning; no warning leads shaping or repair to edit a criterion the change does not otherwise edit.
When no screen shows the outcome, as for an analytics event or a stored record, or the outcome lies outside the page, as for a sound, the browser tab title or icon, an operating system dialog, or a live third-party page, the Then states the outcome in product terms, as in "Then the funnel step is recorded once".
This is Spec Chat's whole recipe for such a criterion: shaping does not add a capture file, readback command, or stub to make it drivable, and the target, not the spec, names any artifact.
How a criterion is tested belongs to the target repository, so Spec Chat's criterion rules neither add nor remove a test level, proof method, or proving test, as in "proven by a unit test: no screen shows it".
Shaping writes such a clause where a target project rule, a criterion the target marks with `data-project-rule`, calls for one; a criterion shaping adds under no such rule names no test level or proof.
Shaping and repair keep every such clause a criterion carries, word for word, also when they rewrite the rest of that criterion; when they split it into several, the clause stays word for word on each split criterion whose outcome it proves, so at least one keeps it.
QA harness setup is not product behavior: stubs, capture files, readback tooling, and fixture extensions never appear in a product spec; they belong to the target's qa.toml or QA docs.
No hidden steps: when reaching the Then needs something the user must do, such as finishing a re-run setup after a handle change, the When says so in user terms; no step the Then depends on is left implied.
Concrete values, selectors, and paths stay out of the spec: the When says what the user types or picks in user terms, as in "types a folder name", and the target's qa.toml hint file carries the exact value.
A gesture or timing is its own explicit step with its target or duration, such as "press and hold 500ms" or "drag to the Ideas tile", and the criterion says in plain words that it needs that gesture or timing, so a QA program without it can report the criterion unsupported instead of failed.
Spec Chat keeps no list of QA capabilities; the plain words are the whole contract.
A state that cannot occur live is marked "needs an induced failure: <what fails>", as in "needs an induced failure: provider timeout"; the spec does not say how the target induces it.
Given, When, and Then open their clauses capitalized as written here, the same way throughout a spec, with no label before them such as a number or "Scenario:"; a criterion in a table cell keeps each Given, When, and Then a readable clause of its own.

A `data-spec-tbd` marker is open unless its value is `later`. Open TBDs block
spec acceptance and are highlighted in review. `data-spec-tbd="later"` marks a
TBD deliberately left for a later slice: it stays visible text but neither
blocks spec acceptance nor is highlighted.

A TBD question whose answer is one of a few options offers each option as an
answer button: a `button` carrying `data-spec-choice`, whose value is the
answer's text, inside the question's anchored TBD block. The recommended
option's button text ends in `(recommended)`; the value stays the bare answer.
The page needs no script, and an answer no button offers takes an ordinary
comment on that block.

```html
<p data-anchor="retry-limit" data-spec-tbd>How many retries before checkout fails?
  <button type="button" data-spec-choice="Three retries">Three retries (recommended)</button>
  <button type="button" data-spec-choice="One retry">One retry</button>
</p>
```

Every marked spec has one Modular boundaries section, including a self-contained
single-module spec. Each anchored boundary names responsibility, the
caller-facing seam, dependency direction including self-contained or an
external provider, and observable scope. The structural gate checks these
fields; review still judges whether their meanings are correct.

## Information plan

Before writing or styling, classify each behavior cluster:

- Required visual: prose would lose contract-bearing structure.
- Valuable visual: an artifact materially improves comprehension, comparison, error detection, memory, or review.
- Prose: the rule is atomic and equally or more legible without a visual.

Realize every required and valuable opportunity, with no fixed artifact count.
Separate unrelated relationships; use complementary views only when each preserves a distinct dimension.
Give every rule one authoritative expression: artifacts replace relational prose rather than duplicate it.

Choose the least-lossy form:

- topology or module ownership: directed semantic diagram with labeled seams
- ownership and handoffs across actors: swimlane diagram
- guarded state: state machine with guards and meaningful self-loops
- lifecycle, order, or cycle: rail, numbered sequence, or loop
- spatial behavior: proportional wireframe
- quantities: chart with real values, scale, labels, and thresholds
- exact mapping, precedence, or evidence: explicit table
- peer axioms: identified clause group
- formula: readable notation with a one-line gloss

When simplifying visible statuses, use a table mapping underlying state and evidence to label and recovery action; fewer labels do not erase lifecycle or failure rules.

Preserve existing `data-anchor` identities; give every new contract-bearing section, clause, figure, row, and acceptance rule a stable anchor.
Use one sentence per prose line and pretty semantic-island JSON beside its render target.
A removable visual is decoration, not a contract artifact.

State what the user sees, never how it looks or moves: look and motion mechanics, such as animation timings and their order, easing, colors, and spacing, belong to the design system, its design document and shared parts, so link the design system for them and a change to a color or an animation never becomes a spec change.
A timing the user feels is an outcome and stays one, as in "the list shows within two seconds".
On each clause the change adds or edits, the validator warns when it states a color value, such as a hex, rgb, or hsl color; an easing curve, such as a cubic Bézier or a named CSS easing; or a motion timeline, two or more durations under a second in one clause or across the rows of one table, as a choreography table holds; the warning names the clause and the words that raised it, and validation still passes.
One duration in a clause, as a press held for a set time or an answer within a set time, raises no warning.
Answer each warning by moving the mechanics to the design system and linking it, or by stating the outcome the user feels; a clause whose text is unchanged from the base raises no warning, so never edit a clause the change does not otherwise edit to answer one.
Repair's whole-spec check, `validate-style.py --whole-spec`, runs the warning on every clause.

Every shaped spec includes a concise Modular boundaries section naming responsibility, caller-facing seam, dependency direction, and observable scope, including self-contained single-module specs.
Prefer a semantic diagram for multiple relationships; omit classes, functions, files, and replaceable detail.

## Visual system

Use one coherent system for type roles, surfaces, text, rules, semantic colors, focus, spacing, geometry, hierarchy, responsive behavior, and annotation affordance.
Color always has a text, position, or line-shape cue.
Normal text and labels meet 4.5:1 contrast; large text and meaningful boundaries meet 3:1; muted text never relies on low opacity.
A figure takes its colors from the shared stylesheet, never fixed colors of its own, so its labels and edges meet that contrast in light and dark appearance.

Use only a shared spec stylesheet that already exists unchanged at the exact review base.
If none exists, copy [../assets/style/spec.css](../assets/style/spec.css) unchanged to the target shared spec style directory and link it relatively.
Do not invent a page-level palette, typography, background, surface, or geometry system during shaping, and do not put one in a spec-local `<style>` block.
Inline styling is limited to artifact-local geometry and semantic marks that the shared system does not express.
The fallback is the approved default, not an invitation to synthesize another aesthetic.

Every artifact needs collision-free labels, complete distinguishable edges, intentional grouping, legible type without zoom, a contract-bearing title or caption, and stable anchors.
Change the layout or renderer instead of shrinking content until it fits.
Use ECharts for quantities, not topology; use readable inline SVG for small exact diagrams and Beautiful Mermaid when semantic source expresses the system cleanly.
A figure draws the mechanism or screen its adjacent text states in one of the information plan's forms, never as ASCII art in a preformatted block, a placeholder, or a picture the text has moved past.

### Shared markup

Inspect the established stylesheet's layout before reusing its classes.
In the bundled system, `.sec-num` occupies a narrow marker column; put the title in `h2`:

```html
<div class="sec-head">
  <span class="sec-num">02</span>
  <h2 data-anchor="save-heading">Save and continue</h2>
  <p class="sec-note" data-anchor="save-note">Durability before navigation.</p>
</div>
```

`.duo` places its immediate children in two columns; a title for both belongs outside that grid:

```html
<figure data-anchor="save-comparison">
  <h3 data-anchor="save-comparison-heading">Save behavior</h3>
  <div class="duo">
    <p data-anchor="save-before">Before: wait for background completion.</p>
    <p data-anchor="save-after">After: advance once work is durably owned.</p>
  </div>
</figure>
```

Use `.table-scroll` around a wide table and `.rail-wrap` around a rail so local horizontal scrolling stays inside the page.
Headings step down one level at a time, never skipping one, and a callout uses markup the shared stylesheet already styles, never ad hoc styling of its own.

## Mockups

When a change adds or changes a screen, shaping writes or updates a mockup of each such screen and commits it with the spec; a change with no screen writes none.
A mockup shows one screen as the spec intends it, at desktop and phone; the spec stays canonical for behavior, so where they disagree, fix the mockup.
It is at [production fidelity](../../../docs/specs/mockups.spec.html#fidelity): the screen as the product will ship it, in the product's own chrome, components, type, tokens, states, motion, and words, rendered by the project's own frontend code, never a wireframe, a generic frame, or a picture, so the reviewer accepts what users will see; only chrome the project does not own is drawn.
Avoid unnecessary secondary labels and descriptions. Let the layout do the explaining ([labels and descriptions](../../../docs/specs/mockups.spec.html#fidelity-labels)): set each part the change adds beside what it acts on, in the group it belongs to, in the order its user works, as the kind of control that does what it does, then give it a secondary label, caption, or description only where its user still could not tell what it is or does.
Like its spec, a mockup shows the target state only, never a before or history; Git keeps the before.
Its only alternatives are a screen's first-round [directions](#directions), which the round after removes.
It shows real content: the repo's fixture data, the product's own words, and the spec's names for things, never placeholder text.

A spec's mockup set is the folder `mockups/<spec name>/` beside the spec, `<spec name>` being the spec's file name without `.spec.html`.
Each screen is one file there, `<NN>-<screen>.mockup.html`: `NN` two digits ordering the screens, `<screen>` its name in lowercase words joined by hyphens.
Images, font files, and other files a mockup shows sit in the same folder, or in `mockups/` itself when several sets show them; no list names the screens, so adding a screen is adding a file.
Keep no rendered picture beside a mockup: a reader that needs one renders the frame.

### A running prototype first

A screen the project owns has its [running prototype](../../../docs/specs/mockups.spec.html#prototype-what) as its mockup: the project's own frontend code on the lane branch, never redrawn and never a picture, showing real data where reading it is safe, else the repo's fixture data.
It is either the screen's real route of the app, built from its shipped components and chrome, imported or copied, each new part in the same design system, or a [harness page](../../../docs/specs/mockups.spec.html#prototype-harness): a route rendering the project's shipped components on the repo's fixture data inside the chrome they ship in, for a part the app shows on no route of its own, such as one inside another product's page, or a state no route reaches safely.
The prototype is written on the lane branch outside this skill. The project's [prototype command](../../../docs/specs/mockups.spec.html#prototype-seam), named in its `AGENTS.md`, serves the app's routes and harness pages at the root of a loopback address, as the product ships; the caller runs it from the lane worktree and names that address with the lane command's `--prototype` (`spec-chat-review`), and review frames the route at the lane's own [prototype origin](../../../docs/specs/review-service.spec.html#prototype-proxy), never under a base path.
The build keeps its components, so the screen accepted is the code that ships.
Shaping writes the screen's mockup file, which names the route and holds all review needs, so the product's code carries nothing of Spec Chat's; it draws no frame and carries no style and no script but the page runtime, the `runtime.js` its spec page loads, by its path from the set's folder (as a drawn mockup loads it, below):

```html
<!-- docs/specs/mockups/report/01-weekly-report.mockup.html -->
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="mockup-prototype" content="/reports/weekly">
<title>Weekly report - mockup</title>
<script defer src="../../.viz/runtime.js"></script>
</head>
<body>
<ul data-mockup-regions>
  <li data-anchor="team-panel" data-select="#team-panel" data-illustrates="acceptance-team">Team panel</li>
  <li data-anchor="export-button" data-select="#export" data-illustrates="acceptance-export">Export button</li>
</ul>
<ol data-mockup-steps>
  <li data-step="totals" data-illustrates="acceptance-totals">Every team's total for the week</li>
  <li data-step="team-open" data-illustrates="acceptance-team">Payments opens its last three weeks<ul><li data-press=".team-card[data-team=Payments]"></li></ul></li>
  <li data-step="search">The list keeps teams named Pay<ul><li data-type="#search" data-text="Pay"></li></ul></li>
</ol>
<aside data-mockup-questions>
  <p data-anchor="question-trend" data-spec-tbd data-about="team-panel" data-step="team-open">Should the team panel show a trend line?
    <button type="button" data-spec-choice="Totals only">Totals only (recommended)</button>
    <button type="button" data-spec-choice="Add a trend line">Add a trend line</button></p>
</aside>
</body>
</html>
```

Each region is a `<li>` with its `data-anchor`, unique in the mockup and stable across rounds, the selector finding it on the route in `data-select`, the spec anchors it illustrates, and its name in words as its text.
Each step lists the actions that reach it from the step before: `data-press` presses what its selector finds, `data-type` with `data-text` types that text into it; review and the lane's recorder do every action up to a step, each after the motion before it ends, so no step needs code of its own.
Each round's videos are the lane's recorder's, in the mockup's spool; the file names none.

### A drawn mockup, only for chrome the project does not own

Only chrome the project does not own is [drawn](../../../docs/specs/mockups.spec.html#prototype-drawn): a screen of another product's own chrome, such as a host product's page a walkthrough passes through, is a drawn mockup, a page that draws its own two frames from that chrome's design source and names that product in `<meta name="mockup-chrome">`; say why it is drawn in the hand-off that sends the review link.
A screen the project owns is never drawn, even one its product does not serve yet: its mockup is a prototype route or harness page.

A drawn mockup is self-contained, unlike a spec page: it carries its own `<style>`, written with its design source's tokens by their names and values, and never the spec stylesheet.
Its fidelity note, `<meta name="mockup-design-source">`, names each [design source](../../../docs/specs/mockups.spec.html#fidelity-source) file it was drawn from, kept in the repo, the files holding that chrome's tokens, type, components, and motion rules, such as a copy of its owner's published design tokens, as `<path>@<commit>` separated by spaces, or `none` in a repo with none.
It loads the page runtime its spec page loads, the same `runtime.js` file by its path from the set's folder: `../../.viz/runtime.js` for a spec page loading `./.viz/runtime.js`, as in these examples, and `../../../.viz/runtime.js` for a spec a folder below, such as `docs/specs/domains/`, loading `../.viz/runtime.js`.
It loads nothing else from outside the spec folder, so it renders the same from any server and at any commit; it carries no script of its own.
Draw every part as the [fidelity table](../../../docs/specs/mockups.spec.html#fidelity-table) holds it: that product's chrome and words; every color, duration, and easing from a token its frames declare; the frames' font from a token, and a font the product loads from a file loaded from that file beside the mockup; one primary action per state, the design source's spacing scale, and phone targets of at least 44 CSS pixels; every control's hover, focus, pressed, and disabled styles; and motion by the design source's rules, with a `prefers-reduced-motion` rule.

Review shows the frames inside the review page, each moved into its own slot, with the mockup's `<style>` still applied to the whole page, so scope every rule to the frames: each selector starts with `[data-mockup-frame]` or is a class used only inside a frame, and the tokens, background, font, and color are set on `[data-mockup-frame]`.
Write no `:root`, `html`, `body`, or bare element rule such as `button` or `table`, which restyles the review page's own controls, and no selector keyed to where a frame sits in the page, such as `body > section`, `section + section`, or `:first-of-type`, which stops matching once the frame is in its slot.

It holds exactly two frames showing the same screen, laid out as the product lays it out at that width: a desktop frame 1440 CSS pixels wide and a phone frame 390 CSS pixels wide, each as tall as its content; in a first round, two more for each other [direction](#directions).
Every frame, and every region of it a reviewer may comment on, such as a card, a panel, or a bar, carries a `data-anchor` unique in its mockup and stable across edits.
A frame, or an anchored element inside one, names the anchors of its own spec it shows in `data-illustrates`, separated by spaces; every frame names at least one, on itself or on a part of it.

```html
<!-- docs/specs/mockups/comment-layer/01-host-page.mockup.html -->
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="mockup-chrome" content="Northwind Wiki">
<meta name="mockup-design-source" content="design/northwind-wiki-tokens.css@1a2b3c4">
<title>Host page - mockup</title>
<style>
  [data-mockup-frame] { --color-ink: #1d1d1f; --color-surface: #ffffff; --font-ui: Inter, system-ui, sans-serif; /* the host's design tokens, by name and value */
                        background: var(--color-surface); color: var(--color-ink); font: 16px/1.5 var(--font-ui); }
  [data-mockup-frame="desktop"] { width: 1440px; }
  [data-mockup-frame="phone"] { width: 390px; }
  [data-mockup-frame] img { width: 32px; border-radius: 50%; }
</style>
<script defer src="../../.viz/runtime.js"></script>
</head>
<body>
  <section data-mockup-frame="desktop" data-anchor="desktop" data-illustrates="host-layout">
    <div class="card" data-anchor="desktop-page-header" data-illustrates="acceptance-host-page">
      <img src="avatar.png" alt="Ada Lovelace"> Weekly totals, edited by Ada Lovelace
    </div>
  </section>
  <section data-mockup-frame="phone" data-anchor="phone" data-illustrates="host-layout">
    <div class="card" data-anchor="phone-page-header" data-illustrates="acceptance-host-page">
      <img src="avatar.png" alt="Ada Lovelace"> Weekly totals, edited by Ada Lovelace
    </div>
  </section>
</body>
</html>
```

### Walkthrough

A screen that changes as its user acts, or moves on its own, holds a [walkthrough](../../../docs/specs/mockups.spec.html#walkthrough): `<ol data-mockup-steps>` outside the frames, one `<li data-step="<name>">` per state in order, its caption in the product's words as its text, and the spec anchors it shows in `data-illustrates`.
A state the screen reaches, such as empty, loading, error, or done, is a step; a screen with one state holds none.
A prototype's steps list their actions (above).
In a drawn mockup, a step's `data-step-by` names the anchors of the parts whose press reaches it, and the runtime sets each frame's `data-at` to the names of the steps reached so far, the showing one last; the mockup's style draws each step from it with its own transitions, so steps need no script:

```html
<!-- a step in <ol data-mockup-steps>, after the frames -->
<li data-step="team-open" data-step-by="desktop-open phone-open" data-illustrates="acceptance-team">Payments opens its last three weeks</li>
<!-- in the mockup's <style>: the panel fades in once that step is reached -->
[data-mockup-frame] .panel { opacity: 0; transition: opacity var(--motion-duration) var(--motion-ease); }
[data-at~="team-open"] .panel { opacity: 1; }
```

### Questions on the mockup

Ask a question about how a screen looks or behaves on its mockup, at the part and step it is about, never in the spec's text ([mockup questions](../../../docs/specs/mockups.spec.html#mockup-questions)): in `<aside data-mockup-questions>` outside the frames, an anchored TBD block with its answer buttons, as a spec's, naming in `data-about` the anchors of the parts it is about and in `data-step` the step it is asked at (the prototype example above).
It is open, and blocks Accept spec, as a spec's TBD is.
Settle it by editing the mockup, the spec, or both, removing the question, and replying on its thread, so the next round shows the change at the part and step it names.

### Directions

A screen's first round shows its [directions](../../../docs/specs/mockups.spec.html#directions): two ways it could go, or three when one differs in kind, side by side on its mockup page, so the reviewer picks between real options in the round they review anyway.
Write them only for a screen the change adds or changes, and only in the round that first shows it; a change with no screen, and every later round, shows none.
Make each a real option at production fidelity, the spec staying canonical for what each does.

List them in `<ul data-mockup-directions data-anchor="directions">` outside the frames, one `<li data-direction="<key>">` per direction: its short name in the product's words as its text, the one line saying what it gives up in `data-gives-up`, and on your pick its one-line reason in `data-pick`.
The pick is the mockup itself, so its regions, steps, and questions are the pick's, and only it is walked through and recorded: in a prototype mockup the file's route, in a drawn one its two frames.
Each other direction is, in a prototype mockup, another route of the same running prototype named in `data-route`, written on the lane branch as the pick's route is; in a drawn mockup, its own desktop and phone frames, each naming it in `data-direction`, anchored and illustrating as every frame is.

```html
<!-- in 01-weekly-report.mockup.html, after its regions -->
<ul data-mockup-directions data-anchor="directions">
  <li data-direction="cards" data-gives-up="A team's earlier weeks take a press to see." data-pick="Every team's total reads at a glance on a phone, and a press opens its weeks.">Team cards</li>
  <li data-direction="table" data-gives-up="On a phone the three weeks squeeze into narrow columns." data-route="/reports/weekly/table">Team table</li>
</ul>
```

A reviewer's Pick arrives as an answer on the directions list, its words the picked direction's name (`spec-chat-review`).
The round after brings the screen back to one direction, whatever its hand-off holds: the one the newest Pick names, else your pick.
Make that direction the mockup: in a prototype mockup, the route `<meta name="mockup-prototype">` names shows it, each region's selector and each step's actions found on it; in a drawn mockup, its frames are the two frames, naming no direction.
Keep the anchor of every part the screen still shows, remove the other directions and the directions list, and settle a Pick by replying on its thread; the prototype drops the other routes, product code on the lane branch outside this skill.
Directions hold up no acceptance: with no Pick pressed, Accept spec accepts your pick, and a reader of accepted mockups reads the pick alone.

### Markup

| Where | Markup | Holds |
| --- | --- | --- |
| The page | `<title>` | The screen's name in words, then ` - mockup`, as `Host page - mockup`; the chip, the panel, and the top line name the screen by it. |
| Each drawn frame | `data-mockup-frame` | `desktop` or `phone`, once each per mockup, and once each per other direction it draws, those frames naming it in `data-direction`. |
| Each frame and region | `data-anchor` | Its anchor in the mockup, where its threads attach. |
| A frame, an anchored part of one, a region, or a step | `data-illustrates` | The anchors of the spec it shows, separated by spaces. |
| A prototype mockup | `<meta name="mockup-prototype">` | The screen's route, a path on the running prototype. |
| A prototype mockup | `<ul data-mockup-regions>`, one `<li data-anchor data-select>` per region | Each region's anchor, its selector on the route, the spec anchors it illustrates, and its name as its text. |
| A prototype mockup's step | A list of `<li data-press>` and `<li data-type data-text>` | The actions that reach the step from the one before, in order. |
| A drawn mockup | `<meta name="mockup-chrome">` | The product whose chrome it draws, one the project does not own. |
| A drawn mockup | `<meta name="mockup-design-source">` | The fidelity note: each design source file as `<path>@<commit>`, separated by spaces, or `none`. |
| The page, outside the frames | `<ol data-mockup-steps>`, one `<li data-step>` per step | The walkthrough: each step's name in `data-step`, its caption as its text, and in a drawn mockup `data-step-by`, the anchors of the parts whose press reaches it. |
| Each drawn frame, set by the runtime | `data-at` | The names of the steps reached so far, the showing one last; the mockup's style draws each step from it. |
| The page, outside the frames | `<aside data-mockup-questions>` | The open questions, each an anchored TBD block with its answer buttons, naming its parts in `data-about` and its step in `data-step`. |
| The page, outside the frames | `<ul data-mockup-directions data-anchor="directions">`, one `<li data-direction>` per direction | A first round's directions: each one's short name as its text, its key in `data-direction`, the line saying what it gives up in `data-gives-up`, on the pick its reason in `data-pick`, and on each other direction of a prototype mockup its route in `data-route`. |

### Checks

The structural validator checks the mockup set of each spec it validates: each file's name, a drawn mockup's exactly one desktop and one phone frame for its screen and for each other direction it draws, unique anchors, every drawn frame naming at least one anchor it illustrates, and every illustrated anchor present in the spec; each problem fails validation, naming the mockup and what is wrong.
On each mockup the change adds or edits, it also checks a prototype route or the two drawn frames; on a drawn mockup, the product whose chrome it draws, a fidelity note naming files in the repo, each unchanged since the commit the note names, no color, duration, or easing outside a token's declaration, the font from a token and every font file in the repo, a focus style in a frame holding a button, link, or field, a disabled style in a frame holding a disabled control, a reduced-motion rule when styles move, and no script but the runtime its spec page loads; and on both, unique step names, every part a step or question names in the mockup, every question's step in its walkthrough, and one directions list, its own anchor in `data-anchor`, of two or three directions with unique names and unique keys, exactly one of them the pick with its reason, each with its gives-up line, and each other direction its route or, in a drawn mockup, its two frames, failing on the missing pick, or on the direction and what it lacks; and on a frame naming the pick, a direction the list lacks, or none in an empty `data-direction`.
A design source changed since its note's commit fails ([why](../../../docs/specs/mockups.spec.html#why-moved)): read the source again, then update the mockup and its note.
It warns, and passes, on a note reading `none` ([checks](../../../docs/specs/mockups.spec.html#checks-warn)). A mockup the change leaves untouched passes these checks as it is; repair's whole-spec check, `--whole-spec`, runs them on every mockup of the spec, the chrome check only warning there.
The validator checks only the markup; whether the chrome is the product's, a drawn mockup's chrome is truly another product's, one action leads, or a screen needs a walkthrough is the reviewer's judgment, and yours before the review link goes out.

## Runtime

Canonical `*.spec.html` uses HTML and the committed offline Spec Chat runtime, not a React application.
Reuse the shared stylesheet and choose only the artifact renderer the information requires.
React remains appropriate for an existing embedded application or an explicitly noncanonical throwaway prototype.
