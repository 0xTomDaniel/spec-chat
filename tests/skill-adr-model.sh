#!/bin/sh
# Skill-text test (prompt-first-shaping#tdd-adr): the shaping skill states the
# ADR ownership and supersede rule and the repo-wide rule's candidate ask, and
# the repair skill lists the ADR repair.
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
need "$SHAPE" "A repo-wide rule, a promise a later feature could break (project-rules#declare-promise) and never a design-system convention, lives once, in its home spec, as an acceptance criterion whose text says it applies to every feature and that its home spec marks as a project rule"
need "$SHAPE" "a project keeps at most one rule for its design system, the umbrella"
need "$SHAPE" "never restates it"
need "$SHAPE" "neither a spec nor an ADR states them"
refuse "$SHAPE" "the policy and mechanics that follow from it"
# shape-spec: look and motion mechanics (prompt-first-shaping#acceptance-look-no-mechanics), the bullet and the
# step 5 warning line, each by its own words, not the implementation mechanics bullet's
need "$SHAPE" "Look and motion mechanics, such as animation timings and their order, easing, colors, and spacing, belong to the design system, its design document and shared parts; a spec states what the user sees and links the design system for how it looks or moves"
need "$SHAPE" "A timing the user feels is an outcome and stays one"
need "$SHAPE" "It also passes but warns on each clause the change adds or edits that states look or motion mechanics: a color value, an easing curve, or two or more durations under a second in one clause or across the rows of one table"
need "$SHAPE" "answer each by moving the mechanics to the design system and linking it, or by stating the outcome the user feels"
# shape-spec: the candidate ask (project-rules#agent-ask, #acceptance-shared-part)
need "$SHAPE" "is it a repo-wide rule, a promise a later feature could break?"
need "$SHAPE" "Ask no rule question about a criterion the read does not list as a candidate"
# shape-spec: supersede (complete-adr-immutable)
need "$SHAPE" "An ADR is immutable once committed to the base branch"
need "$SHAPE" "Superseded by ADR NNNN (date)"

# repair-specs: ADR repair, order, seven types, reads ADRs; the supersede rule by reference
need "$REPAIR" "### 4. ADR"
need "$REPAIR" "Run in this fixed order: clean, home, contradict, ADR, criteria, presentation, mockups."
need "$REPAIR" "the supersede rule ([shape step 10](../shape-spec/SKILL.md#shape))"
need "$REPAIR" "gains only the supersede rule's line"
refuse "$REPAIR" "Superseded by ADR NNNN (date)"
need "$REPAIR" "all seven repair types"
need "$REPAIR" "the repo's specs and ADRs"
refuse "$REPAIR" "five repair types"
refuse "$REPAIR" "six repair types"

echo "skill-adr-model tests passed"
