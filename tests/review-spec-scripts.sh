#!/bin/sh
set -eu

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
SCRIPTS="$ROOT/skill/review-spec/scripts"
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

command -v jq >/dev/null 2>&1 || {
  echo "jq is required for the codex session-continuity test" >&2
  exit 1
}

FAKE_BIN="$TMP/bin"
CALLS="$TMP/codex-calls"
mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/codex" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_CODEX_CALLS"
case " $* " in
  *' --json '*)
    printf '%s\n' \
      '{"type":"thread.started","thread_id":"thread-test-123"}' \
      '{"type":"item.completed","item":{"type":"agent_message","text":"cold drain complete"}}'
    ;;
esac
EOF
chmod +x "$FAKE_BIN/codex"

PATH="$FAKE_BIN:$PATH" FAKE_CODEX_CALLS="$CALLS" \
  "$SCRIPTS/codex-review.sh" --once "$TMP" >/dev/null 2>&1

STATE="$REVIEW/state.json"
[ "$(jq -r '.sessionId' "$STATE")" = 'thread-test-123' ] || {
  echo "codex-review did not persist the cold thread id" >&2
  exit 1
}

PATH="$FAKE_BIN:$PATH" FAKE_CODEX_CALLS="$CALLS" \
  "$SCRIPTS/codex-review.sh" --once "$TMP" >/dev/null 2>&1

grep -F 'exec -s workspace-write --skip-git-repo-check resume thread-test-123' "$CALLS" >/dev/null || {
  echo "codex-review did not resume the persisted thread id" >&2
  exit 1
}

node "$ROOT/tests/runtime-thread-model.mjs"
echo "review-spec script tests passed"
