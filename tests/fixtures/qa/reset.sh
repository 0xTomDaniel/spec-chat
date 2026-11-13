#!/bin/sh
# QA fixture reset (qa.toml fixture.spec_review_site): rebuild the served spec repo
# and the review registry under $QA_ROOT. base/ is the compared base commit, head/
# the specs under shaping on top of it. Jev records and the warning ledger under
# $QA_ROOT/state are kept: the running service holds them in memory too, and the
# fake is deterministic, so they match every reset's identical commits.
#
# Capture resets once per story, but criteria add drafts and hand-offs to the spec's
# on-disk spool. So every collection below is its own clean copy of the one repo,
# served at /<collection>/docs/specs/<spec>: qa-fixture is the story entry, and each
# other collection is one criterion's start, named by its qa.toml nav hint. Review-state
# collections also get their seeded spool and agent edit from spools.py. The lanes, qa-1 to qa-19 and qa-21, are lane keys, so
# the review index shows them as lanes; every other collection settles. Each is copied here and registered on its
# service, with the specs it registers, from their one table, spools.py LANES; every other collection registers the
# four specs on the review service. qa-1 and qa-2 hold the four, their specs holding open TBDs or awaiting
# acceptance. QA-2 also holds a spec registered from a worktree since removed (its root
# is never created), so its card shows that row Stale (review-index #acceptance-index-stale).
# qa-3 to qa-7 are index gate lanes (review-index #index-gates), each registering Report filters
# alone (no open decision), its one thread seeded by spools.py: with the agent, resolved twice
# (qa-5 is pressed Accept spec), replied, and a draft. qa-15 registers Report page alone, pressed Accept spec before
# its set gained 03 Export dialog (review-index #acceptance-index-gate-sent-new-mockup); qa-16 Report filters alone,
# whose spool cannot be read (#acceptance-index-stale-unreadable). qa-17 to qa-19 register Report filters alone, each at
# the stage spools.py STAGES names (threads #stage-tag): qa-18 holds a comment written at an earlier stage, and qa-19 also
# the agent's reply to it, written at another. qa-21 registers Report filters alone at Spec review, its threads written
# there, one waiting on the reviewer (threads #acceptance-waiting-root).
#
# The evidence provider is a peer outside this repository, so its answers are fixture data
# (annotateanything jev-capture #qa-rules-real-data): $QA_ROOT/evidence holds its declared
# bundle page and review facts answer, the evidence service (qa.toml) serves them as files, and
# the review-plugged service's registry folder holds the provider file pointing at it
# (review-service #providers-evidence), and the stage of each lane spools.py STAGES names. Its lanes qa-8 (named accepted at the head commit) and
# qa-9 (named a proof bundle) register Report filters alone; qa-10 registers Weekly report (open TBDs), Report filters
# with the agent (spools.py), and long-title.spec.html, a long title waiting on acceptance, so one index shows the
# attention, running, and pass marks and a title that wraps beside its gate word (review-index #index-marks,
# #index-design). The answer and the provider file
# name the evidence service's address, so they are written once it runs (QA_URL_EVIDENCE, every
# reset capture runs before a criterion); the reset before services start leaves both out. The
# review service has no provider file: it is the box with no evidence provider.
#
# The review-sections service's registry holds exactly the parts review-index #index-sections names: the qa-11 lane,
# Weekly report alone (open TBDs, a decision open); the qa-12 lane, Report filters alone with its one thread handed off
# and not answered (spools.py), so all its specs are with the agent; and the main collection, a slug that is no lane
# key, holding the four specs. A registry serves its rows only, so no spec there is served without a row.
#
# A rule is its criterion's mark in its home spec (project-rules #declare): onboarding.spec.html
# marks its onboarding criterion. Dismissals live in the warning ledger by project and spec
# path, and hold for good, so each reset registers a fresh project: a story's clicks never
# reach the next one. rule-candidate is its own project whose onboarding criterion is
# unmarked, so it is a candidate on its home spec (qa.toml nav); rule-main-only is its own project registering its report
# spec alone, its origin/main the base commit, which holds the rule's home, so only its project's main holds that home
# and no row serves it, while qa-fixture's lane serves it, as qa-1 and the other collections registering the four specs
# do (project-rules #q-rule-source, #card-source); convention-no-candidate's onboarding spec gains an
# unmarked design-system convention, which the fake reads as this feature, so it is none (spools.py CONVENTION);
# rule-pending's report spec gains a sentence whose rule check the fake's general LLM holds unanswered until the service
# stops, so its rule check is pending while the page is open, and rule-answer's another, whose rule check it answers,
# missed, a few seconds after its first ask, the sentence naming this reset's project, so no answer the state kept from
# an earlier reset matches it (spools.py HELD, serve.py). Capture resets once per
# story, so a criterion that dismisses starts on its own project too (dismiss, dismiss-reload,
# not-a-rule): its click never hides the marker a sibling criterion of the story needs.
#
# print.spec.html's criteria name no story (jev-suggestions #coverage-declared) and its head only rewords its base;
# it is registered only in coverage-undeclared, so every other collection serves the same four specs.
#
# bar-dirty's report spec carries the agent's edit uncommitted in its worktree (spools.py), so it differs from its head
# (compared-range #acceptance-bar-dirty).
#
# The registry records a public base address, so the service, though on loopback, is public
# and answers a page with no origin (served-pages #served-private): the test host (host.py)
# serves the served-* collections' pages at its own address, in a frame with no origin. The review-second service's
# registry, public too, holds served-storage-return and served-storage-stored at the same roots, so their specs are on two
# review services (served-pages #served-storage), and served-storage-service at its own copy under site-second, so its
# spool is not the review service's: a comment the first service stores never shows on the second service's spec, which
# shows it only if the frame gave the second service's page the first service's items
# (#acceptance-served-storage-service). It has no wake provider, so only the review service wakes.
#
# The review service's wake provider (review-service #providers) is a fake: every owner is present, and each send
# appends the spec it wakes to $QA_ROOT/wakes.log, so a hand-off wakes its spec's owner
# (prompt-first-shaping #acceptance-mobile-handoff-wake) and a test counts the wakes. Each reset removes the log with the
# site, so it holds only this reset's wakes.
#
# Shell collections (shell-*) seed the spec page the test shell (shell.py) holds, its comment UI page beside it (spools.py
# SHELL), shell-host-item and shell-host-box a host page beside its specs (panes.py), shell-native and shell-box-picture
# the scope of the shell's own native canvas page (panes.py), and shell-mockup-step a walkthrough screen with a thread
# at its second step (spools.py WALKTHROUGHS). The review service's registry names the test shell's origin, once it runs
# (QA_URL_SHELL), as box setup's --shell does; shell-other, the same shell at another origin, is named nowhere.
#
# Threads collections (threads-*) are review-state collections too: each seeds Report filters' or Weekly report's threads
# as one criterion of threads.spec.html reads them (spools.py THREADS), and registers that spec alone. The video ones
# (threads-video*) register Report walkthrough, a spec showing a short video of the report page, and layer-video's host
# page shows it in an item (panes.py VIDEO): each holds the video at docs/walkthrough.webm (spools.py write_video).
#
# Comment-layer collections (host-*, one-thread-*, no-scope, no-service, layer-*) also get host pages beside their specs and
# their seeded threads from panes.py, once the registry names this reset's project; a collection's slug is the scope
# its pages' items name. A stage collection's page also opens on an evidence item in a scope that holds no spec, a
# scope row the review service's registry names (panes.py STAGE).
#
# Mockup collections (mockups: mockup-*, and the lanes qa-13 to qa-15) register report-page.spec.html,
# Report page, alone; no other collection registers it, so no other page shows its chips. Its mockup set,
# docs/specs/mockups/report-page/, holds three screens: 01 Report page, changed since the base, 02 Team page, unchanged,
# and 03 Export dialog, new since it; their cards, avatars, and totals are the repo's fixture data,
# fixtures/weekly-totals.json. The repo pins the shared design set (spec-chat's design-set.toml, copied into its base),
# and each mockup's styles are that set's tokens by name and value. spools.py seeds their threads on the spec's and the
# Report page mockup's spools; in mockup-agent-edit and mockup-mark-follows the agent's edit to that mockup lands once the
# open page first checks its file (serve.py). qa-13 to qa-15 are lanes, so the review index shows them as lanes.
# The walkthrough collections (mockup-step*, mockup-question*) add screens with walkthroughs to that set, new since the
# base, and mockup-questions and mockup-question-* their questions (spools.py WALKTHROUGHS). In next-screen-one the set
# holds 01 Report page alone (spools.py ONE_SCREEN).
#
# Prototype collections (prototypes: prototype-*) register Report page alone too, its set also holding Weekly report, a
# prototype mockup committed in two rounds, each round's videos in its spool (spools.py PROTOTYPES). They are the
# review-prototype service's registry, which records no public base address, so that service is private on loopback and
# each lane's prototype origin is <slug>-prototype.localhost at its port, which a browser reaches on loopback
# (review-service #prototype-proxy). Their rows name the fixture's running prototype (prototype.py, QA_URL_PROTOTYPE,
# once it runs) as the lane command's --prototype does; prototype-down's names a closed port, so its prototype does not
# answer. prototype-own-chrome's names the review-prototype service itself (QA_URL_REVIEW_PROTOTYPE), the lane's own
# review service run as its prototype, and registers Report spec too, the page its Report spec mockup frames (spools.py
# OWN_CHROME). screens-index is one too: its Report page spec indexes the set's four screens, Weekly report's holding an
# open thread and its open question (mockups #screen-index). So are directions, directions-pick, and
# directions-pick-panel, whose Weekly report names two directions, Team cards, the pick, and Team table, the
# prototype's reports/weekly-table, with no question and no round video; and directions-kept, where the reviewer's
# Pick of Team table is handed off and answered, Weekly report's second round Team table's route alone. In
# directions-drawn, a drawn collection, Team panel shows Team table's own frames beside its own (spools.py
# PROTOTYPE_DIRECTIONS, DRAWN_DIRECTIONS, KEPT_DIRECTIONS; mockups #directions).
set -eu
collections="qa-fixture rule-candidate rule-main-only convention-no-candidate rule-pending rule-answer tbd-later tbd-later-handoff tbd-open-highlight tbd-open-single
  next-tbd next-tbd-step next-tbd-handoff tbd-open-jump tbd-open-wrap mobile-composer mobile-handoff
  mobile-one-control mobile-count mobile-handoff-wake mobile-handoff-frame mobile-next-tbd
  block-target-enter block-target-space block-target-off
  choice-press choice-phone choice-thread choice-again choice-change choice-reload
  choice-handoff choice-comment-mode
  anchor-moved anchor-changed anchor-gone stale-page agent-scan name-reply typed-kept advice
  clear-thread dismiss dismiss-reload not-a-rule bar-dirty
  served-threads served-handoff served-index served-focus served-reload-handoff served-reload-outbox coverage-undeclared
  served-storage-service served-storage-return served-storage-stored served-storage-host-named
  host-draft host-starters host-handoff host-handoff-two host-reply host-live host-same-subject
  host-quote host-quote-gone host-only-comments host-phone host-frame one-thread-spec one-thread-host
  no-scope no-service layer-markup layer-stores layer-focus layer-stream layer-video host-place-scrolled host-place-grows
  layer-stage-lane layer-stage-none layer-stage-reply-lane
  threads-order threads-sides threads-replies threads-focus threads-focus-reply threads-pin threads-chip threads-sent threads-new
  threads-seen threads-resolved threads-waiting threads-question threads-answer threads-answer-same threads-colors
  threads-video threads-video-box threads-video-chip
  shell-held shell-alone shell-unnamed shell-close shell-pick shell-pick-key shell-wake shell-page-tools shell-publish
  shell-answer shell-show shell-pin-focus shell-handoff shell-accept shell-accept-waits shell-host-item shell-host-box
  shell-native shell-box-picture shell-phone shell-phone-id"
