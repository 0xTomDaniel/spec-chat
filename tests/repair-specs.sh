#!/bin/sh
# Repair-specs skill-text test: the shaping and repair skill texts carry the
# product-term criterion rules, the presentation repair, and the drift check.
set -eu

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)

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
# presentation repair, sixth after criteria
need "$REPAIR" "clean, home, contradict, ADR, criteria, presentation"
need "$REPAIR" "### 6. Presentation"
need "$REPAIR" "visual quality contract"
need "$REPAIR" "never what it says"
need "$REPAIR" "ASCII-art figure"
need "$REPAIR" "page-level style block"
need "$REPAIR" "shared stylesheet"
need "$REPAIR" "preflight"
need "$REPAIR" "Markdown ADRs have no figures"
need "$REPAIR" "criterion-rule, and presentation repairs still run"
# drift check, after repairs, human-settled, read-only on code
need "$REPAIR" "## Drift check"
need "$REPAIR" "After the six repairs"
need "$REPAIR" "never edit it"
need "$REPAIR" "Write no spec-to-code index"
need "$REPAIR" "Code committed together with the spec"
need "$REPAIR" "covers in spirit"
need "$REPAIR" "Update the spec to match the code (default)"
need "$REPAIR" "Fix the code, saying why"
need "$REPAIR" "adjust the spec, fix the code later, or leave it"
need "$REPAIR" "Nothing about drift is written into a spec"
need "$REPAIR" "not done until the human has settled every suggestion"
need "$REPAIR" "not checked against code"
need "$REPAIR" "Run all six repair types and the drift check"
if grep -qiw 'five' "$REPAIR"; then
  echo "$REPAIR still counts five repair types" >&2; exit 1
fi

echo "repair-specs tests passed"
