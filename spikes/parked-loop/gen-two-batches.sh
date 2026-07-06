#!/bin/sh
# Two hand-off batches on a delay — proves multi-cycle looping within ONE session.
# usage: gen-two-batches.sh REVIEW_DIR
set -eu
DIR=$1
emit() { # $1 id  $2 event  $3 anchorId  $4 targetJson  $5 text
  f="$DIR/human/$(date +%s%N)-$2-$1.json"
  printf '{"id":"%s","event":"%s","anchorId":"%s","target":%s,"quote":null,"text":"%s","actor":"human","createdAt":"%s","schemaVersion":1}\n' \
    "$1" "$2" "$3" "$4" "$5" "$(date -Is)" > "$f"
  echo "emitted $f"
}
sleep 10
emit c4 comment latency-budget '{"type":"datum","key":"inventory"}' 'Add a note that inventory p95 is cache-miss dominated.'
sleep 2
emit h3 handoff '' null 'batch: c4'
sleep 30
emit c5 comment retry-policy '{"type":"element","key":"p[3]"}' 'Link the DLQ runbook from the nothing-dropped claim.'
sleep 2
emit h4 handoff '' null 'batch: c5'
echo done
