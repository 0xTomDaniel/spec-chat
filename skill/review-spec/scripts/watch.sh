#!/bin/sh
# Bounded batch wait: exits 0 with the list of unseen human events once a
# hand-off marker arrives (or on any new event when LIVE=1); exits 3 on timeout.
# usage: watch.sh REVIEW_DIR CURSOR_FILE [TIMEOUT_S] [POLL_S]
set -eu
DIR=$1
CUR=$2
TMO=${3:-240}
POLL=${4:-2}
if [ "$TMO" -gt 0 ]; then
  case "${SPEC_CHAT_WATCH_OWNER:-}" in
    turn-yielded | external-wake) ;;
    *)
      echo "watch: detection-only raw long watcher cannot own review wake; use review-control.sh yielded, external, or manual" >&2
      exit 2
      ;;
  esac
fi
mkdir -p "$DIR/human" "$DIR/agent"
touch "$CUR"
t=0
while [ "$t" -lt "$TMO" ]; do
  new=$(ls "$DIR/human" 2>/dev/null | grep -vxFf "$CUR" || true)
  if [ -n "$new" ]; then
    ready=$new
    if [ "${LIVE:-0}" != 1 ]; then
      last_handoff=$(printf '%s\n' "$new" | awk '/-handoff-/ { line = NR } END { if (line) print line }')
      if [ -n "$last_handoff" ]; then
        ready=$(printf '%s\n' "$new" | sed -n "1,${last_handoff}p")
      else
        ready=""
      fi
    fi
    if [ -n "$ready" ]; then
      printf '%s\n' "$ready"
      exit 0
    fi
  fi
  sleep "$POLL"
  t=$((t + POLL))
done
exit 3
