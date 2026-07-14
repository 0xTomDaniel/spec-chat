#!/bin/sh
# Bounded multi-page wait: scans every *.html.review spool below REVIEW_ROOT
# and exits 0 with tab-separated HTML_PATH / EVENT_FILENAME rows for the first
# ready hand-off batch. Each review spool owns an independent cursor file.
# Exits 3 on timeout.
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

ROOT=$(CDPATH= cd "$ROOT" && pwd)
SCAN=${TMPDIR:-/tmp}/spec-chat-watch-specs.$$
trap 'rm -f "$SCAN"' EXIT HUP INT TERM

t=0
while [ "$t" -lt "$TMO" ]; do
  find "$ROOT" -type d -name '*.html.review' -prune -print | LC_ALL=C sort > "$SCAN"
  while IFS= read -r REVIEW; do
    SPEC=${REVIEW%.review}
    [ -f "$SPEC" ] || continue
    [ -d "$REVIEW/human" ] || continue
    mkdir -p "$REVIEW/agent"
    CURSOR="$REVIEW/$CURSOR_NAME"
    touch "$CURSOR"
    new=$(LC_ALL=C ls -1 "$REVIEW/human" 2>/dev/null | grep -vxFf "$CURSOR" || true)
    if [ -n "$new" ]; then
      if [ "${LIVE:-0}" = 1 ] || echo "$new" | grep -q handoff; then
        echo "$new" | while IFS= read -r EVENT; do
          printf '%s\t%s\n' "$SPEC" "$EVENT"
        done
        exit 0
      fi
    fi
  done < "$SCAN"
  sleep "$POLL"
  t=$((t + POLL))
done
exit 3
