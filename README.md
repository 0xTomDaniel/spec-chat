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
- A prompt-first shaping skill commits the spec seed locally, opens Git-derived focus through a private review link by default, and keeps the same authoring turn parked through hand-off batches.

## Constraints (fixed)

Agent-agnostic across Claude Code / Codex / pi · plain files + CLI + skills over MCP/hooks/inference services · all inference through the CLI session, except optional Jev typed suggestions called by the review server and off without a key ([jev-suggestions](docs/specs/jev-suggestions.spec.html)) · tiny HTTP file transport only when the browser cannot share the filesystem (loopback on one machine, box-side public service for remote review) · no alt-tabbing to the terminal to trigger the agent.

## Install and onboarding

One entry point, `scripts/install-spec-chat`, run from a Spec Chat clone. The light core below is all most repos need; the hosted review service is an opt-in on top.

### Use it on your repo

From your repo root (a plain folder works too; Git is not required):

```sh
<spec-chat clone>/scripts/install-spec-chat --specs docs/specs
```

This links the skills (`spec-chat-shape`, `spec-chat-review`, `repair-specs`) into `~/.claude/skills` and `~/.codex/skills`, and copies the page runtime (`.viz`) and style (`.style`) into the spec folder. No hosting, keys, or registry.

It prints `spec-chat onboarding pending` and a `next: ... --review ...` hint, and `$XDG_STATE_HOME/spec-chat/onboarding.toml` keeps `status = "pending"`. That is expected on the light path: `done` only means the hosted service is set up.

Open a spec in the browser, either way:

- as a plain file (`file://`): the page asks for write access to the spec folder or an ancestor;
- over loopback, no prompts, any browser:

  ```sh
  python3 ~/.claude/skills/spec-chat-review/assets/review-serve.py docs 0
  ```

  It binds `127.0.0.1` on a free port (`0`) and prints the URL. Stop it when review ends.

Press `C`, comment, and hand off. Then ask your agent to review the spec (the `spec-chat-review` skill). Its loop is plain files (here `c1` and `p1` are the comment and anchor ids from the hand-off):

```sh
R=~/.claude/skills/spec-chat-review/scripts
sh $R/watch-specs.sh docs .cursor-owner 0 3   # one immediate scan: rows <spec> <file name>
sh $R/emit-reply.sh docs/specs/example.spec.html.review/ c1 p1 '{"type":"text","key":"downloads a CSV"}' acknowledged 'no spec change' 'Date, item, total.'
printf '%s\n' <file names printed by the scan above> >> docs/specs/example.spec.html.review/.cursor-owner
```

The scan's `0` is the wait (none) and `3` the poll interval; exit status 3 means nothing is pending. After replying, append exactly the file names the scan printed to the cursor of the spec in the scan's first column (`<spec>.review/.cursor-owner`). Never rescan into the cursor: hand-offs that arrived in between would be marked seen unprocessed.

The agent runs these for you; they are shown so you can see nothing else is involved.

Caveats:

- `watch-specs.sh` needs `python3` (standard library only).
- Marks follow agent edits only over HTTP; loopback is enough. On `file://` a mark places by its anchor and quote.
- Jev suggestions are off without a key ([jev-suggestions](docs/specs/jev-suggestions.spec.html)).

A rerun changes nothing. `--undo` removes only what earlier runs added (recorded in `$XDG_STATE_HOME/spec-chat/install.toml`).

### Optional: hosted review service

For reviewers on other machines: box hosting, the service registry, owner wake, Jev suggestions, and live multi-reviewer review. Opt-in steps:

1. `install-spec-chat --specs <spec folder> --review <first .spec.html>` starts or joins the box's review service with that spec, prints its URL, and sets `status = "done"`. The spec must be in a Git repository.
2. Box setup, by the box owner, once: add `--public <host>`, `--private`, or `--proof-host <host>` to step 1. Lanes never pass them.
3. Owner wake and Jev suggestions are providers the service reads from its state ([Providers](docs/specs/review-service.spec.html#providers)).

Canonical contract: [remote hosting lifecycle](skill/review-spec/SKILL.md#remote-hosting-lifecycle).

- The box hosts two independent services only: Spec Chat review (this repo) and the evidence provider named in its `providers/evidence.toml` ([Providers](docs/specs/review-service.spec.html#providers)).
- Each service has its own launcher, process, approved-port discovery, collision-safe binding, narrow root, public URL, exact served-byte check, and baseline check. Starting, failing, or stopping one never touches the other.
- A lane runs only `skill/review-spec/scripts/review-host.py register --slug <lane-key> --owner <pane> <spec path>`, repeating the spec path for more specs. A plain spec path infers project, root, and base from Git; `--base <ref>` overrides the base; the `PROJECT_ID=ROOT:SPEC_PATH@BASE` form still works. It names no exposure. It checks exact spec bytes and `/api/baseline` on the box before printing the URL.
- Box setup, by the box owner and outside any lane: `--public <host>`, `--private`, and `--proof-host <host>`, each once, through `scripts/install-spec-chat`. Approved ports come from `SPEC_CHAT_APPROVED_INGRESS_PORTS` or readable host firewall rules. The service listens on loopback by default (reach it with `ssh -L`); public binds an approved port with a no-login warning, and the registry keeps that choice across restarts. `review-host.py --state-dir` names a separate, isolated service for tests or deliberate isolation; lanes do not pass it.
- Ordinary edits to a served spec keep the same server and URL alive. Rerun exact served-byte and `/api/baseline` checks for the same selected base after each edit. Restart only when the root, collection, process, port, runtime, or ownership changes, or when the server is dead.
- Shared helper code is allowed only when service-neutral.
- BB is a laptop client. Box hosting boot never requires, installs, or starts BB. BB consumes two public URLs: the Spec Chat URL and the evidence URL.
- No external probe, tunnel, VPN, or client-machine setup. Public URLs are not authentication boundaries and never enter Linear, pull requests, or other public durable records.

## Writing a project rule

A project rule is an acceptance criterion that applies to every feature, such as *"When any change adds a screen or alters what a user can do, the change ships with its help center article."* Jev finds these automatically and checks every spec under shaping against them ([project-wide rules](docs/specs/project-rules.spec.html)).

To make a criterion a project rule, include explicit every-feature wording in the criterion text: *every feature*, *every change*, *any change*, or equivalent. Jev reads the text and decides; no tag, list, or declaration is needed.

## Repo layout

```
docs/specs/              canonical *.spec.html, one per capability
  .viz/                  shared runtime + vendored libs
  .style/                shared visual-spec styles
docs/adr/                architecture decision records
skill/shape-spec/        shaping skill, validator, references
skill/review-spec/       review skill, review server, watch and reply scripts
skill/repair-specs/      spec repair skill
scripts/install-spec-chat  install and onboarding entry point
tests/                   all tests (Python, Node, shell)
qa.toml                  QA capture target
DESIGN.md                consensus design document
```

## License

Licensed under the Apache License, Version 2.0 — see [LICENSE](LICENSE).
