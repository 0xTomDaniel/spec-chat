#!/usr/bin/env python3
"""QA fixture service (qa.toml services.review): the real review-serve.py on a registry,
with Jev answered by the rules test's FakeProvider instead of OpenRouter.

usage: serve.py <registry.toml> <port>

The fake says the onboarding rule applies to every feature and that a spec misses it
unless the spec mentions onboarding, so the served specs show a rule mark and Jev
writes real records. Each criterion verifies the story its data-story names and no
other, so no false coverage gap shows. A project whose name ends in -confirmed (reset.sh)
has the onboarding rule confirmed as it warms up, through Confirm rule's own record write,
so its warm-up checks specs against it; in any other project the rule stays a candidate. A fixture agent edit pending beside a spec
(spools.py, stale-page) lands when a page next posts an event to that spec, so the page
saving it was loaded before the edit. In an answering collection (spools.py, name-reply) the
agent answers each comment the page saves, so the page offers a reply. Everything else runs
unchanged.
"""

import importlib.util
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).resolve().parent
CONFIRMED = "-confirmed"
CRITERION = re.compile(r"<[^>]*\bdata-acceptance-criterion\b[^>]*>")
ATTR = re.compile(r'\b(data-anchor|data-story)="([^"]*)"')


def coverage():
    """(story, criterion) pairs the fixture specs declare with data-story, in any attribute order."""
    pairs = set()
    for path in FIXTURE.glob("*/docs/specs/*.spec.html"):
        for tag in CRITERION.findall(path.read_text(encoding="utf-8")):
            attrs = dict(ATTR.findall(tag))
            pairs.update((story, attrs["data-anchor"]) for story in attrs.get("data-story", "").split())
    if not pairs:
        raise SystemExit("qa fixture: no data-story criteria found under " + str(FIXTURE))
    return pairs


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv):
    registry, port = argv
    sys.path.insert(0, str(ROOT / "skill" / "review-spec" / "assets"))
    serve = load("qa_review_serve", ROOT / "skill" / "review-spec" / "assets" / "review-serve.py")
    rules = load("qa_review_serve_jev_rules", ROOT / "tests" / "review-serve-jev-rules.py")
    fake = rules.FakeProvider(
        scope={rules.ONBOARDING: ("every feature", 0.95)},
        rule={rules.ONBOARDING: (lambda spec: "covered" if "onboarding" in spec.lower() else "missed", 0.95)},
        coverage=coverage(),
    )
    real = serve.JevService
    warm_project = real._warm_project

    def warm_confirmed(service, project, rows, status):
        """A human's Confirm rule on the onboarding candidate, before the warm-up reads the project's rules."""
        if project.endswith(CONFIRMED):
            try:
                _, specs = service._main_specs(rows)
                service._warm_submit(list(service._built("scope", specs)))  # the scope record Confirm rule resolves
            except Exception:
                pass  # the warm-up below reports it
            service.confirm({"project": project}, rules.ONBOARDING)
        return warm_project(service, project, rows, status)

    real._warm_project = warm_confirmed
    serve.JevService = lambda **kw: real(**{**kw, "provider": fake, "api_key": "fake"})
    spools = load("qa_fixture_spools", FIXTURE / "spools.py")
    post = serve.MountHandler._post_event

    def post_after_agent_edit(handler, query, body):
        _, review = handler._route_review(query)
        if review:
            spools.land_pending_edit(review[:-len(".review")])
        return post(handler, query, body)

    write = serve._write_event

    def write_then_answer(review, root, actor, name, event):
        fresh = not os.path.exists(os.path.join(review, actor, name))  # a resend is answered once
        stored = write(review, root, actor, name, event)
        if stored and fresh and actor == "human":
            spools.answer_comment(review[:-len(".review")], event)  # before the page hears the save
        return stored

    serve.MountHandler._post_event = post_after_agent_edit
    serve._write_event = write_then_answer
    return serve.main(["--registry", registry, "--port", port])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
