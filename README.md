# Spec Chat

Visual HTML specs you annotate in the browser; a coding agent addresses the annotations and edits the spec in place. Discussion happens *on* the visualization, not in chat prose.

**Status:** design phase. Canonical design doc: [DESIGN.md](DESIGN.md) · clickable UX mockup: [mockups/spec-review-ux.html](mockups/spec-review-ux.html)

## The idea in one pass

- Specs are visual HTML documents (charts, diagrams, math — semantic islands, not rendered debris). The HTML **is** the spec — no markdown counterpart, no sync loop.
- Open a spec as a plain file, press `C`, and annotate **anything on the page** — a chart bar, an axis tick, a diagram arrow, the title, the divider under it. Only the commenting shell itself is exempt.
- Annotations land in actor-segregated event spools (`spec.html.review/human/`, `agent/` — one file per event; no shared writable file, ever).
- One parked CLI watcher (Claude Code, Codex CLI, or pi) covers the whole spec collection by default: it discovers per-page hand-off spools, drains batches serially with independent cursors/session state, edits the selected spec, and writes replies back. In-session subscription inference; no MCP, no hooks, no server.

## Constraints (fixed)

Agent-agnostic across Claude Code / Codex / pi · plain files + CLI + skills over MCP/hooks/servers · all inference through the CLI session · no alt-tabbing to the terminal to trigger the agent.

## Repo layout (planned)

```
specs/            example/dogfood specs, one per capability
  .viz/           shared runtime + vendored libs (ECharts, Mermaid, pin runtime)
specs/.viz/       runtime (island hydration + annotation layer + transports) + vendored libs
skill/            the review-spec agent skill + polling script
mockups/          UX mockups
DESIGN.md         consensus design (Claude ↔ GPT-5.5 adversarial review, 3 rounds)
```

## License

Licensed under the Apache License, Version 2.0 — see [LICENSE](LICENSE).
