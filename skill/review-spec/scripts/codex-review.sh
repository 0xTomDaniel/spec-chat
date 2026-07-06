#!/bin/sh
# Codex attachment for the spec-chat review loop.
# Codex spends ~40k+ tokens per drain cycle, so the watch runs OUTSIDE the
# Codex session (free) and invokes `codex exec` only when a batch actually
# arrives. Resumes the recorded authoring thread when review/state.json has
# one (full authoring context); otherwise a cold exec (files carry context).
# usage: codex-review.sh SPEC_PATH   (SPEC_PATH = path to the .spec.html)
set -eu
SPEC=$1
REVIEW="$SPEC.review"
CURSOR="$REVIEW/.cursor-codex"
STATE="$REVIEW/state.json"
SKILL_DIR=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
mkdir -p "$REVIEW/human" "$REVIEW/agent"
# Start with an EMPTY cursor so any batch already waiting (the whole point of
# detached pickup - a dead session left work behind) gets processed. An
# existing cursor from a prior run is preserved. NEVER seed from `ls`: that
# marks pending events as already-seen and the batch is silently skipped.
[ -f "$CURSOR" ] || : > "$CURSOR"

SID=""
if [ -f "$STATE" ] && command -v jq >/dev/null 2>&1; then
  SID=$(jq -r '.sessionId // empty' "$STATE" 2>/dev/null || true)
fi
echo "codex-review: watching $REVIEW (session=${SID:-cold+digest})"

while :; do
  set +e
  NEW=$("$SKILL_DIR/scripts/watch.sh" "$REVIEW" "$CURSOR" 3600 3)
  RC=$?
  set -e
  [ "$RC" -eq 3 ] && continue          # quiet timeout, re-park for free
  [ -z "$NEW" ] && continue
  PROMPT="A hand-off batch arrived on $SPEC. Follow $SKILL_DIR/SKILL.md exactly: read the new human events under $REVIEW/human, apply each comment to the spec in the dialect, reply per comment with $SKILL_DIR/scripts/emit-reply.sh, append the processed filenames to $CURSOR (never regenerate with ls), and externalize agreements to $REVIEW/context.md. Do ONE drain cycle then stop."
  # --skip-git-repo-check: specs may live outside a git repo (or in a
  # gitignored area); workspace-write sandbox still bounds writes.
  # </dev/null: don't let codex block waiting on the wrapper's stdin.
  if [ -n "$SID" ]; then
    codex exec -s workspace-write --skip-git-repo-check resume "$SID" "$PROMPT" </dev/null
  else
    codex exec -s workspace-write --skip-git-repo-check "$PROMPT" </dev/null
  fi
done