mockups="mockup-view mockup-comment-element mockup-comment-region mockup-comment-phone mockup-handoff mockup-one-control
  mockup-one-control-phone mockup-reply mockup-agent-edit mockup-mark-follows mockup-after mockup-accept-waits
  mockup-accept-offered mockup-steps mockup-step-thread mockup-step-pin mockup-questions mockup-question-answer
  mockup-question-words mockup-question-phone mockup-question-accept shell-mockup-step next-screen-one
  directions-drawn"
prototypes="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import spools; print(*spools.PROTOTYPES)' "$QA_FIXTURE")"
sections="main"
lanes="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import spools; print(*spools.LANES)' "$QA_FIXTURE")"
second="served-storage-return served-storage-stored"
second_own="served-storage-service"
site="$QA_ROOT/site"
site_second="$QA_ROOT/site-second"
serve="$QA_ROOT/serve"
serve_plugged="$QA_ROOT/serve-plugged"
serve_sections="$QA_ROOT/serve-sections"
serve_second="$QA_ROOT/serve-second"
serve_prototype="$QA_ROOT/serve-prototype"
evidence="$QA_ROOT/evidence"
repo="$site/.repo"
rm -rf "$site" "$site_second" "$evidence" "$serve_plugged/providers" "$QA_ROOT/wakes.log"
mkdir -p "$site" "$site_second" "$serve" "$serve_plugged/providers" "$serve_sections" "$serve_second" "$serve_prototype"
# Fixed identity and dates: every reset yields the same commits.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
  GIT_AUTHOR_NAME=qa GIT_AUTHOR_EMAIL=qa@example.invalid GIT_AUTHOR_DATE=2026-01-01T00:00:00Z \
  GIT_COMMITTER_NAME=qa GIT_COMMITTER_EMAIL=qa@example.invalid GIT_COMMITTER_DATE=2026-01-01T00:00:00Z
