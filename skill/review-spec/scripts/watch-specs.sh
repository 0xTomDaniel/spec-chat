#!/bin/sh
# Bounded multi-page wait: scans every *.html.review spool below REVIEW_ROOT
# and exits 0 with tab-separated HTML_PATH / EVENT_FILENAME rows for the first
# ready page's hand-off batches: each hand-off not in the cursor and the events
# it lists (assets/spool.py, shared with host wake). Each spool owns its cursor.
# TIMEOUT_S=0 performs one read-only scan. Exits 3 when no batch is ready.
# Each row whose event has a mark also gets a stderr line from the shared resolver
# (assets/place.py): place<TAB>EVENT_FILENAME<TAB>state<TAB>#anchor<TAB>start-end<TAB>quote.
# usage: watch-specs.sh REVIEW_ROOT [CURSOR_NAME] [TIMEOUT_S] [POLL_S]
set -eu

ROOT=$1
CURSOR_NAME=${2:-.cursor-owner}
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

if [ "$TMO" -gt 0 ]; then
  case "${SPEC_CHAT_WATCH_OWNER:-}" in
    turn-yielded) ;;
    *)
      echo "watch-specs: detection-only raw long watcher cannot own review wake; use review-control.sh yielded or manual" >&2
      exit 2
      ;;
  esac
fi

ROOT=$(CDPATH= cd "$ROOT" && pwd)
SPOOL=$(CDPATH= cd "$(dirname "$0")/../assets" && pwd)/spool.py
PLACE=$(CDPATH= cd "$(dirname "$0")/../assets" && pwd)/place.py
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
        ready=$(python3 "$SPOOL" batch "$REVIEW" "$CURSOR_NAME")
      fi
      if [ -n "$ready" ]; then
        printf '%s\n' "$ready" | while IFS= read -r EVENT; do
          printf '%s\t%s\n' "$SPEC" "$EVENT"
        done
        printf '%s\n' "$ready" | python3 "$PLACE" "$SPEC" >&2 || :
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
