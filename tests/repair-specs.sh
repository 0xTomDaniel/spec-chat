#!/bin/sh
# Repair-specs skill-text test: the repair skill reads its rules from the shaping contract by
# reference (repair-specs #rule-source, #acceptance-rule-source-text), holds only repair's own how,
# runs mockup repair seventh, validates whole, and keeps its split check, drift check, and catch-up;
# the shaping texts carry the rules repair links; no skill text names a peer project.
set -eu

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)

# --- skill texts ---
SHAPE="$ROOT/skill/shape-spec/SKILL.md"
AUTHORING="$ROOT/skill/shape-spec/references/authoring.md"
REPAIR="$ROOT/skill/repair-specs/SKILL.md"
need() { # file phrase
  grep -qF -- "$2" "$1" || { echo "$1 missing: $2" >&2; exit 1; }
}
refuse() { # file phrase
  if grep -qF -- "$2" "$1"; then echo "$1 still says: $2" >&2; exit 1; fi
}
for f in "$SHAPE" "$AUTHORING" "$REPAIR"; do
  if grep -qi 'annotateanything' "$f"; then
    echo "$f names a peer project" >&2; exit 1
  fi
done

# --- rule source (repair-specs #rule-source-what, #acceptance-rule-source-text) ---
# Every link resolves to its rule: a file that exists, and the heading or data-anchor it names.
# Each repair type opens with a Rules line linking the shaping contract, and the skill repeats no
# sentence of the shaping skill or its authoring reference.
python3 - "$REPAIR" "$SHAPE" "$AUTHORING" <<'PY'
import os, re, sys
repair, shape, authoring = sys.argv[1:]
text = open(repair).read()
fail = []

def slugs(path):
    out = set()
    for line in open(path):
        m = re.match(r"#+\s+(.*)", line)
        if m:
            s = re.sub(r"[^\w\- ]", "", m.group(1).strip().lower())
            out.add(s.replace(" ", "-"))
    return out

def resolves(target):
    path, _, frag = target.partition("#")
    file = os.path.normpath(os.path.join(os.path.dirname(repair), path)) if path else repair
    if not os.path.isfile(file):
        return False
    if not frag:
        return True
    if file.endswith(".md"):
        return frag in slugs(file)
    page = open(file).read()
    return f'data-anchor="{frag}"' in page or f'id="{frag}"' in page

links = re.findall(r"\]\(([^)\s]+)\)", text)
for target in links:
    if not target.startswith(("http:", "https:")) and not resolves(target):
        fail.append(f"link does not resolve: {target}")

contract = ("../shape-spec/", "../../docs/specs/")
sections = re.split(r"^### ", text, flags=re.M)[1:]
types = [s for s in sections if re.match(r"\d\. ", s)]
if [s.split("\n", 1)[0] for s in types] != ["1. Clean", "2. Home", "3. Contradict", "4. ADR", "5. Criteria", "6. Presentation", "7. Mockups"]:
    fail.append("repair types are not the seven in order")
for s in types:
    body = s.split("\n", 1)[1].strip()
    first = body.split("\n", 1)[0]
    if not first.startswith("Rules: ") or not any(f"]({c}" in first for c in contract):
        fail.append(f"{s.split(chr(10), 1)[0]} opens with no Rules line linking the shaping contract")

flat = " ".join(text.split())
for source in (shape, authoring):
    for sentence in re.split(r"(?<=[.;:])\s+", " ".join(open(source).read().split())):
        if len(sentence.split()) >= 9 and sentence in flat:
            fail.append(f"restates {os.path.basename(source)}: {sentence}")

if fail:
    sys.stderr.write("\n".join(f"{repair}: {f}" for f in fail) + "\n")
    sys.exit(1)
