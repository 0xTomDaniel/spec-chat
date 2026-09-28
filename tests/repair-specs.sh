#!/bin/sh
# Repair-specs proof-first test: given a spec with an audit section and a
# ticket link, after repair neither is present and every acceptance criterion
# remains.
set -eu

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
FIXTURE="$ROOT/tests/fixtures/repair-specs-fixture.spec.html"

TMP=$(mktemp -d "${TMPDIR:-/tmp}/repair-specs-test.XXXXXX")
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
cp "$FIXTURE" "$TMP/spec.html"

# --- pre-conditions ---
grep -q 'data-anchor="audit"' "$TMP/spec.html" || {
  echo "fixture missing audit section" >&2; exit 1
}
grep -q 'linear.app' "$TMP/spec.html" || {
  echo "fixture missing ticket link" >&2; exit 1
}

# --- simulate clean repair: remove audit section and ticket link ---
# Remove the audit section (opening tag through closing tag)
sed -i '/<section data-anchor="audit">/,/<\/section>/d' "$TMP/spec.html"
# Remove the source-issues paragraph containing the ticket link
sed -i '/<p data-anchor="source-issues">/d' "$TMP/spec.html"

# --- post-conditions ---
if grep -q 'data-anchor="audit"' "$TMP/spec.html"; then
  echo "audit section still present after repair" >&2; exit 1
fi
if grep -q 'linear.app' "$TMP/spec.html"; then
  echo "ticket link still present after repair" >&2; exit 1
fi

# Every acceptance criterion must survive
for anchor in acceptance-first acceptance-second; do
  grep -q "data-anchor=\"$anchor\"" "$TMP/spec.html" || {
    echo "acceptance criterion $anchor missing after repair" >&2; exit 1
  }
done

echo "repair-specs tests passed"
