#!/bin/sh
# Skill-text test (prompt-first-shaping#acceptance-directions-seed,
# #acceptance-directions-kept, #flow-mockups; mockups#directions,
# #directions-next, #set-target): shaping writes a screen's directions only in
# its first round, each with what it gives up and the pick with its reason, and
# the round after keeps the picked direction alone; review reads a Pick as an
# answer on the directions list.
set -eu

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
SHAPE="$ROOT/skill/shape-spec/SKILL.md"
AUTHORING="$ROOT/skill/shape-spec/references/authoring.md"
REVIEW="$ROOT/skill/review-spec/SKILL.md"
need() { # file phrase
  grep -qF -- "$2" "$1" || { echo "$1 missing: $2" >&2; exit 1; }
}
never() { # file phrase
  if grep -qF -- "$2" "$1"; then echo "$1 still holds: $2" >&2; exit 1; fi
}

# shape-spec step 4: directions in a first round only, the rest removed after
need "$SHAPE" "In a screen's first round, show its directions: two, or three when one differs in kind, each with the line saying what it gives up, your pick with its reason"
need "$SHAPE" "the round after keeps the direction the reviewer picked, else your pick, and removes the rest, so later rounds show none."

# authoring: the target-state rule names directions as the one alternative
never "$AUTHORING" "never a before, history, or alternatives"
need "$AUTHORING" "Its only alternatives are a screen's first-round [directions](#directions), which the round after removes."
# authoring: when, the list's markup, and the next round
need "$AUTHORING" "### Directions"
need "$AUTHORING" "Write them only for a screen the change adds or changes, and only in the round that first shows it"
need "$AUTHORING" '<ul data-mockup-directions data-anchor="directions">'
need "$AUTHORING" "The round after brings the screen back to one direction, whatever its hand-off holds: the one the newest Pick names, else your pick."
need "$AUTHORING" "remove the other directions and the directions list, and settle a Pick by replying on its thread"
need "$AUTHORING" "| The page, outside the frames | \`<ul data-mockup-directions data-anchor=\"directions\">\`, one \`<li data-direction>\` per direction |"
need "$AUTHORING" "and one directions list, its own anchor in \`data-anchor\`, of two or three directions with unique names and unique keys, exactly one of them the pick with its reason"
need "$AUTHORING" "and on a frame naming the pick, a direction the list lacks, or none in an empty \`data-direction\`."

# review-spec: the scan's answer line for a Pick, and the round after
need "$REVIEW" "A Pick on a direction is an answer on the directions list: its \`answer\` line names the list's anchor as \`#<question>\`"
need "$REVIEW" "A mockup that shows directions goes back to one direction in the round after, whatever the hand-off holds: the one the newest Pick names, else the agent's pick"

echo "skill-directions tests passed"