PY
need "$REPAIR" "Repair's rules are the shaping contract's: the [shaping skill](../shape-spec/SKILL.md), its [authoring reference](../shape-spec/references/authoring.md), and each spec they link for a rule."
need "$REPAIR" "this skill restates none"
need "$REPAIR" "none is grandfathered"
# the rules repair once restated in its own words are gone; their source holds them
for phrase in 'Zhuyin' 'sidebar collapsed' 'the first card' 'posthog-captures.jsonl' 'tf-admin' 'Q3 scripts' \
  'ASCII-art' 'page-level style block' 'Superseded by ADR NNNN' 'proven by a unit test' \
  'One sentence per line' 'Pretty-printed semantic-island JSON' 'looks at or reads something' \
  'by screen position or order' 'a comma alone'; do
  refuse "$REPAIR" "$phrase"
done
# repair's own how (repair-specs #rule-source-own): every anchor kept, proof clauses and rule-meeting
# text kept, never an invented fact, and what moves out listed in the commit message
need "$REPAIR" "Keep every anchor: never remove or rename one"
need "$REPAIR" "Keep every proof clause a criterion carries word for word"
need "$REPAIR" "leave word for word each criterion and mockup that already meets every rule"
need "$REPAIR" "Never invent a fact a rule needs"
need "$REPAIR" "write an open TBD (\`data-spec-tbd\`) at that clause naming what is missing"
need "$REPAIR" "List in the repair commit message what a pass moves out of a spec"
need "$REPAIR" "| A concrete value, selector, or path | The target's qa.toml hint file |"
need "$REPAIR" "| A fixture user | The target's qa.toml, as that role's fixture user |"
need "$REPAIR" "| QA harness setup: stubs, capture files, readback tooling, fixture extensions | The target's qa.toml or QA docs |"
need "$REPAIR" "| An ADR's mechanics | The code and the change request |"

# --- the rules repair links live in the shaping contract ---
# When-action and then-step (prompt-first-shaping#complete-criterion-when-action,
# #complete-criterion-then-step; repair-specs#acceptance-criteria-when-look, #acceptance-criteria-then-step)
need "$AUTHORING" "A When step is something the user does"
need "$AUTHORING" "looking or reading is not a step, so what the user sees belongs in the Then"
need "$AUTHORING" "When the learner opens the graph, Then its script control reads Zhuyin"
need "$AUTHORING" "When a Then compares what the screen shows before and after the When"
need "$AUTHORING" 'the When joins each step to the next with "then"'
need "$AUTHORING" "never with a comma alone"
need "$AUTHORING" "and at repair to every criterion"
need "$SHAPE" "When steps that are only things the user does, never looking or reading"
need "$SHAPE" 'a When whose Then compares the screen before and after it joining each step to the next with "then", never a comma alone'
# presentation rules once only in repair's text (repair-specs #repair-presentation)
need "$AUTHORING" "A figure takes its colors from the shared stylesheet, never fixed colors of its own"
need "$AUTHORING" "never as ASCII art in a preformatted block, a placeholder, or a picture the text has moved past"
need "$AUTHORING" "Headings step down one level at a time, never skipping one"
need "$AUTHORING" "a callout uses markup the shared stylesheet already styles"

# --- repair types: mockups seventh, any other rule after it (repair-specs #repair-order, #rule-source-new) ---
need "$REPAIR" "Run in this fixed order: clean, home, contradict, ADR, criteria, presentation, mockups."
need "$REPAIR" "all seven repair types"
need "$REPAIR" "never what it says"
need "$REPAIR" "The text governs any figure that disagrees with it"
need "$REPAIR" "preflight"
need "$REPAIR" "Markdown ADRs have no figures"
need "$REPAIR" "criterion-rule, presentation, and mockup repairs still run"
# mockup repair (repair-specs #repair-mockups, #repair-mockups-review): shipped route, drawn fallback, no product code
need "$REPAIR" "[production fidelity](../../docs/specs/mockups.spec.html#fidelity)"
need "$REPAIR" "on every screen a spec states"
need "$REPAIR" "naming the shipped screen's route, with no code to write"
need "$REPAIR" "illustrate each spec anchor the old mockup illustrated"
need "$REPAIR" "Keep the mockup's file and each anchor of a part the screen still shows"
need "$REPAIR" "Write no product code, a prototype included."
need "$REPAIR" "[prototype address](../../docs/specs/mockups.spec.html#prototype-address)"
need "$REPAIR" "runs as a pass of its own"
# whole-spec validation (repair-specs #rule-source-validator)
need "$REPAIR" "## Validate"
need "$REPAIR" "validate-style.py --whole-spec <repo-root> <spec.html> <run-base>"
need "$REPAIR" "fails a proof clause dropped since the run's base"
# the description names what the skill does now
desc=$(sed -n 's/^description: //p' "$REPAIR")
for phrase in "mockups" "never restates" "writing no product code"; do
  case "$desc" in *"$phrase"*) ;; *) echo "$REPAIR description missing: $phrase" >&2; exit 1 ;; esac
