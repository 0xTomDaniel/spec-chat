#!/bin/sh
# QA fixture reset (qa.toml fixture.spec_review_site): rebuild the served spec repo
# and the review registry under $QA_ROOT. base/ is the compared base commit, head/
# the specs under shaping on top of it. Jev records and onboarding status under
# $QA_ROOT/state are kept: the running service holds them in memory too, and the
# fake is deterministic, so they match every reset's identical commits.
#
# Capture resets once per story, but criteria add drafts and hand-offs to the spec's
# on-disk spool. So every collection below is its own clean copy of the one repo,
# served at /<collection>/docs/specs/<spec>: qa-fixture is the story entry, and each
# other collection is one criterion's start, named by its qa.toml nav hint. Review-state
# collections also get their seeded spool and agent edit from spools.py.
#
# Rule state (project-rules #approval) is the project's, and a project's decisions hold
# for good, so each reset registers fresh projects: a story's clicks never reach the
# next one. The fixture's onboarding rule is confirmed in a project whose name ends in
# -confirmed, by the fake as that project warms up (serve.py), and a candidate in any
# other. The entry project is confirmed for the stories below, whose criteria are
# Given a confirmed rule; rule-candidate and rule-confirmed are each their own project,
# for a criterion whose Given differs from its story's (qa.toml nav).
set -eu
confirmed_stories=" story-home story-miss story-card story-agent story-dismiss story-copies
  story-own-project story-bootstrap story-labels "
collections="qa-fixture rule-candidate rule-confirmed tbd-later tbd-later-handoff tbd-open-highlight tbd-open-single
  next-tbd next-tbd-step next-tbd-handoff tbd-open-jump tbd-open-wrap mobile-composer
  block-target-enter block-target-space block-target-off
  anchor-moved anchor-changed anchor-gone stale-page agent-scan name-reply"
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
# Review-state collections: seeded spools and agent edits (spools.py).
python3 "$QA_FIXTURE/spools.py" "$site"
# Registry: every spec of every collection, in this reset's projects. Python writes it
# so paths are escaped as TOML strings.
project="qa-$(date +%s%N)"
case "$confirmed_stories" in *" ${QA_STORY:-none} "*) entry="$project-confirmed" ;; *) entry="$project" ;; esac
python3 - "$serve/registry.toml" "$site" "$base" "$entry" "$project" $collections <<'PY'
import json, os, sys
registry, site, base, entry, project, *collections = sys.argv[1:]
own = {"rule-candidate": project + "-rule-candidate", "rule-confirmed": project + "-rule-confirmed"}
rows = []
for slug in collections:
    for spec in ("onboarding", "report", "later-only", "one-open"):
        root = os.path.join(site, slug)
        fields = {
            "id": "spec:%s::docs/specs/%s.spec.html" % (slug, spec), "slug": slug, "project": own.get(slug, entry),
            "root": root, "narrow_root": os.path.join(root, "docs"), "spec": "docs/specs/%s.spec.html" % spec,
            "base": base, "owner": "qa", "checker": "qa", "lifecycle": "serving", "cursor_name": ".cursor-qa",
            "registered_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        }
        rows.append("[[resource]]\n" + "".join("%s = %s\n" % (k, json.dumps(v)) for k, v in fields.items()))
with open(registry + ".tmp", "w", encoding="utf-8") as out:
    out.write("\n".join(rows))
os.replace(registry + ".tmp", registry)
PY
# A story's reset waits until the running service has warmed this reset's projects:
# confirmed ones offer reconcile, the others their candidates. Capture's first page
# then shows the rule state its Given names.
if [ -n "${QA_URL_REVIEW:-}" ]; then
  python3 - "$QA_URL_REVIEW" "$entry" "$project" <<'PY'
import json, sys, time, urllib.parse, urllib.request
url, entry, project = sys.argv[1:]
waits = {"qa-fixture": entry, "rule-candidate": project + "-rule-candidate", "rule-confirmed": project + "-rule-confirmed"}
for collection, name in waits.items():
    offer = "offer" if name.endswith("-confirmed") else "candidate_offer"
    query = urllib.parse.urlencode({"path": "%s/docs/specs/report.spec.html" % collection})
    until = time.monotonic() + 60
    while True:
        try:
            with urllib.request.urlopen("%s/api/jev?%s" % (url, query), timeout=10) as response:
                if json.load(response).get(offer):
                    break
        except (OSError, ValueError):
            pass
        if time.monotonic() > until:
            sys.exit("qa reset: %s (project %s) shows no %s after 60s" % (collection, name, offer))
        time.sleep(0.2)
PY
fi
