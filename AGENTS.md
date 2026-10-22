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
scripts/             install-spec-chat (onboarding entry point), design-set-sync
design-set.toml      the one pin of the shared design set
tests/               all tests (Python, Node, shell)
DESIGN.md            consensus design document
```

## Commands

### Run all tests (serial, CI-identical)

```sh
export SPEC_CHAT_TEST_TMP="$(cd "$(mktemp -d /tmp/spec-chat-tests.XXXXXX)" && pwd -P)"
export TMPDIR="$SPEC_CHAT_TEST_TMP"
trap 'sh tests/leftover-processes; rm -rf "$SPEC_CHAT_TEST_TMP"' EXIT
trap 'exit 130' INT TERM
fail=0
for f in tests/*.py tests/*.sh tests/*.mjs; do
  case "$f" in
    *.py)  run="python3 $f" ;;
    *.sh)  run="sh $f" ;;
    *.mjs) run="node $f" ;;
  esac
  if $run; then echo "ok $f"; else echo "FAIL $f"; fail=1; fi
  sh tests/leftover-processes || { echo "FAIL $f left processes running"; fail=1; }
done
exit $fail
```

Tests run serially, one process at a time. No parallel runner; order is
lexicographic within each glob. CI uses exactly this loop
(`.github/workflows/test.yml`). Every temp file of the run lives in one run
directory, so a server a test starts names it on its command line;
`tests/leftover-processes` fails the test file that leaves one running, stops
it, and runs again on exit or interrupt. A test stops what it starts, also
when it fails.

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

## QA capture

`qa.toml` at the repo root declares the QA capture target: the review server on the `tests/fixtures/qa/` fixture, and that fixture's test host (`host.py`), which serves its pages at another address in a frame with no origin, or unframed at its own origin. The comment-layer collections hold host pages that load the layer (`panes.py`).

## Browser tests

Eight tests need headless Chromium via `playwright-core`:

- `tests/runtime-pin-marker.mjs` (pin placement and clearance)
- `tests/runtime-jev-popover.mjs` (Jev popover positioning)
- `tests/runtime-comment-target.mjs` (comment-mode block buttons and keyboard activation)
- `tests/runtime-review-live.mjs` (shared review state between reviewers' pages)
- `tests/runtime-review-place.mjs` (marks through agent edits on the QA fixture collections)
- `tests/runtime-answer-buttons.mjs` (answer buttons save a draft comment and show the chosen mark)
- `tests/runtime-served-host.mjs` (a page served at another address, in a frame with no origin, through the QA fixture's test host)
- `tests/runtime-comment-layer.mjs` (the comment layer on the QA fixture's host pages: served directly, at another origin, and in a frame with no origin)

They read `PLAYWRIGHT_CORE` to locate the package:

```sh
PLAYWRIGHT_CORE=/path/to/node_modules/playwright-core node tests/runtime-pin-marker.mjs
```

Install `playwright-core` once per box and point `PLAYWRIGHT_CORE` at it. CI
installs its own (`npm install --no-save --prefix /tmp/pw playwright-core` then
`playwright-core install --with-deps chromium`).

All other `runtime-*.mjs` tests run in Node without a browser.

## Runtime copies

`docs/specs/.viz/runtime.js` must be byte-identical to
`skill/review-spec/assets/viz/runtime.js`. This invariant is tested by
`tests/review-spec-scripts.sh`.

## Design set

`design-set.toml` pins the shared design set (tokens, status marks, line icons) at one commit. `scripts/design-set-sync
[CLONE]` builds it into the marked design-set region of `skill/review-spec/assets/viz/runtime.js` (the chrome CSS; the
review index serves that same block) and of `skill/shape-spec/assets/style/spec.css` (the document's `--spec-*`
values); `--check` only reports. Never edit a region by hand. Move the set only by changing the pin, then sync and copy
both files to `docs/specs/.viz/` and `docs/specs/.style/`. `tests/design-set.py` checks the copies offline, and against
the pinned commit when `DESIGN_SET_CLONE` names a clone holding it. Nothing fetches the set at run time.

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