done

line_of() { # file heading: its first line number, or fail when the heading is missing
  n=$(grep -nF -- "$2" "$1" | head -n 1 | cut -d: -f1)
  [ -n "$n" ] || { echo "$1 missing heading: $2" >&2; return 1; }
  echo "$n"
}
p=$(line_of "$REPAIR" "### 6. Presentation") || exit 1
m=$(line_of "$REPAIR" "### 7. Mockups") || exit 1
o=$(line_of "$REPAIR" "### Any rule no type covers") || exit 1
v=$(line_of "$REPAIR" "## Validate") || exit 1
s=$(line_of "$REPAIR" "## Split check") || exit 1
d=$(line_of "$REPAIR" "## Drift check") || exit 1
if [ "$p" -ge "$m" ] || [ "$m" -ge "$o" ] || [ "$o" -ge "$v" ] || [ "$v" -ge "$s" ] || [ "$s" -ge "$d" ]; then
  echo "$REPAIR passes are not in order: presentation, mockups, any other rule, validate, split, drift" >&2; exit 1
fi

# --- split check (repair-specs #split, #acceptance-split-*): after the repairs, before drift,
# human-settled, anchors moved unchanged, never written into a spec ---
need "$REPAIR" "## Split check"
need "$REPAIR" "After the seven repairs pass [validation](#validate) and before the drift check, check each spec"
need "$REPAIR" "for the split check the repo's Git history"
need "$REPAIR" "the split check, and the drift check"
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
need "$REPAIR" "a split the human chose moves an anchor, unchanged, to its new spec"
need "$REPAIR" "Commit on the current branch at each step: the repaired specs, mockups, and ADRs as validation starts, each fix validation needs, each split the human chose as one commit of its own"
need "$REPAIR" "When the drift check completes, register each changed or new spec"
refuse "$REPAIR" "When repairs, the split check, and the drift check complete"
# shaping proposes a split as an open TBD (prompt-first-shaping #flow-split, #acceptance-split-*)
need "$SHAPE" "split check's signals"
need "$SHAPE" "the change's stories included"
need "$SHAPE" "write an open TBD at the change proposing that split and naming each new spec"
need "$SHAPE" "one answer button to split and one to keep one spec"
need "$SHAPE" "size alone never raises it"
need "$SHAPE" "as its own commit, before the change moves to its new spec"

# --- drift check, after repairs and split check, human-settled, read-only on code ---
need "$REPAIR" "## Drift check"
need "$REPAIR" "After the seven repairs and the split check"
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

# --- catch-up: the batch rule checks through the agent read, idempotent, no onboarding status read ---
need "$REPAIR" "## Catch-up"
need "$REPAIR" "jev-read.py"
need "$REPAIR" "--not-a-rule"
need "$REPAIR" "--not-for-spec"
need "$REPAIR" "Mark it with a short name; Not a project rule; or skip"
need "$REPAIR" "Cover it; add a reason line; or Not for this spec"
need "$REPAIR" "Step 2 runs after step 1"
need "$REPAIR" "idempotent"
need "$REPAIR" "the rule checks need Jev"
refuse "$REPAIR" "onboarding.toml"
# shaping asks the human per own-spec candidate
need "$SHAPE" "--not-a-rule"
need "$SHAPE" "data-project-rule"
need "$SHAPE" "Candidates on other specs are only listed"
refuse "$SHAPE" "Never confirm or reject a rule"
for count in five six; do
  if grep -qiw "$count" "$REPAIR"; then
    echo "$REPAIR still counts $count repair types" >&2; exit 1
  fi
done

echo "repair-specs tests passed"
