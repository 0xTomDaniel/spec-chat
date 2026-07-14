#!/bin/sh
# Bounded multi-page wait: scans every *.html.review spool below REVIEW_ROOT
# and exits 0 with tab-separated HTML_PATH / EVENT_FILENAME rows for the first
# ready page's completed hand-off batches. Each spool owns its own cursor file.
# TIMEOUT_S=0 performs one read-only scan. Exits 3 when no batch is ready.
# usage: watch-specs.sh REVIEW_ROOT [CURSOR_NAME] [TIMEOUT_S] [POLL_S]
set -eu

ROOT=$1
CURSOR_NAME=${2:-.cursor-codex}
TMO=${3:-240}
POLL=${4:-2}

[ -d "$ROOT" ] || {
  echo "watch-specs: not a directory: $ROOT" >&2
  exit 2
}
case "$CURSOR_NAME" in
  "" | */*)
    echo "watch-specs: cursor name must be a basename" >&2
    exit 2
    ;;
esac
case "$TMO" in
  "" | *[!0-9]*)
    echo "watch-specs: timeout must be a non-negative integer" >&2
    exit 2
    ;;
esac
case "$POLL" in
  "" | *[!0-9]* | 0)
    echo "watch-specs: poll interval must be a positive integer" >&2
    exit 2
    ;;
esac

ROOT=$(CDPATH= cd "$ROOT" && pwd)
SCAN=${TMPDIR:-/tmp}/spec-chat-watch-specs.$$
trap 'rm -f "$SCAN"' EXIT HUP INT TERM

scan_once() {
  find "$ROOT" -type d -name '*.html.review' -prune -print | LC_ALL=C sort > "$SCAN"
  while IFS= read -r REVIEW; do
    SPEC=${REVIEW%.review}
    [ -f "$SPEC" ] || continue
    [ -d "$REVIEW/human" ] || continue
    CURSOR="$REVIEW/$CURSOR_NAME"
    if [ -f "$CURSOR" ]; then
      new=$(LC_ALL=C ls -1 "$REVIEW/human" 2>/dev/null | grep -vxFf "$CURSOR" || true)
    else
      new=$(LC_ALL=C ls -1 "$REVIEW/human" 2>/dev/null || true)
    fi
    if [ -n "$new" ]; then
      ready=$new
      if [ "${LIVE:-0}" != 1 ]; then
        last_handoff=$(printf '%s\n' "$new" | awk '/-handoff-/ { line = NR } END { if (line) print line }')
        [ -n "$last_handoff" ] || continue
        ready=$(printf '%s\n' "$new" | sed -n "1,${last_handoff}p")
      fi
      if [ -n "$ready" ]; then
        printf '%s\n' "$ready" | while IFS= read -r EVENT; do
          printf '%s\t%s\n' "$SPEC" "$EVENT"
        done
        return 0
      fi
    fi
  done < "$SCAN"
  return 3
}

if scan_once; then
  exit 0
else
  RC=$?
  [ "$RC" -eq 3 ] || exit "$RC"
fi
[ "$TMO" -eq 0 ] && exit 3

t=0
while [ "$t" -lt "$TMO" ]; do
  sleep "$POLL"
  t=$((t + POLL))
  if scan_once; then
    exit 0
  else
    RC=$?
    [ "$RC" -eq 3 ] || exit "$RC"
  fi
done
exit 3
