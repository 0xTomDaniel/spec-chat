#!/bin/sh
# QA fixture reset (qa.toml fixture.spec_review_site): rebuild the served spec repo
# and the review registry under $QA_ROOT. base/ is the compared base commit, head/
# the specs under shaping on top of it. Jev records and onboarding status under
# $QA_ROOT/state are kept: the running service holds them in memory too, and the
# fake is deterministic, so they match every reset's identical commits.
set -eu
site="$QA_ROOT/site"
serve="$QA_ROOT/serve"
rm -rf "$site"
mkdir -p "$site" "$serve"
# Fixed identity and dates: every reset yields the same commits, so a running
# service keeps the same registry rows.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
  GIT_AUTHOR_NAME=qa GIT_AUTHOR_EMAIL=qa@example.invalid GIT_AUTHOR_DATE=2026-01-01T00:00:00Z \
  GIT_COMMITTER_NAME=qa GIT_COMMITTER_EMAIL=qa@example.invalid GIT_COMMITTER_DATE=2026-01-01T00:00:00Z
git init -q -b main "$site"
cp -R "$QA_FIXTURE/base/." "$site/"
cp -R "$QA_REPO_SPEC_CHAT/docs/specs/.viz" "$QA_REPO_SPEC_CHAT/docs/specs/.style" "$site/docs/specs/"
git -C "$site" add -A
git -C "$site" commit -qm base
base="$(git -C "$site" rev-parse HEAD)"
cp -R "$QA_FIXTURE/head/." "$site/"
git -C "$site" add -A
git -C "$site" commit -qm head
tmp="$serve/registry.toml.tmp"
: > "$tmp"
for spec in onboarding report later-only one-open; do
  cat >> "$tmp" <<ROW
[[resource]]
id = "spec:qa-fixture::docs/specs/$spec.spec.html"
slug = "qa-fixture"
project = "qa-fixture"
root = "$site"
narrow_root = "$site/docs"
spec = "docs/specs/$spec.spec.html"
base = "$base"
owner = "qa"
checker = "qa"
lifecycle = "serving"
cursor_name = ".cursor-qa"
registered_at = "2026-01-01T00:00:00Z"
updated_at = "2026-01-01T00:00:00Z"

ROW
done
mv "$tmp" "$serve/registry.toml"
