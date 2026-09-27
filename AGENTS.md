# AGENTS.md

Spec Chat: visual HTML specs annotated in the browser, addressed by a coding
agent. This file owns repo commands, test runner, validators, spec format,
and conventions.

## Repo layout

```
docs/specs/          canonical *.spec.html, one per capability
docs/specs/.viz/     shared runtime + vendored libs
docs/specs/.style/   shared visual-spec styles
skill/shape-spec/    shaping skill, validator, references
skill/review-spec/   review skill, review-host, watch, server assets
scripts/             install-spec-chat (onboarding entry point)
tools/               byte-identical copies of skill/review-spec/assets/*.py
tests/               all tests (Python, Node, shell)
DESIGN.md            consensus design document
```

## Commands

### Run all tests (serial, CI-identical)

```sh
fail=0
for f in tests/*.py tests/*.sh tests/*.mjs; do
  case "$f" in
    *.py)  run="python3 $f" ;;
    *.sh)  run="sh $f" ;;
    *.mjs) run="node $f" ;;
  esac
  if $run; then echo "ok $f"; else echo "FAIL $f"; fail=1; fi
done
exit $fail
```

Tests run serially, one process at a time. No parallel runner; order is
lexicographic within each glob. CI uses exactly this loop
(`.github/workflows/test.yml`).

### Run a single test

```sh
# Python (unittest)
python3 tests/<name>.py

# Node
node tests/<name>.mjs

# Shell
sh tests/<name>.sh
```

### Validate a spec

```sh
python3 skill/shape-spec/scripts/validate-style.py <repo-root> <spec.html> <base-ref>
```

Structural gate for shaped specs: checks style provenance, story declarations,
anchor coverage, and section contract. Run after every spec commit; stop on
failure.

## Browser tests

Two tests need headless Chromium via `playwright-core`:

- `tests/runtime-pin-marker.mjs` (pin placement and clearance)
- `tests/runtime-jev-popover.mjs` (Jev popover positioning)

They read `PLAYWRIGHT_CORE` to locate the package:

```sh
PLAYWRIGHT_CORE=/path/to/node_modules/playwright-core node tests/runtime-pin-marker.mjs
```

Install `playwright-core` once per box and point `PLAYWRIGHT_CORE` at it. CI
installs its own (`npm install --no-save --prefix /tmp/pw playwright-core` then
`playwright-core install --with-deps chromium`).

All other `runtime-*.mjs` tests run in Node without a browser.

## Tool and runtime copies

`tools/*.py` must be byte-identical to `skill/review-spec/assets/*.py`.
`docs/specs/.viz/runtime.js` must be byte-identical to
`skill/review-spec/assets/viz/runtime.js`. Both invariants are tested by
`tests/tool-copies.sh` and `tests/review-spec-scripts.sh`.

## Spec format

`Spec format: spec-chat`

Canonical specs are `*.spec.html` under `docs/specs/`. The HTML is the spec;
no Markdown counterpart. Shaping uses `skill/shape-spec/`, review uses
`skill/review-spec/`. Specs describe target state only; no current-state
audits, status lines, ticket links, or migration notes. Behavior belongs in
the spec that already owns it.

## Conventions

- Specs and ADRs name no ticket and no pane.
- One mechanism: extend the existing one before adding a second; a second
  needs a stated reason in the spec.
- No tracker behavior enters Spec Chat (skills, runtime, transport, protocol).
- Shaping commits locally only; pushing, PRs, and issue work belong to the
  caller.
- Review spools (`*.review/`) are gitignored.
- Python tests use `unittest`; run from repo root with
  `PYTHONDONTWRITEBYTECODE=1`.
- Node runtime tests use `node:assert/strict` and `node:test` or standalone
  assertion scripts.
- Shell tests use `set -eu` and exit nonzero on failure.
