#!/bin/sh
# Repair-specs proof-first test: given a spec with an audit section, a ticket
# link, and QA harness setup, after repair none is present and every acceptance
# criterion remains. Then checks the skill texts carry the product-term
# criterion rules.
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
grep -q 'posthog-captures.jsonl' "$TMP/spec.html" || {
  echo "fixture missing QA harness setup" >&2; exit 1
}

# --- simulate clean repair: remove audit section and ticket link ---
# Remove the audit section (opening tag through closing tag)
sed -i '/<section data-anchor="audit">/,/<\/section>/d' "$TMP/spec.html"
# Remove the source-issues paragraph containing the ticket link
sed -i '/<p data-anchor="source-issues">/d' "$TMP/spec.html"
# Remove the QA harness section (stub and capture file)
sed -i '/<section data-anchor="qa-harness">/,/<\/section>/d' "$TMP/spec.html"

# --- post-conditions ---
if grep -q 'data-anchor="audit"' "$TMP/spec.html"; then
  echo "audit section still present after repair" >&2; exit 1
fi
if grep -q 'linear.app' "$TMP/spec.html"; then
  echo "ticket link still present after repair" >&2; exit 1
fi

if grep -q 'posthog-captures.jsonl\|provider stub' "$TMP/spec.html"; then
  echo "QA harness setup still present after repair" >&2; exit 1
fi

# Every acceptance criterion must survive
for anchor in acceptance-first acceptance-second; do
  grep -q "data-anchor=\"$anchor\"" "$TMP/spec.html" || {
    echo "acceptance criterion $anchor missing after repair" >&2; exit 1
  }
done

# --- skill texts carry the product-term criterion rules ---
SHAPE="$ROOT/skill/shape-spec/SKILL.md"
AUTHORING="$ROOT/skill/shape-spec/references/authoring.md"
REPAIR="$ROOT/skill/repair-specs/SKILL.md"
need() { # file phrase
  grep -qF -- "$2" "$1" || { echo "$1 missing: $2" >&2; exit 1; }
}
NO_SCREEN='proven by a unit test: no screen shows it'
for f in "$SHAPE" "$AUTHORING" "$REPAIR"; do
  need "$f" "$NO_SCREEN"
  need "$f" "fixture user"
  if grep -qi 'annotateanything' "$f"; then
    echo "$f names a peer project" >&2; exit 1
  fi
done
need "$SHAPE" "product terms"
need "$SHAPE" "no QA harness"
need "$AUTHORING" "QA harness setup is not product behavior"
need "$AUTHORING" "product terms"
if grep -qF 'terminal output or a named file' "$AUTHORING"; then
  echo "$AUTHORING still allows a file-backed Then" >&2; exit 1
fi
need "$REPAIR" "QA harness setup"
need "$REPAIR" "artifact path"
need "$REPAIR" "QA docs"

echo "repair-specs tests passed"
