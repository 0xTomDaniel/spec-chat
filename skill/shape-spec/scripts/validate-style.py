#!/usr/bin/env python3
"""Reject unapproved page-level styling in a materially shaped Spec Chat page."""

from html.parser import HTMLParser
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit


class StyleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stylesheets = []
        self.style_blocks = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "style":
            self.style_blocks += 1
        if tag == "link" and "stylesheet" in values.get("rel", "").split():
            self.stylesheets.append(values.get("href", ""))


def fail(message):
    print(message, file=sys.stderr)
    return 2


def inside(path, root):
    try:
        path.resolve(strict=False).relative_to(root)
        return True
    except ValueError:
        return False


def main(argv):
    if len(argv) != 4:
        return fail("usage: validate-style.py <repository> <spec-html> <exact-base>")

    repository = Path(argv[1]).resolve()
    spec = Path(argv[2]).resolve()
    base = argv[3]
    if not repository.is_dir() or not spec.is_file() or not inside(spec, repository):
        return fail("repository and spec must exist, with the spec inside the repository")

    commit = subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if commit.returncode != 0:
        return fail("exact base must resolve to a local Git commit")

    parser = StyleParser()
    parser.feed(spec.read_text(errors="replace"))
    parser.close()
    if parser.style_blocks:
        return fail("material specs may not contain page-level <style> blocks")
    if not parser.stylesheets:
        return fail("material specs must reference exactly one repository-relative .style/spec.css")
    if len(parser.stylesheets) > 1:
        return fail("material specs must reference only one shared stylesheet")

    candidates = []
    for href in parser.stylesheets:
        parsed = urlsplit(href)
        if parsed.scheme or parsed.netloc or parsed.path.startswith("/"):
            continue
        path = (spec.parent / parsed.path).resolve()
        if path.name == "spec.css" and ".style" in path.parts:
            candidates.append(path)
    if len(set(candidates)) != 1:
        return fail("material specs must reference exactly one repository-relative .style/spec.css")

    style = candidates[0]
    if not inside(style, repository) or not style.is_file():
        return fail("shared spec stylesheet must exist inside the repository")

    current = style.read_bytes()
    fallback = Path(__file__).resolve().parents[1] / "assets" / "style" / "spec.css"
    if current == fallback.read_bytes():
        print("style=fallback")
        return 0

    relative = style.relative_to(repository).as_posix()
    prior = subprocess.run(
        ("git", "-C", str(repository), "show", f"{base}:{relative}"),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if prior.returncode == 0 and prior.stdout == current:
        print("style=established")
        return 0
    if prior.returncode == 0:
        return fail("shared spec stylesheet differs from the exact review base")
    return fail("new shared spec stylesheet must match the bundled fallback byte-for-byte")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
