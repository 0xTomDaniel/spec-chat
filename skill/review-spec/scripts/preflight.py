#!/usr/bin/env python3
"""Verify and, when required, migrate one repository's Spec Chat runtime and visuals."""

from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
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


def capabilities(text):
    for line in text.splitlines()[:20]:
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


def migration_files(source, target):
    """Map each bundled asset file to the target path it overwrites."""
    return [
        (path, target / path.relative_to(source))
        for path in sorted(source.rglob("*"))
        if path.is_file()
    ]


def copy_files(files):
    for source, destination in files:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def git(repository, *args):
    return subprocess.run(
        ("git", "-C", str(repository), *args),
        text=True,
        capture_output=True,
    )


def owns_git(repository):
    """True only when the target itself is a git work tree root."""
    top = git(repository, "rev-parse", "--show-toplevel")
    return top.returncode == 0 and Path(top.stdout.strip()).resolve() == repository


def file_text(path):
    return path.read_text(errors="replace") if path.is_file() else ""


def committed_text(repository, path):
    """Runtime text at HEAD, so an uncommitted migration still counts as pending."""
    shown = git(repository, "show", f"HEAD:{path.relative_to(repository).as_posix()}")
    return shown.stdout if shown.returncode == 0 else ""


def commit_migration(repository, paths):
    """Commit only the runtime assets; return the commit id."""
    message = "Migrate Spec Chat review runtime\n\nShared runtime assets only."
    for step in (
        ("add", "--", *paths),
        ("commit", "-q", "--no-verify", "-m", message, "--only", "--", *paths),
    ):
        done = git(repository, *step)
        if done.returncode != 0:
            raise RuntimeError(done.stderr.strip() or f"git {step[0]} failed")
    return git(repository, "rev-parse", "HEAD").stdout.strip()


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
    target_runtime = runtime_path(spec)
    if target_runtime is None:
        return fail("spec must reference exactly one repository-relative runtime.js")
    target_viz = target_runtime.parent

    asset_paths = [target_viz, target_runtime]
    if target_viz.exists():
        asset_paths.extend(target_viz.rglob("*"))
    if any(not path_stays_inside(path, repository) for path in asset_paths):
        return fail("asset path escapes the target repository")

    required_runtime = capabilities(file_text(bundled_runtime))
    if not required_runtime:
        return fail("bundled Spec Chat assets do not declare required capabilities")

    commits = owns_git(repository)
    current = capabilities(file_text(target_runtime))
    if commits:
        current &= capabilities(committed_text(repository, target_runtime))

    runtime_state = "compatible"
    if not required_runtime.issubset(current):
        files = migration_files(bundled_viz, target_viz)
        copy_files(files)
        runtime_state = "migrated"
        if commits:
            try:
                commit = commit_migration(repository, [str(d) for _, d in files])
            except RuntimeError as error:
                return fail(f"runtime migration commit failed, rerun preflight once fixed: {error}")
            runtime_state += f" migration={commit}"

    print(f"runtime={runtime_state} visuals=valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