git init -q -b main "$repo"
cp -R "$QA_FIXTURE/base/." "$repo/"
cp -R "$QA_REPO_SPEC_CHAT/docs/specs/.viz" "$QA_REPO_SPEC_CHAT/docs/specs/.style" "$repo/docs/specs/"
cp "$QA_REPO_SPEC_CHAT/design-set.toml" "$repo/"
git -C "$repo" add -A
git -C "$repo" commit -qm base
base="$(git -C "$repo" rev-parse HEAD)"
cp -R "$QA_FIXTURE/head/." "$repo/"
git -C "$repo" add -A
git -C "$repo" commit -qm head
head="$(git -C "$repo" rev-parse HEAD)"
for collection in $collections $mockups $prototypes $lanes $sections; do
  cp -R "$repo" "$site/$collection"
done
for collection in $second_own; do
  cp -R "$repo" "$site_second/$collection"
done
rm -rf "$repo"
# rule-main-only's project main: origin/main where its branch left it, the base commit (project-rules #q-rule-source).
git -C "$site/rule-main-only" update-ref refs/remotes/origin/main "$base"
# rule-candidate, not-a-rule: the onboarding criterion unmarked in the working tree, a candidate (#q-candidate).
for collection in rule-candidate not-a-rule; do
  sed 's/ data-project-rule="[^"]*"//' "$site/$collection/docs/specs/onboarding.spec.html" > "$site/$collection/onboarding.tmp"
  mv "$site/$collection/onboarding.tmp" "$site/$collection/docs/specs/onboarding.spec.html"
