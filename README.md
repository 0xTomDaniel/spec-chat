# Spec Chat

Visual HTML specs you annotate in the browser; a coding agent addresses the annotations and edits the spec in place. Discussion happens *on* the visualization, not in chat prose.

**Status:** design phase. Product design history: [DESIGN.md](DESIGN.md) · canonical prompt-first shaping contract: [docs/specs/prompt-first-shaping.spec.html](docs/specs/prompt-first-shaping.spec.html) · clickable UX mockup: [mockups/spec-review-ux.html](mockups/spec-review-ux.html)

## The idea in one pass

- Specs are visual HTML documents (charts, diagrams, math — semantic islands, not rendered debris). The HTML **is** the spec — no markdown counterpart, no sync loop.
- Open a spec as a plain file, press bare `C`, and annotate **anything on the page** — a chart bar, an axis tick, a diagram arrow, the title, the divider under it. Clipboard shortcuts such as `Ctrl+C` and `Command+C` remain untouched.
- Annotations land in actor-segregated event spools (`spec.html.review/human/`, `agent/` — one file per event; no shared writable file, ever).
- A compact floating dock shows one status-colored square per conversation; selecting a square opens that thread, while comment mode or the dock’s chat control opens the full review sidebar.
- Threads support human↔agent follow-up replies and append-only edits to unanswered human messages; selecting a thread rings the exact page element it annotates, and resolved threads collapse automatically while remaining browsable.
- One parked CLI watcher (Claude Code, Codex CLI, or pi) covers the whole spec collection by default: it discovers per-page hand-off spools, drains batches serially with independent cursors/session state, edits the selected spec, and writes replies back. In-session subscription inference; no MCP, hooks, inference service, or mandatory daemon.
- A prompt-first shaping skill creates the durable issue and spec seed, opens Git-derived focus through an unguessable public review link, and keeps the same authoring turn parked through hand-off batches.

## Constraints (fixed)

Agent-agnostic across Claude Code / Codex / pi · plain files + CLI + skills over MCP/hooks/inference services · all inference through the CLI session · tiny loopback file transport only when the browser cannot share the filesystem · no alt-tabbing to the terminal to trigger the agent.

## Repo layout (planned)

```
docs/                   shared review collection root
  specs/                visual product/domain specs, one per capability
    .viz/                shared runtime + vendored libs
    .style/              shared visual-spec styles
  adr/                   visual architecture decision records
skill/                   prompt-first shaping and review skills + focused references/scripts
mockups/                 UX mockups
DESIGN.md                consensus design (Claude ↔ GPT-5.5 adversarial review, 3 rounds)
```

## License

Licensed under the Apache License, Version 2.0 — see [LICENSE](LICENSE).
