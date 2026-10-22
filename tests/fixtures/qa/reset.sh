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
# collections also get their seeded spool and agent edit from spools.py. qa-1 and qa-2
# are lane keys, so the review index shows them as lane cards (QA-1, QA-2) under Needs
# you, their specs holding open TBDs or awaiting acceptance; every other collection
# settles. QA-2 also holds a spec registered from a worktree since removed (its root
# is never created), so its card shows that row Stale (lane-hosting #acceptance-index-stale).
# qa-3 to qa-7 are index gate lanes (lane-hosting #index-gates), each registering Report filters
# alone (no open decision), its one thread seeded by spools.py: with the agent, resolved twice
# (qa-5 is pressed Accept spec), replied, and a draft.
#
# The evidence provider is a peer outside this repository, so its answers are fixture data
# (annotateanything jev-capture #qa-rules-real-data): $QA_ROOT/evidence holds its declared
# bundle page and review facts answer, the evidence service (qa.toml) serves them as files, and
# the review-plugged service's registry folder holds the provider file pointing at it
# (review-service #providers-evidence). Its lanes qa-8 (named accepted at the head commit) and
# qa-9 (named a proof bundle) register Report filters alone; qa-10 registers Weekly report (open TBDs), Report filters
# with the agent (spools.py), and long-title.spec.html, a long title waiting on acceptance, so one index shows the
# attention, running, and pass marks and a title that wraps beside its gate word (lane-hosting #index-marks,
# #index-design). The answer and the provider file
# name the evidence service's address, so they are written once it runs (QA_URL_EVIDENCE, every
# reset capture runs before a criterion); the reset before services start leaves both out. The
# review service has no provider file: it is the box with no evidence provider.
#
# A rule is its criterion's mark in its home spec (project-rules #declare): onboarding.spec.html
# marks its onboarding criterion. Dismissals live in the warning ledger by project and spec
# path, and hold for good, so each reset registers a fresh project: a story's clicks never
# reach the next one. rule-candidate is its own project whose onboarding criterion is
# unmarked, so it is a candidate on its home spec (qa.toml nav). Capture resets once per
# story, so a criterion that dismisses starts on its own project too (dismiss, dismiss-reload,
# not-a-rule): its click never hides the marker a sibling criterion of the story needs.
#
# print.spec.html's criteria name no story (jev-suggestions #coverage-declared) and its head only rewords its base;
# it is registered only in coverage-undeclared, so every other collection serves the same four specs.
#
# The registry records a public base address, so the service, though on loopback, is public
# and answers a page with no origin (review-service #served-private): the test host (host.py)
# serves the served-* collections' pages at its own address, in a frame with no origin.
#
# Comment-layer collections (host-*, one-thread-*, no-scope, no-service, layer-*) also get host pages beside their specs and
# their seeded threads from panes.py, once the registry names this reset's project; a collection's slug is the scope
# its pages' items name.
set -eu
collections="qa-fixture rule-candidate tbd-later tbd-later-handoff tbd-open-highlight tbd-open-single
  next-tbd next-tbd-step next-tbd-handoff tbd-open-jump tbd-open-wrap mobile-composer mobile-handoff
  block-target-enter block-target-space block-target-off
  choice-press choice-phone choice-thread choice-again choice-change choice-reload
  choice-handoff choice-comment-mode
  anchor-moved anchor-changed anchor-gone stale-page agent-scan name-reply advice
  clear-thread qa-1 qa-2 qa-3 qa-4 qa-5 qa-6 qa-7 dismiss dismiss-reload not-a-rule
  served-threads served-handoff served-index served-focus served-reload-handoff served-reload-outbox coverage-undeclared
  host-draft host-starters host-handoff host-handoff-two host-reply host-live host-same-subject
  host-quote host-quote-gone host-only-comments host-phone host-frame one-thread-spec one-thread-host
  no-scope no-service layer-markup layer-stores layer-focus"
plugged="qa-8 qa-9 qa-10"
site="$QA_ROOT/site"
serve="$QA_ROOT/serve"
serve_plugged="$QA_ROOT/serve-plugged"
evidence="$QA_ROOT/evidence"
repo="$site/.repo"
rm -rf "$site" "$evidence" "$serve_plugged/providers"
mkdir -p "$site" "$serve" "$serve_plugged/providers"
# Fixed identity and dates: every reset yields the same commits.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
  GIT_AUTHOR_NAME=qa GIT_AUTHOR_EMAIL=qa@example.invalid GIT_AUTHOR_DATE=2026-01-01T00:00:00Z \
  GIT_COMMITTER_NAME=qa GIT_COMMITTER_EMAIL=qa@example.invalid GIT_COMMITTER_DATE=2026-01-01T00:00:00Z
