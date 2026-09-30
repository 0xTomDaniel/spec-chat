#!/usr/bin/env python3
"""QA fixture service (qa.toml services.review): the real review-serve.py on a registry,
with Jev answered by the rules test's FakeProvider instead of OpenRouter.

usage: serve.py <registry.toml> <port>

The fake says the onboarding rule applies to every feature and that a spec misses it
unless the spec mentions onboarding, so the served specs show a rule mark and Jev
writes real records. Each criterion verifies the story its data-story names and no
other, so no false coverage gap shows. Everything else runs unchanged.
"""

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).resolve().parent
CRITERION = re.compile(r'<p data-acceptance-criterion data-anchor="([^"]+)" data-story="([^"]+)"')


def coverage():
    """(story, criterion) pairs the fixture specs declare with data-story."""
    return {(story, criterion) for path in FIXTURE.glob("*/docs/specs/*.spec.html")
            for criterion, story in CRITERION.findall(path.read_text(encoding="utf-8"))}


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
    serve.JevService = lambda **kw: real(**{**kw, "provider": fake, "api_key": "fake"})
    return serve.main(["--registry", registry, "--port", port])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
