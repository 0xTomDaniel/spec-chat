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
surface:

```html
<article class="spec" data-spec-contract="shaped-sections-v1">
  <section data-spec-section="user-stories" data-anchor="user-stories">
    <h2>User stories</h2>
    <p data-user-story data-anchor="story-name" data-user-facing="true" data-guided-journey="no">As a ...</p>
  </section>
  <section data-spec-section="acceptance" data-acceptance-scope="traceability" data-anchor="acceptance">
    <h2>Acceptance criteria</h2>
    <p data-acceptance-criterion data-anchor="acceptance-name">
      <span data-acceptance-scenario>When ...</span>
      <span data-acceptance-observable>The page ...</span>
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

A `data-spec-tbd` marker is open unless its value is `later`. Open TBDs block
Finish and are highlighted in review. `data-spec-tbd="later"` marks a TBD
deliberately left for a later slice: it stays visible text but neither blocks
Finish nor is highlighted.

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

Every shaped spec includes a concise Modular boundaries section naming responsibility, caller-facing seam, dependency direction, and observable scope, including self-contained single-module specs.
Prefer a semantic diagram for multiple relationships; omit classes, functions, files, and replaceable detail.

## Visual system

Use one coherent system for type roles, surfaces, text, rules, semantic colors, focus, spacing, geometry, hierarchy, responsive behavior, and annotation affordance.
Color always has a text, position, or line-shape cue.
Normal text and labels meet 4.5:1 contrast; large text and meaningful boundaries meet 3:1; muted text never relies on low opacity.

Use only a shared spec stylesheet that already exists unchanged at the exact change-request base.
If none exists, copy [../assets/style/spec.css](../assets/style/spec.css) unchanged to the target shared spec style directory and link it relatively.
Do not invent a page-level palette, typography, background, surface, or geometry system during shaping, and do not put one in a spec-local `<style>` block.
Inline styling is limited to artifact-local geometry and semantic marks that the shared system does not express.
The fallback is the approved default, not an invitation to synthesize another aesthetic.

Every artifact needs collision-free labels, complete distinguishable edges, intentional grouping, legible type without zoom, a contract-bearing title or caption, and stable anchors.
Change the layout or renderer instead of shrinking content until it fits.
Use ECharts for quantities, not topology; use readable inline SVG for small exact diagrams and Beautiful Mermaid when semantic source expresses the system cleanly.

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

## Browser gate

After the review skill verifies served bytes and baseline, inspect the complete rendered page and every artifact at desktop and mobile widths in normal and Git-focus modes before the first review handoff or a material batch's replies.
Reject it until hierarchy and contrast are clear, changed material leads, unchanged context remains usable, nothing collides or clips, diagrams need no prose reconstruction, review controls obscure nothing, and mobile has no horizontal page drift.
DOM presence or successful library initialization is not visual proof.
Complete the batch before this pass and group discovered corrections before rechecking.
For a local correction after a complete pass, inspect the affected area and nearby layout at the relevant widths and modes; repeat the full pass when shared styles, runtime, page structure, or unresolved findings make that necessary.
Keep enough readable evidence for the checks performed; do not regenerate every full page, tile, and contact sheet after each small edit.

## Runtime

Canonical `*.spec.html` uses HTML and the committed offline Spec Chat runtime, not a React application.
Reuse the shared stylesheet and choose only the artifact renderer the information requires.
React remains appropriate for an existing embedded application or an explicitly noncanonical throwaway prototype.