git init -q -b main "$repo"
cp -R "$QA_FIXTURE/base/." "$repo/"
cp -R "$QA_REPO_SPEC_CHAT/docs/specs/.viz" "$QA_REPO_SPEC_CHAT/docs/specs/.style" "$repo/docs/specs/"
git -C "$repo" add -A
git -C "$repo" commit -qm base
base="$(git -C "$repo" rev-parse HEAD)"
cp -R "$QA_FIXTURE/head/." "$repo/"
git -C "$repo" add -A
git -C "$repo" commit -qm head
head="$(git -C "$repo" rev-parse HEAD)"
for collection in $collections $plugged; do
  cp -R "$repo" "$site/$collection"
done
rm -rf "$repo"
# rule-candidate, not-a-rule: the onboarding criterion unmarked in the working tree, a candidate (#q-candidate).
for collection in rule-candidate not-a-rule; do
  sed 's/ data-project-rule="[^"]*"//' "$site/$collection/docs/specs/onboarding.spec.html" > "$site/$collection/onboarding.tmp"
  mv "$site/$collection/onboarding.tmp" "$site/$collection/docs/specs/onboarding.spec.html"
done
# Review-state collections: seeded spools and agent edits (spools.py).
python3 "$QA_FIXTURE/spools.py" "$site"
# Registry: every spec of every collection, in this reset's projects. Python writes it
# so paths are escaped as TOML strings.
project="qa-$(date +%s%N)"
python3 - "$serve/registry.toml" "$serve_plugged/registry.toml" "$site" "$base" "$project" "$plugged" $collections <<'PY'
import json, os, sys
registry, registry_plugged, site, base, entry, plugged, *collections = sys.argv[1:]
plugged = plugged.split()
own = {slug: entry + "-" + slug for slug in ("rule-candidate", "not-a-rule", "dismiss", "dismiss-reload")}
# Index gate lanes register Report filters alone; qa-10 a decision, a spec with the agent, and a long title.
specs_of = {**{slug: ("later-only",) for slug in ("qa-3", "qa-4", "qa-5", "qa-6", "qa-7", "qa-8", "qa-9")},
            "qa-10": ("report", "later-only", "long-title")}


def row(slug, root, spec):
    fields = {
        "id": "spec:%s::%s" % (slug, spec), "slug": slug, "project": own.get(slug, entry),
        "root": root, "narrow_root": os.path.join(root, "docs"), "spec": spec,
        "base": base, "owner": "qa", "checker": "qa", "lifecycle": "serving", "cursor_name": ".cursor-qa",
        "registered_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }
    return "[[resource]]\n" + "".join("%s = %s\n" % (k, json.dumps(v)) for k, v in fields.items())


def rows_of(slugs):
    return [row(slug, os.path.join(site, slug), "docs/specs/%s.spec.html" % spec) for slug in slugs
            for spec in specs_of.get(slug, ("onboarding", "report", "later-only", "one-open"))]


rows = rows_of(collections)
rows.append(row("coverage-undeclared", os.path.join(site, "coverage-undeclared"), "docs/specs/print.spec.html"))
# QA-2's removed worktree: a spec registered from a root since gone (review-service #service-stale).
rows.append(row("qa-2", os.path.join(site, "qa-2-removed"), "docs/specs/archive.spec.html"))
with open(registry + ".tmp", "w", encoding="utf-8") as out:
    out.write('[process]\npublic_base = "https://review.qa.invalid"\n\n' + "\n".join(rows))
os.replace(registry + ".tmp", registry)
with open(registry_plugged + ".tmp", "w", encoding="utf-8") as out:
    out.write("\n".join(rows_of(plugged)))
os.replace(registry_plugged + ".tmp", registry_plugged)
PY
python3 "$QA_FIXTURE/panes.py" "$site" "$serve" "$project"
# The evidence provider's declared answers (lane-hosting #index-gates-provider), once its service runs.
cp -R "$QA_FIXTURE/evidence" "$evidence"
if [ -n "${QA_URL_EVIDENCE:-}" ]; then
  python3 - "$evidence/review-facts" "$serve_plugged/providers/evidence.toml" "$QA_URL_EVIDENCE" "$head" <<'PY'
import json, os, sys
answer, provider, url, head = sys.argv[1:]
spec = "docs/specs/later-only.spec.html"
facts = {"specs": {
    "qa-8/" + spec: {"accepted": head},
    "qa-9/" + spec: {"proof": {"bundle": "qa-proof", "url": url.rstrip("/") + "/bundles/qa-proof/"}},
}}
for path, text in ((answer, json.dumps(facts)), (provider, "url = %s\n" % json.dumps(url))):
    with open(path + ".tmp", "w", encoding="utf-8") as out:
        out.write(text)
    os.replace(path + ".tmp", path)
PY
fi
