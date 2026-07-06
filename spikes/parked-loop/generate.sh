#!/bin/sh
# Synthetic annotator: emits human events against specs/order-pipeline.spec.html
# on a delay, ending with a hand-off marker. Stands in for the browser runtime.
# usage: generate.sh REVIEW_DIR
set -eu
DIR=$1
mkdir -p "$DIR/human"
emit() { # $1 id  $2 event  $3 anchorId  $4 targetJson  $5 text
  f="$DIR/human/$(date +%s%N)-$2-$1.json"
  cat > "$f" <<JSON
{"id":"$1","event":"$2","anchorId":"$3","target":$4,"quote":null,"text":"$5","actor":"human","createdAt":"$(date -Is)","schemaVersion":1}
JSON
  echo "emitted $f"
}
sleep 5
emit c1 comment latency-budget '{"type":"datum","key":"enqueue"}' 'Add an 800 ms target line to this chart so enqueue reads as the breach it is.'
sleep 6
emit c2 comment retry-policy '{"type":"element","key":"p[1]"}' 'State the DLQ alert channel explicitly - who gets paged, where.'
sleep 4
emit h1 handoff '' null 'batch: c1,c2'
echo "generator done"
