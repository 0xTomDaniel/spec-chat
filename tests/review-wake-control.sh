#!/bin/sh
set -eu

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
SCRIPTS="$ROOT/skill/review-spec/scripts"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/spec-chat-wake-tests.XXXXXX")
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

DOCS="$TMP/docs"
PAGE="$DOCS/wake.spec.html"
REVIEW="$PAGE.review"
mkdir -p "$REVIEW/human" "$REVIEW/agent" "$TMP/bin"
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

cat > "$TMP/fake-wake.sh" <<'EOF'
#!/bin/sh
set -eu
[ "$(cat "$1")" = closed ] || exit 20
[ "$SPEC_CHAT_OWNER_ID" = 'pane-test' ] || exit 21
[ "$SPEC_CHAT_OWNER_SESSION" = 'terminal-test' ] || exit 22
printf '%s\n' "$SPEC_CHAT_BATCH_ID" > "$2"
printf '%s\n' reactivated > "$1"
EOF
chmod +x "$TMP/fake-wake.sh"
printf '%s\n' closed > "$TMP/owner-state"

SPEC_CHAT_MONITOR_ONCE=1 "$SCRIPTS/review-control.sh" external \
  "$DOCS" .cursor-owner pane-test terminal-test \
  "$TMP/fake-wake.sh" "$TMP/owner-state" "$TMP/wake-batch" \
  > "$TMP/monitor-output" 2> "$TMP/monitor-error" &
MONITOR_PID=$!

for _ in 1 2 3 4 5; do
  grep -F 'control=external-wake' "$TMP/monitor-output" >/dev/null 2>&1 && break
  sleep 1
done
grep -F 'control=external-wake' "$TMP/monitor-output" >/dev/null || {
  echo "external monitor did not reach ready state" >&2
  exit 1
}

: > "$REVIEW/human/100-comment-test.json"
: > "$REVIEW/human/110-handoff-test.json"
wait "$MONITOR_PID"

[ "$(cat "$TMP/owner-state")" = reactivated ] || {
  echo "later handoff did not reactivate the closed owner" >&2
  exit 1
}
[ -s "$TMP/wake-batch" ] || {
  echo "wake adapter did not receive a stable batch identity" >&2
  exit 1
}
[ ! -e "$REVIEW/.cursor-owner" ] || {
  echo "wake monitor advanced the processing cursor" >&2
  exit 1
}

cat > "$TMP/bin/herdr" <<'EOF'
#!/bin/sh
if [ "$1 $2" = 'agent get' ]; then
  printf '%s\n' "{\"id\":\"test\",\"result\":{\"agent\":{\"pane_id\":\"wC:pTest\",\"terminal_id\":\"term-test\",\"agent_status\":\"${FAKE_HERDR_STATUS:-done}\"}}}"
  exit 0
fi
exit 2
EOF
cat > "$TMP/bin/herdr-say" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" > "$FAKE_HERDR_SAY_LOG"
printf '%s\n' 'message_id=test status=pending'
EOF
chmod +x "$TMP/bin/herdr" "$TMP/bin/herdr-say"

PATH="$TMP/bin:$PATH" FAKE_HERDR_SAY_LOG="$TMP/herdr-say-log" \
  SPEC_CHAT_OWNER_ID='wC:pTest' SPEC_CHAT_OWNER_SESSION='term-test' \
  SPEC_CHAT_BATCH_ID='batch-test' SPEC_CHAT_READY_SPEC="$PAGE" \
  SPEC_CHAT_CURSOR_NAME='.cursor-owner' \
  python3 "$SCRIPTS/wake-herdr.py"
grep -F -- '--kind command' "$TMP/herdr-say-log" >/dev/null
grep -F -- 'wC:pTest' "$TMP/herdr-say-log" >/dev/null
grep -F -- 'batch-test' "$TMP/herdr-say-log" >/dev/null

set +e
PATH="$TMP/bin:$PATH" FAKE_HERDR_STATUS=working FAKE_HERDR_SAY_LOG="$TMP/herdr-say-busy" \
  SPEC_CHAT_OWNER_ID='wC:pTest' SPEC_CHAT_OWNER_SESSION='term-test' \
  SPEC_CHAT_BATCH_ID='batch-test' SPEC_CHAT_READY_SPEC="$PAGE" \
  SPEC_CHAT_CURSOR_NAME='.cursor-owner' \
  python3 "$SCRIPTS/wake-herdr.py" >/dev/null 2> "$TMP/busy-error"
BUSY_RC=$?
set -e
[ "$BUSY_RC" -eq 75 ] && [ ! -e "$TMP/herdr-say-busy" ] || {
  echo "Herdr adapter did not defer cleanly while the owner was working" >&2
  exit 1
}

set +e
PATH="$TMP/bin:$PATH" FAKE_HERDR_SAY_LOG="$TMP/herdr-say-mismatch" \
  SPEC_CHAT_OWNER_ID='wC:pTest' SPEC_CHAT_OWNER_SESSION='wrong-terminal' \
  SPEC_CHAT_BATCH_ID='batch-test' SPEC_CHAT_READY_SPEC="$PAGE" \
  SPEC_CHAT_CURSOR_NAME='.cursor-owner' \
  python3 "$SCRIPTS/wake-herdr.py" >/dev/null 2> "$TMP/mismatch-error"
MISMATCH_RC=$?
set -e
[ "$MISMATCH_RC" -ne 0 ] && grep -F 'identity changed' "$TMP/mismatch-error" >/dev/null || {
  echo "Herdr adapter did not reject a changed owner identity" >&2
  exit 1
}

echo "review wake control tests passed"
