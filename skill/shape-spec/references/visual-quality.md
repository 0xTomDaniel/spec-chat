# Visual quality

Treat the spec as an editorial visual contract, not prose placed inside cards.
Libraries are implementation choices made after the information and composition plan.

## Plan before authoring

Create a small working map for every major behavior cluster:

- information shape
- least-lossy artifact
- visual role in the page
- observable proof that the artifact is legible and complete

Establish the reading order before styling: masthead and authority, governing clauses, explanatory figures, detailed interfaces and failures, acceptance, then sources.
Dense specs need visual coverage proportional to their real relationships.
A long multi-section contract with one token chart requires either more load-bearing artifacts or a recorded reason each remaining cluster is least-lossy as prose or a table.

## Positive correction map

- Library-list drift: begin with the artifact and hierarchy plan, then select the smallest suitable renderer.
- Token visual: give each relationship-bearing cluster its appropriate diagram, rail, wireframe, table, formula, or identified clause group.
- Invented pale theme: reuse the target's proven spec language or the bundled high-contrast editorial stylesheet.
- Render-only QA: inspect the complete rendered page and every artifact for hierarchy, contrast, collision, clipping, and review-control obstruction.
- Weak focus base: evaluate normal and Git-focus views together so changed content leads while unchanged context remains readable.

## Editorial visual language

Prefer:

- near-black ink on warm light paper
- one to three restrained semantic accents
- strong rules and deliberate whitespace
- a large masthead with compact metadata
- numbered sections, identified clauses, figures, and captions
- square or lightly rounded geometry
- monospace for identifiers, formulas, state labels, and metadata

Color communicates state, ownership, category, or warning and is always reinforced by text, position, or line shape.
Normal text and diagram labels meet 4.5 to 1 contrast.
Large text and meaningful graphic boundaries meet 3 to 1.
Muted text remains readable and is never produced with low opacity.

Use the bundled [../assets/style/spec.css](../assets/style/spec.css) only when the target has no established high-quality spec stylesheet.
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
Inspect the full-page reading order and each artifact at its actual embedded size.
Reject the page until all of these are true:

- primary, secondary, metadata, and interactive text remain readable
- changed material leads and unchanged context remains usable
- no label, node, edge, caption, table, or control collides or clips
- diagrams remain understandable without prose reconstruction
- review controls do not obscure contract content
- mobile preserves hierarchy and artifact comprehension without horizontal page drift

DOM presence or successful library initialization is not visual proof.
