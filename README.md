# Spec Chat

Visual HTML specs you annotate in the browser; a coding agent addresses the annotations and edits the spec in place. Discussion happens *on* the visualization, not in chat prose.

**Status:** design phase. Product design history: [DESIGN.md](DESIGN.md) · canonical prompt-first shaping contract: [docs/specs/prompt-first-shaping.spec.html](docs/specs/prompt-first-shaping.spec.html)

## The idea in one pass

- Specs are visual HTML documents (charts, diagrams, math — semantic islands, not rendered debris). The HTML **is** the spec — no markdown counterpart, no sync loop.
- Open a spec as a plain file, press bare `C`, and annotate **anything on the page** — a chart bar, an axis tick, a diagram arrow, the title, the divider under it. Clipboard shortcuts such as `Ctrl+C` and `Command+C` remain untouched.
- Annotations land in actor-segregated event spools (`spec.html.review/human/`, `agent/` — one file per event; no shared writable file, ever).
- A compact floating dock shows one status-colored square per conversation; selecting a square opens that thread, while comment mode or the dock’s chat control opens the full review sidebar.
- Threads support human↔agent follow-up replies and append-only edits to unanswered human messages; selecting a thread rings the exact page element it annotates, and resolved threads collapse automatically while remaining browsable.
- One parked CLI watcher (Claude Code, Codex CLI, or pi) covers the whole spec collection by default: it discovers per-page hand-off spools, drains batches serially with independent cursors/session state, edits the selected spec, and writes replies back. In-session subscription inference; no MCP, hooks, inference service, or mandatory daemon.
- A prompt-first shaping skill creates the durable issue and spec seed, opens Git-derived focus through an unguessable public review link, and keeps the same authoring turn parked through hand-off batches.

## Constraints (fixed)

Agent-agnostic across Claude Code / Codex / pi · plain files + CLI + skills over MCP/hooks/inference services · all inference through the CLI session · tiny HTTP file transport only when the browser cannot share the filesystem (loopback on one machine, box-side public service for remote review) · no alt-tabbing to the terminal to trigger the agent.

## Remote hosting

Canonical contract: [docs/specs/remote-handoff-proof.spec.html](docs/specs/remote-handoff-proof.spec.html) (`#topology`).

- The box hosts two independent services only: Spec Chat review (this repo) and annotateanything evidence (peer repo).
- Each service has its own launcher, process, lifecycle, approved-port discovery, collision-safe binding, narrow root, secret URL, exact served-byte check, and baseline check. Starting, failing, or stopping one never touches the other.
- Spec Chat starts with `skill/review-spec/scripts/launch-review-serve.sh <narrow-collection> <spec-path> <exact-base>`. Approved ports come from `SPEC_CHAT_APPROVED_INGRESS_PORTS` or readable host firewall rules. It checks exact spec bytes and `/api/baseline` on the box before printing the secret URL.
- Ordinary edits to a served spec keep the same server and URL alive. Rerun exact served-byte and `/api/baseline` checks for the same selected base after each edit. Restart only when the root, collection, process, port, runtime, or ownership changes, or when the server is dead.
- Shared helper code is allowed only when service-neutral.
- BB is a laptop client. Box hosting boot never requires, installs, or starts BB. BB consumes two public URLs: the Spec Chat URL and the evidence URL.
- No external probe, tunnel, VPN, or client-machine setup. Secret URLs never enter Linear, pull requests, or other public durable records.

## Repo layout (planned)

```
docs/                   shared review collection root
  specs/                visual product/domain specs, one per capability
    .viz/                shared runtime + vendored libs
    .style/              shared visual-spec styles
  adr/                   visual architecture decision records
skill/                   prompt-first shaping and review skills + focused references/scripts
DESIGN.md                consensus design (Claude ↔ GPT-5.5 adversarial review, 3 rounds)
```

## License

Licensed under the Apache License, Version 2.0 — see [LICENSE](LICENSE).
