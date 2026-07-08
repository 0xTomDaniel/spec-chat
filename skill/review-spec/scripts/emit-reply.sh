#!/bin/sh
# Write one agent reply event into the spool, schema-exact (the browser
# runtime renders respondsTo/text/status/change - field names matter).
# usage: emit-reply.sh REVIEW_DIR RESPONDS_TO ANCHOR_ID TARGET_JSON STATUS CHANGE TEXT
#   TARGET_JSON: the comment's target verbatim, or 'null'
#   STATUS: acknowledged | orphaned
#   CHANGE: short change summary, or 'no spec change'
# Uses jq if present, else python3; errors clearly if neither exists.
set -eu
DIR=$1; RT=$2; ANCHOR=$3; TARGET=$4; STATUS=$5; CHANGE=$6; TEXT=$7
mkdir -p "$DIR/agent"
# portable across GNU/BSD: ns via python3 (BSD date lacks %N), UTC ISO time
NS=$(python3 -c 'import time; print(time.time_ns())' 2>/dev/null || date +%s%N)
OUT="$DIR/agent/${NS}-reply-$RT.json"
ID="r${NS}"; AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if command -v jq >/dev/null 2>&1; then
  jq -n --arg id "$ID" --arg rt "$RT" --arg anchor "$ANCHOR" --argjson target "$TARGET" \
        --arg status "$STATUS" --arg change "$CHANGE" --arg text "$TEXT" --arg at "$AT" \
    '{id:$id, event:"reply", respondsTo:$rt, anchorId:$anchor, target:$target,
      text:$text, status:$status, change:$change, actor:"agent",
      createdAt:$at, schemaVersion:1}' > "$OUT"
elif command -v python3 >/dev/null 2>&1; then
  # env-passing avoids shell-quoting hazards in TARGET/TEXT
  ID="$ID" RT="$RT" ANCHOR="$ANCHOR" TARGET="$TARGET" STATUS="$STATUS" \
  CHANGE="$CHANGE" TEXT="$TEXT" AT="$AT" OUT="$OUT" python3 - <<'PY'
import json, os
e = os.environ
json.dump({"id": e["ID"], "event": "reply", "respondsTo": e["RT"],
           "anchorId": e["ANCHOR"], "target": json.loads(e["TARGET"]),
           "text": e["TEXT"], "status": e["STATUS"], "change": e["CHANGE"],
           "actor": "agent", "createdAt": e["AT"], "schemaVersion": 1},
          open(e["OUT"], "w"))
PY
else
  echo "emit-reply.sh: needs jq or python3 to write JSON safely; neither found" >&2
  exit 1
fi
echo "replied -> $OUT ($RT)"
