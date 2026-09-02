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

## Browser gate

Before publication, inspect the complete rendered page and every artifact at desktop and mobile widths in normal and Git-focus modes.
Reject it until hierarchy and contrast are clear, changed material leads, unchanged context remains usable, nothing collides or clips, diagrams need no prose reconstruction, review controls obscure nothing, and mobile has no horizontal page drift.
DOM presence or successful library initialization is not visual proof.

## Technology doctrine

Canonical `*.spec.html` uses NON REACT.
REACT is limited to an existing embedded application or an explicitly noncanonical throwaway prototype.

NON REACT
Core
Tailwind CSS v4 — styling
daisyUI + daisyUI Skill — components and themes
Alpine.js — page state and UI behavior
PixiJS + PixiJS Skills — interactive visual scenes
GSAP — animation and choreography
ECharts — charts and quantitative data visualization
KaTeX — mathematical notation
Beautiful Mermaid — semantic diagrams
Specialists
JSXGraph — precise interactive math and geometry
Paper.js — vector/path manipulation
Three.js — 3D
LiquidGlass — selective premium surface effects
The updated doctrine:
HTML and Alpine control the page.
Tailwind and daisyUI define the interface.
PixiJS defines the visual world.
GSAP defines time.
ECharts explains data.
KaTeX expresses math.
Beautiful Mermaid explains systems.
REACT

Core
React 19 + Vite
Tailwind v4
shadcn/ui + Base UI
@pixi/react
+ PixiJS Skills
GSAP +
@gsap/react
Apache ECharts
KaTeX
Beautiful Mermaid
Interactive specialists
Mafs — interactive math
React Flow — interactive node/system diagrams
React Three Fiber + Drei — 3D
react-konva — interactive 2D canvas/vector UI
Paper.js — serious path/Bézier work
LiquidGlass — selective visual effect
And there's an interesting consequence:
Static version
Alpine + daisyUI + PixiJS
is extraordinarily lightweight and agent-friendly.
React version
React + shadcn + @pixi/react + Mafs + React Flow + R3F
has a much richer ecosystem for Brilliant-like interactive visual education.
