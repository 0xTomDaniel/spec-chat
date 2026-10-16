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
# are lane keys, so the review index shows them as lane cards (QA-1, QA-2) under In
# progress; every other collection settles. QA-2 also holds a spec registered from a
# worktree since removed (its root is never created), so its card shows that row Stale
# (lane-hosting #acceptance-index-stale).
#
# A rule is its criterion's mark in its home spec (project-rules #declare): onboarding.spec.html
# marks its onboarding criterion. Dismissals live in the warning ledger by project and spec
# path, and hold for good, so each reset registers a fresh project: a story's clicks never
# reach the next one. rule-candidate is its own project whose onboarding criterion is
# unmarked, so it is a candidate on its home spec (qa.toml nav). Capture resets once per
# story, so a criterion that dismisses starts on its own project too (dismiss, dismiss-reload,
# not-a-rule): its click never hides the marker a sibling criterion of the story needs.
#
# The registry records a public base address, so the service, though on loopback, is public
# and answers a page with no origin (review-service #served-private): the test host (host.py)
# serves the served-* collections' pages at its own address, in a frame with no origin.
set -eu
collections="qa-fixture rule-candidate tbd-later tbd-later-handoff tbd-open-highlight tbd-open-single
  next-tbd next-tbd-step next-tbd-handoff tbd-open-jump tbd-open-wrap mobile-composer
  block-target-enter block-target-space block-target-off
  anchor-moved anchor-changed anchor-gone stale-page agent-scan name-reply advice
  clear-thread qa-1 qa-2 dismiss dismiss-reload not-a-rule
  served-threads served-handoff served-index served-focus"
site="$QA_ROOT/site"
serve="$QA_ROOT/serve"
repo="$site/.repo"
rm -rf "$site"
mkdir -p "$site" "$serve"
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
for collection in $collections; do
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
python3 - "$serve/registry.toml" "$site" "$base" "$project" $collections <<'PY'
import json, os, sys
registry, site, base, entry, *collections = sys.argv[1:]
own = {slug: entry + "-" + slug for slug in ("rule-candidate", "not-a-rule", "dismiss", "dismiss-reload")}


def row(slug, root, spec):
    fields = {
        "id": "spec:%s::%s" % (slug, spec), "slug": slug, "project": own.get(slug, entry),
        "root": root, "narrow_root": os.path.join(root, "docs"), "spec": spec,
        "base": base, "owner": "qa", "checker": "qa", "lifecycle": "serving", "cursor_name": ".cursor-qa",
        "registered_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }
    return "[[resource]]\n" + "".join("%s = %s\n" % (k, json.dumps(v)) for k, v in fields.items())


rows = [row(slug, os.path.join(site, slug), "docs/specs/%s.spec.html" % spec)
        for slug in collections for spec in ("onboarding", "report", "later-only", "one-open")]
# QA-2's removed worktree: a spec registered from a root since gone (review-service #service-stale).
rows.append(row("qa-2", os.path.join(site, "qa-2-removed"), "docs/specs/archive.spec.html"))
with open(registry + ".tmp", "w", encoding="utf-8") as out:
    out.write('[process]\npublic_base = "https://review.qa.invalid"\n\n' + "\n".join(rows))
os.replace(registry + ".tmp", registry)
PY
