#!/bin/sh
# Skill-text test (prompt-first-shaping#acceptance-accept-no-status, #finish-no-status):
# on Accept spec the agent records the acceptance in no Spec Chat file, the spool's hand-off
# is Spec Chat's only view of it, and no committed spec carries a Status line or acceptance mark.
set -eu

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
SHAPE="$ROOT/skill/shape-spec/SKILL.md"
REVIEW="$ROOT/skill/review-spec/SKILL.md"
need() { # file phrase
  grep -qF -- "$2" "$1" || { echo "$1 missing: $2" >&2; exit 1; }
}

# review-spec: Accept spec is recorded nowhere in Spec Chat; the spool hand-off is its only view
need "$REVIEW" 'Run `scripts/review-host.py reviewed --id <resource id>`; it records no acceptance, since that hand-off in the spool is Spec Chat'"'"'s only view of the acceptance'
need "$REVIEW" "Record it in no Spec Chat file"
need "$REVIEW" "a caller that keeps acceptances quotes that hand-off in its own record"
if grep -nF -- '--accepted' "$REVIEW" "$SHAPE" >&2; then echo "skills still name --accepted" >&2; exit 1; fi
need "$REVIEW" "the spec carries no Status line or acceptance mark"
# shape-spec: Finish leaves the spec as reviewed
need "$SHAPE" "Spec acceptance leaves the spec as reviewed: add no Status line or acceptance mark to it"
need "$SHAPE" "record it in no Spec Chat file, since the spool's Accept spec hand-off is Spec Chat's only view of it"
need "$SHAPE" "a caller that keeps acceptances quotes that hand-off in its own record"

# committed specs: no Status line, no acceptance mark
for f in "$ROOT"/docs/specs/*.spec.html; do
  if grep -nE '<p[^>]*>[[:space:]]*Status:|data-anchor="status"|data-accepted|accepted by human spec acceptance' "$f" >&2; then
    echo "$f carries a Status line or acceptance mark" >&2; exit 1
  fi
done

echo "skill-accept-no-status tests passed"
