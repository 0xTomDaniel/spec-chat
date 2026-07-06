#!/bin/sh
# Second synthetic batch, delayed — used for the Codex leg of spike 2.
# usage: gen-codex.sh REVIEW_DIR [INITIAL_DELAY_S]
set -eu
DIR=$1
sleep "${2:-20}"
f="$DIR/human/$(date +%s%N)-comment-c3.json"
cat > "$f" <<'JSON'
{"id":"c3","event":"comment","anchorId":"goals","target":{"type":"element","key":"p[2]"},"quote":"Non-goal: changing the public checkout API surface.","text":"Also list DB schema freeze as an explicit non-goal.","actor":"human","createdAt":"2026-07-04T02:20:00Z","schemaVersion":1}
JSON
echo "emitted $f"
sleep 5
f="$DIR/human/$(date +%s%N)-handoff-h2.json"
cat > "$f" <<'JSON'
{"id":"h2","event":"handoff","anchorId":"","target":null,"quote":null,"text":"batch: c3","actor":"human","createdAt":"2026-07-04T02:20:05Z","schemaVersion":1}
JSON
echo "emitted $f"
