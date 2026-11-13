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
cleanup() {
  sh tests/leftover-processes
  python3 -c 'import os, shutil; d = os.environ["SPEC_CHAT_TEST_TMP"]; os.path.dirname(d) == os.path.realpath("/tmp") and shutil.rmtree(d)'
}
trap cleanup EXIT
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
when it fails. On exit `python3` removes the run directory, only one directly
in `/tmp`; `rm -rf` on a variable path stops agents on a permission prompt.

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

`qa.toml` at the repo root declares the QA capture target: the review server on the `tests/fixtures/qa/` fixture, a second review server on that fixture, and that fixture's test host (`host.py`), which serves either server's pages at another address in a frame with no origin, or unframed at its own origin, and on `/named/` names its frame and clears the name once the page has loaded. The rule-pending collection's report spec gains a sentence whose rule check the fixture's general LLM holds unanswered until the service stops, then records nothing, so its rule check stays pending while the page is open; rule-answer's gains another, whose rule check it answers a few seconds after its first ask (`spools.py` HELD, `serve.py`). The rule-main-only collection, its own project, registers the report spec alone, its origin/main the base commit, which holds the onboarding rule's home, so only its project's main holds that home, while qa-fixture's lane serves it, as qa-1 and the other collections registering the four specs do (`reset.sh`). The comment-layer collections hold host pages that load the layer (`panes.py`). The mockup collections register `report-page.spec.html`, whose mockup set lists the fixture repo's data (`fixtures/weekly-totals.json`) in the pinned design set's tokens (`reset.sh`, `spools.py`); the walkthrough collections add drawn screens whose style draws each step from `data-at`, Team panel walking three steps and Export flow six, some with questions on the mockup (`spools.py` WALKTHROUGHS, `walkthrough.mockup.template.html`), and the one-screen collection's set holds Report page alone (`spools.py` ONE_SCREEN). The threads collections seed the conversations `threads.spec.html`'s criteria start from (`spools.py` THREADS); the stage lanes and host pages, the stage the box names for each lane, which `serve.py` tells a service with no evidence provider and the evidence service declares (`spools.py` STAGES, `panes.py` STAGE); the video ones and `layer-video`'s host page show a short video of the report page (`report-video.spec.html`, `panes.py` VIDEO), the first round's recording (`videos/`). The test shell (`shell.py`) holds the review server's pages beside its comment UI page, or its own native page, a factory floor drawn on a canvas that speaks the shell messages with no Spec Chat script and makes a picture of a boxed spot; the review server's registry names its origin as a shell, and a second test shell at another origin is named nowhere; the shell collections seed what `shell.spec.html`'s criteria start from (`spools.py` SHELL, `panes.py`). The fixture's running prototype (`prototype.py`), a qa.toml service on loopback, serves the weekly report route only at its root, with helmet's default policy and frame options, its page loading its script and style by absolute paths and opening the team its address names, and its team table at another route; the prototype collections' rows name it, as the lane command's `--prototype` does, so a review server of their own, private on loopback, serves it at each one's prototype origin, `<slug>-prototype.localhost` at its port, and their mockup set adds a prototype mockup of it committed in two rounds, each round's videos in its spool (`spools.py` PROTOTYPES, `videos/`); prototype-own-chrome's row names that review server itself, the lane's own review service as its prototype, and its set adds a prototype mockup of its Report spec page (`spools.py` OWN_CHROME). The directions collections show a first round's two directions: in the prototype ones that mockup names the team table's route as its other direction, with no round video, and in the drawn one Team panel draws the table's own frames beside its own (`spools.py` PROTOTYPE_DIRECTIONS, DRAWN_DIRECTIONS); the kept one shows the round after a Pick of the table handed off and answered, that mockup the table's route alone (`spools.py` KEPT_DIRECTIONS).

## Browser tests

Seventeen tests need headless Chromium via `playwright-core`:

- `tests/runtime-pin-marker.mjs` (pin placement and clearance)
- `tests/runtime-jev-popover.mjs` (Jev popover positioning)
- `tests/runtime-comment-target.mjs` (comment-mode block buttons and keyboard activation)
- `tests/runtime-review-live.mjs` (shared review state between reviewers' pages; a typed comment kept as a draft as the page is left by its back link and shown on coming back)
- `tests/runtime-review-place.mjs` (marks through agent edits on the QA fixture collections)
- `tests/runtime-answer-buttons.mjs` (answer buttons save a draft comment and show the chosen mark)
- `tests/runtime-served-host.mjs` (a page served at another address, in a frame with no origin, through the QA fixture's test host; at phone width, the review panel's one hand-off bar in the bottom toolbar, served directly and in that frame across a reload; the frame's reviewer and unsent changes across two review services, and in a frame the host names)
- `tests/runtime-comment-layer.mjs` (the comment layer on the QA fixture's host pages: served directly, at another origin, and in a frame with no origin; each item's row at its own corner, scrolled, after an item above it grows, and in an app shell when the host shows an element above it)
- `tests/runtime-spec-surface.mjs` (the spec page as one surface on the QA fixture's collections: top line, unboxed header, keys in tooltips, line icons, sentence-case pills; the reader's place across reloads and an address anchor under the top line; the error overlay at the top before the chrome mounts, then under the top line on a desktop and a phone, one counted line per distinct error, the page following its height, the phone's Hand off in full, hidden under the open phone panel; an embed host and file:// without the top line, the overlay at the top and the page and dock under it)
- `tests/runtime-review-index.mjs` (the review index as quiet rows on the QA fixture's review services: status marks, title column, gate word under the title on a phone, dark, focus ring; Find on review-sections: by title, spec name, and lane key, no match, Escape, emptying, reload, and on a phone)
- `tests/runtime-spec-mockups.mjs` (the spec page over its mockup set, on a desktop and a phone: See mockup chips and opening the mockup at the illustrating part or step; the Screens index under the header, its rows, and opening a screen from it; Accept spec waits on open mockup threads and questions, which the panel lists as links, and names each mockup with its version)
- `tests/runtime-conversation.mjs` (threads as one conversation on the QA fixture's threads collections, on a desktop and a phone: the stream's order, sides, chips, folded replies, the focused thread in the panel and as a sheet, receipts, waiting dots, question bubbles, pin and receipt colors, and an anchored item's conversation through the comment layer; comments at a moment of a video, with a dragged box, its chip and rail returning to it, on a spec page and through the comment layer; each message's stage tag beside its author, on a lane's page and a host page's item naming a lane, and a reply taking its thread's lane)
- `tests/runtime-mockup-page.mjs` (a mockup page over its own fixture mockup sets, on a desktop and a phone: frames side by side or stacked, each scaled whole; any element or an anchored region as a comment target; the one hand-off control; the owner's reply; the agent-edit banner and marks that follow the edit; Before and After against the base, New since the base, and an unchanged mockup; arrival at a part ringed; a walkthrough's step bar, Play, a step pressed or reached by pressing a part, and the phone step bar; threads at a step and their chips; questions on the mockup, their marks, Next TBD, and answers by a button or in words, and a question's sheet on a phone; Previous and Next around the screen's place in its set, by press, tap, and arrow key, on a phone at the end of the row under the range, with and without Before and After, and the arrow keys left to a field, the step bar, and a video; a typed comment, reply, and Reply and reopen kept as drafts however the page is left, by Previous, Next, an arrow key, the spec link, or Back, and an Edit left as it opened saving nothing; a set of one and a screen added)
- `tests/runtime-prototype.mjs` (a running prototype as a screen's mockup, through the QA fixture's prototype collections and its running prototype: the route framed at desktop and phone width at the lane's own prototype origin through the review service's prototype proxy, held across the two origins by the shell messages; a product serving its page, script, and style only at its root, styled and running, under helmet's default policy and one allowing no inline style; a step reached by a page a step's action loads, by the step bar and pressing through; a front that does not answer the prototype origin; the lane's own review service run as its prototype, its spec page held by its own runtime with no review panel or toolbar, and a comment on a criterion pinning it; a comment on a button at a step, pressing through to the next step, Prototype not running, Prototype updated by agent, and each round's videos under the frames with Earlier rounds and Before, a comment at a moment of a round's video, on a desktop and a phone)
- `tests/runtime-shell.mjs` (pages a shell holds, through the QA fixture's test shell at an origin the fixture box names and at another: a held spec page, mockup page, and host page beside the comment UI page, on a desktop and a phone and in frames with no origin; the shell messages and their fields; pick, show, marks, Hand off, Accept spec, answers, close, and a late hello; a held page's video picked at a moment and with a box, and shown there; a box on a held host page's thing shown there; the shell's native canvas page: its thing's conversation and pins, a box dragged across it with its picture, kept and shown in the focused thread, loaded by its file)
- `tests/runtime-directions.mjs` (a screen's first round showing its directions, through the QA fixture's directions collections, its running prototype, and a drawn screen, on a desktop and a phone: side by side, each under its name and gives-up line with the agent's pick and its reason; the Desktop and Phone toggle, and a part the address names or the kept place across a reload shown at its viewport; Pick saving the reviewer's answer on the directions list, Your pick, the newest standing across a reload, and the answer in the panel; the other direction taking no comment and no step, by pointer or keyboard; Accept spec with no Pick pressed; the Screens index's directions count)
- `tests/runtime-mockup-settle.mjs` (fitting that settles in one pass on the QA fixture's drawn and prototype mockup pages, a prototype's route sizing its frame from its own origin by the shell messages, with a desktop browser's scrollbars shown: no error overlay as the window narrows to phone width and widens again, the review panel opens and closes, the step bar or a press moves the frames through a walkthrough, a prototype's content grows past the screen, and its root is as tall as its viewport inside the body's margin, settling at the screen's height plus that excess; a route with no doctype; content shrinking again inside a root as tall as its viewport and in a route with no doctype; a route its script sizes from its viewport, by innerHeight, a --vh property, or the visual viewport, settling the same, as tall again once loaded at an earlier step, and following content that grows on its own; a load of another origin leaving no gap under its frame; the frames side by side, each scaled whole)

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

Preflight migrates a target's runtime only when it lacks a capability the
runtime's `spec-chat-capabilities` line declares. A runtime change that gives
target pages a capability adds it to that line.

## Design set

`design-set.toml` pins the shared design set (tokens, status marks, line icons, Find field) at one commit.
`scripts/design-set-sync [CLONE]` builds it into the marked design-set regions of `skill/review-spec/assets/viz/runtime.js`
(the chrome CSS, which the review index serves as the same block, the `ICONS` map of the set's line icons the chrome
draws, the `MARKS` map of the set's status marks the review index and the Screens index draw, and the `FIND` map of the
set's Find field, its markup, style, and script, which the review index builds in and no spec page draws; the index
reads each region with the sync's own reader, which `review-serve.py` loads once as `_SYNC`: tests read the set through
it, browser tests through `tests/fixtures/design-set.py`) and of
`skill/shape-spec/assets/style/spec.css` (the document's `--spec-*` values); `--check` only reports. Never edit a
region by hand. Move the set only by changing the pin, then sync and copy both files to `docs/specs/.viz/` and
`docs/specs/.style/`. `tests/design-set.py` checks the copies offline, and against
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