done
# This reset's project, which also names rule-answer's held sentence (spools.py HELD).
project="qa-$(date +%s%N)"
# Review-state collections: seeded spools and agent edits (spools.py).
python3 "$QA_FIXTURE/spools.py" "$site" "$project"
# Registry: every spec of every collection, in this reset's projects. Python writes it
# so paths are escaped as TOML strings.
python3 - "$QA_ROOT" "$QA_FIXTURE" "$site" "$base" "$project" "$sections" "$second" "$site_second" "$second_own" "$mockups" $collections <<'PY'
import json, os, sys
qa_root, fixture, site, base, entry, sections, second, site_second, second_own, mockups, *collections = sys.argv[1:]
sys.path.insert(0, fixture)
from spools import FOUR, LANES, OWN_CHROME, PROTOTYPES, SHELL, SPEC_OF, THREADS
from panes import STAGE
# Box setup's --shell names the test shell's origin, once it runs (QA_URL_SHELL), as a shell (shell #shell-setting).
SHELLS = 'shells = %s\n' % json.dumps([os.environ["QA_URL_SHELL"].rstrip("/")]) if os.environ.get("QA_URL_SHELL") else ''
PUBLIC = '[process]\npublic_base = "https://review.qa.invalid"\n' + SHELLS + '\n'
own = {slug: entry + "-" + slug for slug in ("rule-candidate", "not-a-rule", "dismiss", "dismiss-reload", "rule-main-only")}
# A lane registers the specs LANES names; a mockup collection Report page alone; a threads or shell collection the one
# spec its threads are on; rule-main-only the report spec alone; any other collection the four.
specs_of = {**{slug: ("report-page",) for slug in (*mockups.split(), *PROTOTYPES)}, **{slug: ("report-page", "report") for slug in OWN_CHROME},
            **{lane: specs for lane, (_, specs, _) in LANES.items()}, "rule-main-only": ("report",),
            **{slug: (os.path.basename(SPEC_OF[slug]).removesuffix(".spec.html"),) for slug in (*THREADS, *SHELL)}}


# A prototype collection's row names the fixture's running prototype, once it runs; prototype-down's a closed port, and
# prototype-own-chrome's the review-prototype service that serves it, once it runs.
RUNNING = os.environ.get("QA_URL_PROTOTYPE", "").rstrip("/")
OWN = os.environ.get("QA_URL_REVIEW_PROTOTYPE", "").rstrip("/")
PROTOTYPE = {slug: "http://127.0.0.1:9" if slug == "prototype-down" else OWN if slug in OWN_CHROME else RUNNING for slug in PROTOTYPES}


