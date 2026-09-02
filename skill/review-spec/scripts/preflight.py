#!/usr/bin/env python3
"""Verify and, when required, migrate one repository's Spec Chat surface."""

from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import shutil
import sys
from urllib.parse import urlsplit


CAPABILITY_PREFIX = "spec-chat-capabilities:"
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


def capabilities(path):
    if not path.is_file():
        return set()
    for line in path.read_text(errors="replace").splitlines()[:20]:
        if CAPABILITY_PREFIX in line:
            return set(line.split(CAPABILITY_PREFIX, 1)[1].strip().split())
    return set()


class VisualContractParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = [{"tag": "root", "line": 1, "islands": [], "targets": []}]
        self.errors = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        parent = self.stack[-1]
        if tag == "script" and values.get("type") == "application/spec+json":
            render = values.get("data-render")
            if render:
                parent["islands"].append(render)
        target = values.get("data-render-target")
        if target:
            parent["targets"].append(target)
        if tag not in VOID_TAGS:
            self.stack.append(
                {"tag": tag, "line": self.getpos()[0], "islands": [], "targets": []}
            )

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self._close(tag)

    def handle_endtag(self, tag):
        self._close(tag)

    def _close(self, tag):
        while len(self.stack) > 1:
            frame = self.stack.pop()
            self._validate(frame)
            if frame["tag"] == tag:
                return

    def finish(self):
        while self.stack:
            self._validate(self.stack.pop())

    def _validate(self, frame):
        missing = Counter(frame["islands"]) - Counter(frame["targets"])
        for render, count in missing.items():
            for _ in range(count):
                self.errors.append(
                    f'line {frame["line"]}: missing data-render-target="{render}" '
                    "beside semantic island"
                )


def validate_visuals(spec):
    parser = VisualContractParser()
    parser.feed(spec.read_text(errors="replace"))
    parser.close()
    parser.finish()
    return parser.errors


class RuntimeReferenceParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        source = dict(attrs).get("src")
        if source and Path(urlsplit(source).path).name == "runtime.js":
            self.sources.append(source)


def runtime_path(spec):
    parser = RuntimeReferenceParser()
    parser.feed(spec.read_text(errors="replace"))
    parser.close()
    resolved = set()
    for source in parser.sources:
        parsed = urlsplit(source)
        if parsed.scheme or parsed.netloc or parsed.path.startswith("/"):
            continue
        resolved.add((spec.parent / parsed.path).resolve())
    if len(resolved) != 1:
        return None
    return resolved.pop()


def copy_tree(source, target):
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def fail(message):
    print(message, file=sys.stderr)
    return 2


def path_stays_inside(path, repository):
    try:
        path.resolve(strict=False).relative_to(repository)
        return True
    except ValueError:
        return False


def main(argv):
    if len(argv) != 3:
        return fail("usage: preflight.py <target-repository> <spec-html>")

    repository = Path(argv[1]).resolve()
    spec = Path(argv[2]).resolve()
    if not repository.is_dir():
        return fail(f"target repository does not exist: {repository}")
    try:
        spec.relative_to(repository)
    except ValueError:
        return fail("spec must be inside the target repository")
    if not spec.is_file():
        return fail(f"spec does not exist: {spec}")

    visual_errors = validate_visuals(spec)
    if visual_errors:
        for error in visual_errors:
            print(f"{spec}:{error}", file=sys.stderr)
        return 2

    skill = Path(__file__).resolve().parents[1]
    bundled_viz = skill / "assets" / "viz"
    bundled_runtime = bundled_viz / "runtime.js"
    bundled_server = skill / "assets" / "review-serve.py"
    target_runtime = runtime_path(spec)
    if target_runtime is None:
        return fail("spec must reference exactly one repository-relative runtime.js")
    target_viz = target_runtime.parent
    target_server = repository / "tools" / "review-serve.py"

    asset_paths = [target_viz, target_runtime, target_server]
    if target_viz.exists():
        asset_paths.extend(target_viz.rglob("*"))
    if any(not path_stays_inside(path, repository) for path in asset_paths):
        return fail("asset path escapes the target repository")

    required_runtime = capabilities(bundled_runtime)
    required_server = capabilities(bundled_server)
    if not required_runtime or not required_server:
        return fail("bundled Spec Chat assets do not declare required capabilities")

    runtime_state = "compatible"
    if not required_runtime.issubset(capabilities(target_runtime)):
        copy_tree(bundled_viz, target_viz)
        runtime_state = "migrated"

    server_state = "compatible"
    if not required_server.issubset(capabilities(target_server)):
        target_server.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled_server, target_server)
        server_state = "migrated"

    print(f"runtime={runtime_state} server={server_state} visuals=valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
