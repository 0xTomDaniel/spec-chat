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
: > "$REVIEW/human/100-handoff-test.json"

READY=$("$SCRIPTS/watch-specs.sh" "$TMP" .cursor-test 1 1)
EXPECTED=$(printf '%s\t%s' "$PAGE" '100-handoff-test.json')
[ "$READY" = "$EXPECTED" ] || {
  echo "watch-specs did not discover a non-spec HTML review spool" >&2
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