def row(slug, root, spec):
    fields = {
        "id": "spec:%s::%s" % (slug, spec), "slug": slug, "project": own.get(slug, entry),
        "root": root, "narrow_root": os.path.join(root, "docs"), "spec": spec,
        "base": base, "owner": "qa", "checker": "qa", "lifecycle": "serving", "cursor_name": ".cursor-qa",
        "registered_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        **({"prototype": PROTOTYPE[slug]} if PROTOTYPE.get(slug) else {}),
    }
    return "[[resource]]\n" + "".join("%s = %s\n" % (k, json.dumps(v)) for k, v in fields.items())


def rows_of(slugs, at=site):
    return [row(slug, os.path.join(at, slug), "docs/specs/%s.spec.html" % spec) for slug in slugs
            for spec in specs_of.get(slug, FOUR)]


# Each service's registry folder: the review service's and review-second's records a public base address.
served = {"serve": collections + mockups.split(), "serve-plugged": [], "serve-sections": sections.split(),
          "serve-second": second.split(), "serve-prototype": list(PROTOTYPES)}
for lane, (folder, _, _) in LANES.items():
    served[folder].append(lane)
for folder, slugs in served.items():
    rows = rows_of(slugs)
    if folder == "serve":
        rows.append(row("coverage-undeclared", os.path.join(site, "coverage-undeclared"), "docs/specs/print.spec.html"))
        # A stage collection's evidence scope: a scope row, which holds no spec (comment-layer #scope-row).
        rows += ["[[resource]]\n" + "".join("%s = %s\n" % (k, json.dumps(v)) for k, v in {
            "id": "scope:" + scope, "slug": scope, "owner": "qa", "checker": "qa", "cursor_name": ".cursor-qa"}.items())
            for scope, _ in STAGE.values()]
        # QA-2's removed worktree: a spec registered from a root since gone (review-service #service-stale).
        rows.append(row("qa-2", os.path.join(site, "qa-2-removed"), "docs/specs/archive.spec.html"))
    if folder == "serve-second":
        rows += rows_of(second_own.split(), site_second)
    registry = os.path.join(qa_root, folder, "registry.toml")
    with open(registry + ".tmp", "w", encoding="utf-8") as out:
        out.write((PUBLIC if folder in ("serve", "serve-second") else "") + "\n".join(rows))
    os.replace(registry + ".tmp", registry)
PY
python3 "$QA_FIXTURE/panes.py" "$site" "$serve" "$project"
# The fake wake provider, written whole: the running service reads it at each use.
python3 - "$serve/providers/wake.toml" "$QA_ROOT/wakes.log" <<'PY'
import json, os, sys
provider, log = sys.argv[1:]
os.makedirs(os.path.dirname(provider), exist_ok=True)
send = ["sh", "-c", 'printf "%s\\n" "$1" >> "$0"', log, "{artifact}"]
with open(provider + ".tmp", "w", encoding="utf-8") as out:
    out.write("check = %s\nsend = %s\n" % (json.dumps(["true"]), json.dumps(send)))
os.replace(provider + ".tmp", provider)
PY
# The evidence provider's declared answers (review-index #index-gates-provider), once its service runs.
cp -R "$QA_FIXTURE/evidence" "$evidence"
if [ -n "${QA_URL_EVIDENCE:-}" ]; then
  python3 - "$evidence/review-facts" "$serve_plugged/providers/evidence.toml" "$QA_URL_EVIDENCE" "$head" "$QA_FIXTURE" <<'PY'
import json, os, sys
answer, provider, url, head, fixture = sys.argv[1:]
sys.path.insert(0, fixture)
from spools import STAGES
spec = "docs/specs/later-only.spec.html"
facts = {"specs": {
    "qa-8/" + spec: {"accepted": head},
    "qa-9/" + spec: {"proof": {"bundle": "qa-proof", "url": url.rstrip("/") + "/bundles/qa-proof/"}},
}, "lanes": {lane: {"stage": stage} for lane, stage in STAGES.items()}}
for path, text in ((answer, json.dumps(facts)), (provider, "url = %s\n" % json.dumps(url))):
    with open(path + ".tmp", "w", encoding="utf-8") as out:
        out.write(text)
    os.replace(path + ".tmp", path)
PY
fi
