#!/bin/sh
# tools/*.py are byte-identical copies of the review-spec skill assets.
set -eu
cd "$(dirname "$0")/.."
for tool in tools/*.py; do
  cmp "$tool" "skill/review-spec/assets/$(basename "$tool")"
done
echo "tool copies identical"
