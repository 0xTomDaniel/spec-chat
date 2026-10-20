#!/bin/sh
# Bounded multi-page wait: scans every *.html.review spool below REVIEW_ROOT
# and exits 0 with tab-separated HTML_PATH / EVENT_FILENAME rows for the first
# ready page's hand-off batches: each hand-off not in the cursor and the events
# it lists (assets/spool.py, shared with host wake). Each spool owns its cursor.
# A REVIEW_ROOT holding human/ itself, not named *.html.review, is a scope's
# hosted threads (a spool with no spec): its rows start with that path.
# TIMEOUT_S=0 performs one read-only scan. Exits 3 when no batch is ready.
# Each row whose event has a mark also gets a stderr line from the shared resolver
# (assets/place.py): place<TAB>EVENT_FILENAME<TAB>state<TAB>#anchor<TAB>start-end<TAB>quote.
# Each row of a thread written on a host page gets a stderr line from the spool fold
# (assets/spool.py): host<TAB>EVENT_FILENAME<TAB>subject<TAB>kind<TAB>label<TAB>from<TAB>quote.
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

# One spool's ready batch: rows on stdout, and on stderr place lines (spec spools)
# and host lines. usage: scan_spool SPOOL ROW_PATH [SPEC]; returns 3 when none.
scan_spool() {
  [ -d "$1/human" ] || return 3
  CURSOR="$1/$CURSOR_NAME"
  if [ -f "$CURSOR" ]; then
    new=$(LC_ALL=C ls -1 "$1/human" 2>/dev/null | grep -vxFf "$CURSOR" || true)
  else
    new=$(LC_ALL=C ls -1 "$1/human" 2>/dev/null || true)
  fi
  [ -n "$new" ] || return 3
  ready=$(python3 "$SPOOL" batch "$1" "$CURSOR_NAME")
  [ -n "$ready" ] || return 3
  printf '%s\n' "$ready" | while IFS= read -r EVENT; do
    printf '%s\t%s\n' "$2" "$EVENT"
  done
  [ -z "${3:-}" ] || printf '%s\n' "$ready" | python3 "$PLACE" "$3" >&2 || :
  printf '%s\n' "$ready" | python3 "$SPOOL" host "$1" >&2 || :
  return 0
}

scan_once() {
  case "$ROOT" in
    *.html.review) ;;
    *)
      if [ -d "$ROOT/human" ]; then
        scan_spool "$ROOT" "$ROOT" && return 0
        return 3
      fi
      ;;
  esac
  find "$ROOT" -type d -name '*.html.review' -prune -print | LC_ALL=C sort > "$SCAN"
  while IFS= read -r REVIEW; do
    SPEC=${REVIEW%.review}
    [ -f "$SPEC" ] || continue
    scan_spool "$REVIEW" "$SPEC" "$SPEC" && return 0
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
