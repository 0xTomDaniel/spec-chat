#!/bin/sh
# Skill-text test (prompt-first-shaping#acceptance-grilling-choices,
# #grilling-choices): shaping and review offer a decision question's options as
# answer buttons in its TBD block, the recommended one marked.
set -eu

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
SHAPE="$ROOT/skill/shape-spec/SKILL.md"
AUTHORING="$ROOT/skill/shape-spec/references/authoring.md"
REVIEW="$ROOT/skill/review-spec/SKILL.md"
need() { # file phrase
  grep -qF -- "$2" "$1" || { echo "$1 missing: $2" >&2; exit 1; }
}

# shape-spec Review shaping: the general rule, and the reconcile TBD follows it
need "$SHAPE" "A question whose answer is one of a few options, such as a decision or a call to check, offers each option as an answer button in its TBD block"
need "$SHAPE" "the recommended option's button text ending in \`(recommended)\`"
need "$SHAPE" "an answer no button offers takes an ordinary comment on that block"
need "$SHAPE" "with an answer button per option and your recommendation marked"

# authoring: markup is data-spec-choice inside the anchored TBD block
need "$AUTHORING" "a \`button\` carrying \`data-spec-choice\`, whose value is the"
need "$AUTHORING" "data-spec-tbd>How many retries before checkout fails?"
need "$AUTHORING" "data-spec-choice=\"Three retries\">Three retries (recommended)</button>"
need "$AUTHORING" "data-spec-choice=\"One retry\">One retry</button>"

# review-spec loop: a decision goes into the spec as a TBD with buttons
need "$REVIEW" "A decision you need from the human is never a question in a reply: write it into the spec as an open TBD block"
need "$REVIEW" "offering each option as an answer button, the recommended option's button text ending in \`(recommended)\`"

echo "skill-grilling-choices tests passed"
