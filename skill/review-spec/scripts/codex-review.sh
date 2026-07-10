#!/bin/sh
# Codex attachment for the spec-chat review loop.
# Codex spends ~40k+ tokens per drain cycle, so the watch runs OUTSIDE the
# Codex session (free) and invokes `codex exec` only when a batch actually
# arrives. Resumes the recorded authoring thread when review/state.json has
# one (full authoring context); otherwise a cold exec (files carry context).
# A directory target is the default: one process discovers and serially drains
# every *.spec.html review spool below it. A file target remains available for
# deliberate single-page narrowing.
# usage: codex-review.sh [--once] SPEC_ROOT_OR_PATH
set -eu

ONCE=0
if [ "${1:-}" = "--once" ]; then
  ONCE=1
  shift
fi
[ "$#" -eq 1 ] || {
  echo "usage: codex-review.sh [--once] SPEC_ROOT_OR_PATH" >&2
  exit 2
}

TARGET=$1
SKILL_DIR=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
CURSOR_NAME=.cursor-codex

if [ -d "$TARGET" ]; then
  MODE=tree
  ROOT=$(CDPATH= cd "$TARGET" && pwd)
  echo "codex-review: watching every spec below $ROOT"
elif [ -f "$TARGET" ]; then
  MODE=single
  SPEC_DIR=$(CDPATH= cd "$(dirname "$TARGET")" && pwd)
  SPEC="$SPEC_DIR/$(basename "$TARGET")"
  REVIEW="$SPEC.review"
  CURSOR="$REVIEW/$CURSOR_NAME"
  mkdir -p "$REVIEW/human" "$REVIEW/agent"
  [ -f "$CURSOR" ] || : > "$CURSOR"
  echo "codex-review: watching only $REVIEW"
else
  echo "codex-review: no such spec root or file: $TARGET" >&2
  exit 2
fi

command -v codex >/dev/null 2>&1 || {
  echo "codex-review: codex CLI not found" >&2
  exit 2
}

while :; do
  set +e
  if [ "$MODE" = tree ]; then
    READY=$("$SKILL_DIR/scripts/watch-specs.sh" "$ROOT" "$CURSOR_NAME" 3600 3)
    RC=$?
  else
    READY=$("$SKILL_DIR/scripts/watch.sh" "$REVIEW" "$CURSOR" 3600 3)
    RC=$?
  fi
  set -e
  [ "$RC" -eq 3 ] && continue          # quiet timeout, re-park for free
  [ "$RC" -eq 0 ] || exit "$RC"
  [ -z "$READY" ] && continue

  if [ "$MODE" = tree ]; then
    SPEC=$(printf '%s\n' "$READY" | cut -f1 | sed -n '1p')
    NEW=$(printf '%s\n' "$READY" | cut -f2-)
    REVIEW="$SPEC.review"
    CURSOR="$REVIEW/$CURSOR_NAME"
  else
    NEW=$READY
  fi
  STATE="$REVIEW/state.json"
  SID=""
  if [ -f "$STATE" ] && command -v jq >/dev/null 2>&1; then
    SID=$(jq -r '.sessionId // empty' "$STATE" 2>/dev/null || true)
  fi

  PROMPT="A hand-off batch arrived on $SPEC. Follow $SKILL_DIR/SKILL.md exactly: read the new human events under $REVIEW/human, apply each comment to the spec in the dialect, reply per comment with $SKILL_DIR/scripts/emit-reply.sh, append exactly the watcher-reported filenames below to $CURSOR (never regenerate with ls), and externalize agreements to $REVIEW/context.md. Do ONE drain cycle then stop.

Watcher-reported filenames:
$NEW"
  # --skip-git-repo-check: specs may live outside a git repo (or in a
  # gitignored area); workspace-write sandbox still bounds writes.
  # </dev/null: don't let codex block waiting on the wrapper's stdin.
  if [ -n "$SID" ]; then
    codex exec -s workspace-write --skip-git-repo-check resume "$SID" "$PROMPT" </dev/null
  else
    codex exec -s workspace-write --skip-git-repo-check "$PROMPT" </dev/null
  fi
  [ "$ONCE" -eq 1 ] && exit 0
done
