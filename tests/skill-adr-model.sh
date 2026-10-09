#!/bin/sh
# Skill-text test (prompt-first-shaping#tdd-adr): the shaping skill states the
# ADR ownership and supersede rule, and the repair skill lists the ADR repair.
set -eu

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
SHAPE="$ROOT/skill/shape-spec/SKILL.md"
REPAIR="$ROOT/skill/repair-specs/SKILL.md"
need() { # file phrase
  grep -qF -- "$2" "$1" || { echo "$1 missing: $2" >&2; exit 1; }
}
refuse() { # file phrase
  if grep -qF -- "$2" "$1"; then echo "$1 still says: $2" >&2; exit 1; fi
}

# shape-spec: ownership (source-adr, source-rules, source-mechanics)
need "$SHAPE" "An ADR owns one hard-to-reverse decision: its context, the decision, why, alternatives rejected, and consequences, kept short; it holds no policy section and no mechanics."
need "$SHAPE" "A repo-wide rule lives once, in its home spec, as an acceptance criterion whose text says it applies to every feature"
need "$SHAPE" "never restates it"
need "$SHAPE" "neither a spec nor an ADR states them"
refuse "$SHAPE" "the policy and mechanics that follow from it"
# shape-spec: supersede (complete-adr-immutable)
need "$SHAPE" "An ADR is immutable once committed to the base branch"
need "$SHAPE" "Superseded by ADR NNNN (date)"

# repair-specs: ADR repair, order, six types, reads ADRs
need "$REPAIR" "### 4. ADR"
need "$REPAIR" "Run in this fixed order: clean, home, contradict, ADR, criteria, presentation."
need "$REPAIR" "Superseded by ADR NNNN (date)"
need "$REPAIR" "all six repair types"
need "$REPAIR" "the repo's specs and ADRs"
refuse "$REPAIR" "five repair types"

echo "skill-adr-model tests passed"
