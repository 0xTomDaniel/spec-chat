# Spec authoring

Optimize for maximum contract-bearing information per unit of attention and page space, constrained by high readability.
Treat the spec as a coherent visual contract, not prose placed inside cards.

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

For cross-module behavior, include a concise Modular boundaries section naming responsibility, caller-facing seam, and dependency direction.
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
