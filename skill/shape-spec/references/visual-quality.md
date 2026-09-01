# Readable visual density

Optimize for maximum contract-bearing information per unit of reader attention and page space, constrained by high readability.
Treat the spec as a coherent visual contract, not prose placed inside cards.
Libraries are implementation choices made after the information and composition plan.

## Plan before authoring

Classify every behavior cluster as a required, valuable, or unsuitable visual opportunity:

- information shape
- least-lossy artifact
- visual role in the page
- observable proof that the artifact is legible and complete

- Required: prose would lose contract-bearing structure.
- Valuable: a visual materially improves comprehension, comparison, error detection, memory, or review.
- Unsuitable: prose is equally or more legible because the rule is atomic or has no useful relationship to display.

Realize every required and valuable opportunity unless a concise reason shows prose is equally or more legible.
Use no fixed artifact count.
A simple contract can remain simple, while a relationship-dense contract should produce many load-bearing visual elements.
Do not combine unrelated relationships merely to reduce artifact count.
Use complementary views when each preserves a distinct material dimension that one view cannot, such as time path plus state-versus-input or desktop plus mobile placement.

Establish a coherent reading order before styling.
Each artifact should replace relational prose, not duplicate it.
Give it only the adjacent context, caveat, or one-line gloss needed for correct interpretation.
Lists remain appropriate when item independence or literal order is itself the information shape; they are not a fallback for topology, state, causality, space, or cross-product logic.

## Positive correction map

- Library-list drift: begin with the artifact and hierarchy plan, then select the smallest suitable renderer.
- Token visual: realize every suitable visual opportunity and separate unrelated structures into legible artifacts.
- Redundant prose: make each rule authoritative once and let diagrams replace relational explanation.
- Incoherent theme: use a complete target visual system or the bundled high-contrast editorial fallback.
- Render-only QA: inspect the complete rendered page and every artifact for hierarchy, contrast, collision, clipping, and review-control obstruction.
- Weak focus base: evaluate normal and Git-focus views together so changed content leads while unchanged context remains readable.

## Visual system contract

The aesthetic may vary, but one coherent system defines:

- primary, secondary, metadata, and interactive type roles
- surface, text, rule, semantic, and focus color roles
- spacing rhythm, geometry, and artifact framing
- section hierarchy and reading order
- responsive behavior and annotation affordance
- normal-view and Git-focus emphasis

Color communicates state, ownership, category, or warning and is always reinforced by text, position, or line shape.
Normal text and diagram labels meet 4.5 to 1 contrast.
Large text and meaningful graphic boundaries meet 3 to 1.
Muted text remains readable and is never produced with low opacity.

Use an established target visual system when it satisfies these invariants.
The bundled [../assets/style/spec.css](../assets/style/spec.css) is one tested crisp editorial implementation, not the required aesthetic identity.
Use it only when the target has no coherent high-quality spec stylesheet.
Copy it to `docs/specs/.style/spec.css` and link it with the correct relative path.
A root `docs/specs/*.spec.html` page uses `./.style/spec.css`, a nested `docs/specs/domains/*.spec.html` page uses `../.style/spec.css`, and `docs/adr/*.spec.html` uses `../specs/.style/spec.css`.

## Artifact choice

- System topology: directed semantic diagram with labeled seams and dependency direction.
- Guarded state: state machine with guards and meaningful self-loops.
- Lifecycle or ordered progression: rail or numbered sequence.
- Cycle: loop or flywheel with no false first step.
- Layout or placement: proportional wireframe.
- Quantity: ECharts or another quantitative chart with real scale, values, labels, and thresholds.
- Exact mapping or coverage: table with identified rows and explicit cells.
- Peer axioms: identified clause group.
- Formula: KaTeX or readable notation with an adjacent plain-language gloss.

ECharts explains quantities.
Do not use a default graph series for system topology merely because it can draw nodes.
Small exact diagrams may use readable inline SVG with a title and semantic labels.
Beautiful Mermaid is preferred when its semantic source expresses the system cleanly.

## Artifact legibility

At its embedded width, every artifact has:

- collision-free labels
- complete, distinguishable edges
- intentional spacing and grouping
- legible type without zoom
- semantic color plus a non-color cue
- a title or caption stating its contract
- stable anchors on the artifact and meaningful surrounding blocks

If a layout engine cannot meet those conditions, change the layout or renderer rather than shrinking labels until they fit.

## Browser gate

Before publication, inspect the rendered page at desktop and mobile widths in normal and Git-focus modes.
Apply the same gate regardless of the chosen visual system.
Inspect the full-page reading order and each artifact at its actual embedded size.
Reject the page until all of these are true:

- primary, secondary, metadata, and interactive text remain readable
- changed material leads and unchanged context remains usable
- no label, node, edge, caption, table, or control collides or clips
- diagrams remain understandable without prose reconstruction
- review controls do not obscure contract content
- mobile preserves hierarchy and artifact comprehension without horizontal page drift

DOM presence or successful library initialization is not visual proof.
