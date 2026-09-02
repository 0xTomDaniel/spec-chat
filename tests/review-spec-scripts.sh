#!/bin/sh
set -eu

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
SCRIPTS="$ROOT/skill/review-spec/scripts"

cmp "$ROOT/skill/review-spec/assets/viz/runtime.js" "$ROOT/docs/specs/.viz/runtime.js" >/dev/null || {
  echo "dogfood runtime differs from the packaged review runtime" >&2
  exit 1
}
cmp "$ROOT/skill/review-spec/assets/review-serve.py" "$ROOT/tools/review-serve.py" >/dev/null || {
  echo "dogfood review server differs from the packaged review server" >&2
  exit 1
}

TMP=$(mktemp -d "${TMPDIR:-/tmp}/spec-chat-tests.XXXXXX")
TMP=$(CDPATH= cd "$TMP" && pwd)
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

PAGE="$TMP/docs/research.whitepaper.html"
REVIEW="$PAGE.review"
mkdir -p "$REVIEW/human" "$REVIEW/agent"
: > "$PAGE"
: > "$REVIEW/human/090-comment-test.json"
: > "$REVIEW/human/100-handoff-test.json"
: > "$REVIEW/human/110-reply-test.json"
: > "$REVIEW/human/120-handoff-test.json"
: > "$REVIEW/human/130-comment-awaiting-handoff.json"

CURSOR="$REVIEW/.cursor-test"
[ ! -e "$CURSOR" ] || {
  echo "zero-wait recovery fixture unexpectedly started with a cursor" >&2
  exit 1
}

READY=$("$SCRIPTS/watch-specs.sh" "$TMP" .cursor-test 0 1)
EXPECTED=$(printf '%s\t%s\n%s\t%s\n%s\t%s\n%s\t%s' \
  "$PAGE" '090-comment-test.json' \
  "$PAGE" '100-handoff-test.json' \
  "$PAGE" '110-reply-test.json' \
  "$PAGE" '120-handoff-test.json')
[ "$READY" = "$EXPECTED" ] || {
  echo "zero-wait watch did not recover every completed batch from a non-spec HTML spool" >&2
  exit 1
}
[ ! -e "$CURSOR" ] || {
  echo "zero-wait scan created or mutated a missing cursor" >&2
  exit 1
}

printf '%s\n' "$READY" | cut -f2- >> "$CURSOR"
CURSOR_AFTER_FIRST=$(cat "$CURSOR")
set +e
EMPTY=$("$SCRIPTS/watch-specs.sh" "$TMP" .cursor-test 0 1)
EMPTY_RC=$?
set -e
[ "$EMPTY_RC" -eq 3 ] && [ -z "$EMPTY" ] || {
  echo "zero-wait watch did not report an empty reconciled backlog" >&2
  exit 1
}
[ "$(cat "$CURSOR")" = "$CURSOR_AFTER_FIRST" ] || {
  echo "empty zero-wait scan mutated the cursor" >&2
  exit 1
}

: > "$REVIEW/human/200-edit-test.json"
: > "$REVIEW/human/210-handoff-test.json"
RECOVERED=$("$SCRIPTS/watch-specs.sh" "$TMP" .cursor-test 0 1)
RECOVERED_EXPECTED=$(printf '%s\t%s\n%s\t%s\n%s\t%s' \
  "$PAGE" '130-comment-awaiting-handoff.json' \
  "$PAGE" '200-edit-test.json' \
  "$PAGE" '210-handoff-test.json')
[ "$RECOVERED" = "$RECOVERED_EXPECTED" ] || {
  echo "zero-wait watch did not recover a later interrupted-turn batch" >&2
  exit 1
}
[ "$(cat "$CURSOR")" = "$CURSOR_AFTER_FIRST" ] || {
  echo "ready zero-wait scan mutated the cursor before processing" >&2
  exit 1
}

SINGLE_REVIEW="$TMP/single.spec.html.review"
mkdir -p "$SINGLE_REVIEW/human" "$SINGLE_REVIEW/agent"
: > "$SINGLE_REVIEW/human/010-comment.json"
: > "$SINGLE_REVIEW/human/020-handoff-h.json"
: > "$SINGLE_REVIEW/human/030-later-draft.json"
SINGLE_READY=$(SPEC_CHAT_WATCH_OWNER=turn-yielded "$SCRIPTS/watch.sh" "$SINGLE_REVIEW" "$SINGLE_REVIEW/.cursor-test" 1 1)
SINGLE_EXPECTED=$(printf '%s\n%s' '010-comment.json' '020-handoff-h.json')
[ "$SINGLE_READY" = "$SINGLE_EXPECTED" ] || {
  echo "single-page watch crossed the newest completed handoff into later drafts" >&2
  exit 1
}

set +e
DETACHED_OUTPUT=$("$SCRIPTS/codex-review.sh" --once "$TMP" 2>&1)
DETACHED_RC=$?
set -e
[ "$DETACHED_RC" -eq 2 ] && printf '%s' "$DETACHED_OUTPUT" | grep -F 'detached processing is disabled' >/dev/null || {
  echo "codex-review still permits a second detached processor" >&2
  exit 1
}

node "$ROOT/tests/runtime-thread-model.mjs"
node "$ROOT/tests/runtime-focus-model.mjs"
node "$ROOT/tests/runtime-fsa-transport.mjs"
node "$ROOT/tests/runtime-mobile-contract.mjs"
(cd "$ROOT" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests/review-serve-baseline.py)
(cd "$ROOT" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests/review-surface-preflight.py)
(cd "$ROOT" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests/spec-style-contract.py)
"$ROOT/tests/review-wake-control.sh"
echo "review-spec script tests passed"
