#!/bin/sh
# Bounded batch wait: exits 0 with the list of unseen human events once a
# hand-off marker arrives (or on any new event when LIVE=1); exits 3 on timeout.
# usage: watch.sh REVIEW_DIR CURSOR_FILE [TIMEOUT_S] [POLL_S]
set -eu
DIR=$1
CUR=$2
TMO=${3:-240}
POLL=${4:-2}
mkdir -p "$DIR/human" "$DIR/agent"
touch "$CUR"
t=0
while [ "$t" -lt "$TMO" ]; do
  new=$(ls "$DIR/human" 2>/dev/null | grep -vxFf "$CUR" || true)
  if [ -n "$new" ]; then
    if [ "${LIVE:-0}" = 1 ] || echo "$new" | grep -q handoff; then
      echo "$new"
      exit 0
    fi
  fi
  sleep "$POLL"
  t=$((t + POLL))
done
exit 3
