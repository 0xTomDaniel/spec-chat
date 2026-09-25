#!/bin/sh
set -eu

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
SCRIPTS="$ROOT/skill/review-spec/scripts"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/spec-chat-wake-tests.XXXXXX")
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

DOCS="$TMP/docs"
PAGE="$DOCS/wake.spec.html"
REVIEW="$PAGE.review"
mkdir -p "$REVIEW/human" "$REVIEW/agent"
: > "$PAGE"

set +e
RAW_OUTPUT=$($SCRIPTS/watch-specs.sh "$DOCS" .cursor-owner 1 1 2>&1)
RAW_RC=$?
set -e
[ "$RAW_RC" -eq 2 ] && printf '%s' "$RAW_OUTPUT" | grep -F 'detection-only' >/dev/null || {
  echo "raw long watcher was allowed to masquerade as attachment" >&2
  exit 1
}

MANUAL=$($SCRIPTS/review-control.sh manual)
printf '%s' "$MANUAL" | grep -F 'control=manual-resume' >/dev/null
printf '%s' "$MANUAL" | grep -F 'new human chat message required' >/dev/null

LOCK_DOCS="$TMP/lock-docs"
LOCK_PAGE="$LOCK_DOCS/lock.spec.html"
mkdir -p "$LOCK_PAGE.review/human" "$LOCK_PAGE.review/agent"
: > "$LOCK_PAGE"
mkdir -p "$TMP/runtime-a" "$TMP/runtime-b" "$TMP/temp-a" "$TMP/temp-b"
XDG_RUNTIME_DIR="$TMP/runtime-a" TMPDIR="$TMP/temp-a" \
  "$SCRIPTS/review-control.sh" yielded "$LOCK_DOCS" .cursor-lock 20 1 \
  > "$TMP/lock-owner" 2>&1 &
LOCK_OWNER_PID=$!
for _ in 1 2 3 4 5; do
  grep -F 'control=turn-yielded' "$TMP/lock-owner" >/dev/null 2>&1 && break
  sleep 1
done
set +e
SECOND_OWNER=$(XDG_RUNTIME_DIR="$TMP/runtime-b" TMPDIR="$TMP/temp-b" \
  "$SCRIPTS/review-control.sh" yielded "$LOCK_DOCS" .cursor-lock 1 1 2>&1)
SECOND_OWNER_RC=$?
set -e
kill "$LOCK_OWNER_PID" 2>/dev/null || true
wait "$LOCK_OWNER_PID" 2>/dev/null || true
[ "$SECOND_OWNER_RC" -eq 5 ] && printf '%s' "$SECOND_OWNER" | grep -F 'control owner already exists' >/dev/null || {
  echo "two review control owners were allowed for one collection cursor" >&2
  exit 1
}

set +e
EXTERNAL=$("$SCRIPTS/review-control.sh" external "$DOCS" .cursor-owner pane term adapter 2>&1)
EXTERNAL_RC=$?
set -e
[ "$EXTERNAL_RC" -eq 2 ] && printf '%s' "$EXTERNAL" | grep -F 'usage: review-control.sh yielded|manual' >/dev/null || {
  echo "review-control.sh still accepts the removed external monitor" >&2
  exit 1
}
[ ! -e "$SCRIPTS/wake-herdr.py" ] || {
  echo "wake-herdr.py adapter still exists; the review host owns wake" >&2
  exit 1
}
if grep -rn -e 'SPEC_CHAT_OWNER_ID' -e 'SPEC_CHAT_BATCH_ID' -e 'wake-herdr' -e 'review-control.sh external' "$ROOT/skill" "$ROOT/DESIGN.md" >/dev/null; then
  echo "adapter environment contract or external monitor instructions remain" >&2
  exit 1
fi

echo "review wake control tests passed"
