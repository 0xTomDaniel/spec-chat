#!/bin/sh
# Repair-specs skill-text test: the shaping and repair skill texts carry the
# product-term criterion rules, the presentation repair, the split check, and the drift check.
set -eu

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)

# --- skill texts carry the product-term criterion rules ---
SHAPE="$ROOT/skill/shape-spec/SKILL.md"
AUTHORING="$ROOT/skill/shape-spec/references/authoring.md"
REPAIR="$ROOT/skill/repair-specs/SKILL.md"
need() { # file phrase
  grep -qF -- "$2" "$1" || { echo "$1 missing: $2" >&2; exit 1; }
}
NO_SCREEN='proven by a test through a public entry point: no screen shows it'
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
# fixture-only Given, final-screen Then, one screen, names not positions
need "$AUTHORING" "A Given holds only state a fixture can set"
need "$AUTHORING" "never only that something closed"
need "$AUTHORING" "A Then reads from one screen"
need "$AUTHORING" "never by screen position or order"
need "$AUTHORING" "the structural validator does not"
need "$AUTHORING" "the outcome lies outside the page"
need "$SHAPE" "one screen per Then"
need "$SHAPE" "never by screen position or order"
need "$SHAPE" "the validator does not check them"
need "$REPAIR" "move that gesture into the When"
# When-action and then-step (prompt-first-shaping#complete-criterion-when-action,
# #complete-criterion-then-step; repair-specs#acceptance-criteria-when-look, #acceptance-criteria-then-step)
need "$AUTHORING" "A When step is something the user does"
need "$AUTHORING" "looking or reading is not a step, so what the user sees belongs in the Then"
need "$AUTHORING" "When the learner opens the graph, Then its script control reads Zhuyin"
need "$AUTHORING" "When a Then compares what the screen shows before and after the When"
need "$AUTHORING" 'the When joins each step to the next with "then"'
need "$AUTHORING" "never with a comma alone"
need "$SHAPE" "When steps that are only things the user does, never looking or reading"
need "$SHAPE" 'a When whose Then compares the screen before and after it joining each step to the next with "then", never a comma alone'
need "$REPAIR" "When a When step says the user looks at or reads something, replace it with the step the user does to reach that screen"
need "$REPAIR" "move what the user sees into the Then"
need "$REPAIR" "how the user reaches what a When only looks at"
need "$REPAIR" "a comma alone separates two When steps, join them with \`then\` and keep the Then's wording"
need "$REPAIR" "one criterion per screen"
need "$REPAIR" "what the screen shows after the dismissal"
need "$REPAIR" "by screen position or order"
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
# split check (repair-specs #split, #acceptance-split-*): after the repairs, before drift,
# human-settled, anchors moved unchanged, never written into a spec
need "$REPAIR" "## Split check"
need "$REPAIR" "After the six repairs and before the drift check, commit the repaired specs and ADRs, then check each spec"
need "$REPAIR" "for the split check the repo's Git history"
need "$REPAIR" "all six repair types, the split check, and the drift check"
need "$REPAIR" "groups that no criterion joins"
need "$REPAIR" "unrelated changes repeatedly editing it at the same time"
# history signal is judgment; merges are one way to read it, squashed or linear history another
need "$REPAIR" "judge from the spec's Git history whether unrelated changes edit it at the same time"
need "$REPAIR" "One way to read it: where branches merge"
need "$REPAIR" "Where history is squashed or linear"
need "$REPAIR" "size alone never splits a spec"
need "$REPAIR" "naming the signal, each new spec's file and title, and the stories, criteria, sections, and boundaries each new spec takes"
need "$REPAIR" "never for a change, fix, or lane"
need "$REPAIR" "split, or leave it"
need "$REPAIR" "with its wording and its anchor unchanged"
need "$REPAIR" "Point every link in the repo to a moved anchor at its new spec"
need "$REPAIR" "list each moved anchor with its old and new spec in the split commit message"
need "$REPAIR" "one commit of its own"
need "$REPAIR" "On leave, change no spec."
# scoped to repair: shaping links this section and asks through an open TBD (prompt-first-shaping #flow-split)
need "$REPAIR" "Repair asks in the run, never in the spec: it never splits a spec without the human's choice, and writes nothing about a split into a spec as a finding, note, or TBD"
need "$REPAIR" "In repair, the split check, and so the run, is not done until the human has settled every proposal"
need "$REPAIR" "the split check is waiting on the unsettled proposal"
need "$REPAIR" "A split the human chose moves an anchor, unchanged, to its new spec"
need "$REPAIR" "Commit on the current branch at each step: the repaired specs and ADRs as the split check starts, each split the human chose as one commit of its own"
need "$REPAIR" "When the drift check completes, register each changed or new spec"
if grep -qF 'When repairs, the split check, and the drift check complete' "$REPAIR"; then
  echo "$REPAIR still commits the repairs at the end of the run" >&2; exit 1
fi
line_of() { # file heading: its first line number, or fail when the heading is missing
  n=$(grep -nF -- "$2" "$1" | head -n 1 | cut -d: -f1)
  [ -n "$n" ] || { echo "$1 missing heading: $2" >&2; return 1; }
  echo "$n"
}
p=$(line_of "$REPAIR" "### 6. Presentation") || exit 1
s=$(line_of "$REPAIR" "## Split check") || exit 1
d=$(line_of "$REPAIR" "## Drift check") || exit 1
if [ "$p" -ge "$s" ] || [ "$s" -ge "$d" ]; then
  echo "$REPAIR split check is not between presentation and drift" >&2; exit 1
fi
# shaping proposes a split as an open TBD (prompt-first-shaping #flow-split, #acceptance-split-*)
need "$SHAPE" "split check's signals"
need "$SHAPE" "the change's stories included"
need "$SHAPE" "write an open TBD at the change proposing that split and naming each new spec"
need "$SHAPE" "one answer button to split and one to keep one spec"
need "$SHAPE" "size alone never raises it"
need "$SHAPE" "as its own commit, before the change moves to its new spec"
# drift check, after repairs and split check, human-settled, read-only on code
need "$REPAIR" "## Drift check"
need "$REPAIR" "After the six repairs and the split check"
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
# catch-up: the batch rule checks through the agent read, idempotent, no onboarding status read
need "$REPAIR" "## Catch-up"
need "$REPAIR" "jev-read.py"
need "$REPAIR" "--not-a-rule"
need "$REPAIR" "--not-for-spec"
need "$REPAIR" "Mark it with a short name; Not a project rule; or skip"
need "$REPAIR" "Cover it; add a reason line; or Not for this spec"
need "$REPAIR" "Step 2 runs after step 1"
need "$REPAIR" "idempotent"
need "$REPAIR" "the rule checks need Jev"
if grep -qF 'onboarding.toml' "$REPAIR"; then
  echo "$REPAIR still reads onboarding status" >&2; exit 1
fi
# shaping asks the human per own-spec candidate
need "$SHAPE" "--not-a-rule"
need "$SHAPE" "data-project-rule"
need "$SHAPE" "Candidates on other specs are only listed"
if grep -qF 'Never confirm or reject a rule' "$SHAPE"; then
  echo "$SHAPE still never asks about a candidate" >&2; exit 1
fi
if grep -qiw 'five' "$REPAIR"; then
  echo "$REPAIR still counts five repair types" >&2; exit 1
fi

echo "repair-specs tests passed"
