#!/usr/bin/env python3
"""Validate style provenance and changed-spec story declaration structure."""

from html.parser import HTMLParser
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote, urlsplit


VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class SpecParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stylesheets = []
        self.style_blocks = 0
        self.anchors = set()
        self.stories = []
        self.current_story = None
        self.stack = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        anchor = values.get("data-anchor")
        if anchor:
            self.anchors.add(anchor)
        if tag == "style":
            self.style_blocks += 1
        if tag == "link" and "stylesheet" in values.get("rel", "").split():
            self.stylesheets.append(values.get("href", ""))
        if "data-user-story" in values:
            story = {
                "tag": tag,
                "depth": len(self.stack),
                "anchor": anchor,
                "user_facing": values.get("data-user-facing"),
                "guided": values.get("data-guided-journey"),
                "mode": values.get("data-guided-journey-mode"),
                "milestone": values.get("data-guided-journey-milestone"),
                "steps": [],
            }
            self.stories.append(story)
            self.current_story = story
        if self.current_story and tag == "a" and "data-guided-journey-step" in values:
            self.current_story["steps"].append(values.get("href", ""))
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.current_story and tag == self.current_story["tag"] and len(self.stack) - 1 == self.current_story["depth"]:
            self.current_story = None
        while self.stack:
            opened = self.stack.pop()
            if opened == tag:
                break


class AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors = set()

    def handle_starttag(self, tag, attrs):
        anchor = dict(attrs).get("data-anchor")
        if anchor:
            self.anchors.add(anchor)


def fail(message):
    print(message, file=sys.stderr)
    return 2


def inside(path, root):
    try:
        path.resolve(strict=False).relative_to(root)
        return True
    except ValueError:
        return False


def git_show(repository, revision, relative):
    return subprocess.run(
        ("git", "-C", str(repository), "show", f"{revision}:{relative}"),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def validate_stories(parser, repository, spec):
    if not parser.stories:
        return "changed governing spec has no explicit data-user-story"
    anchors = [story["anchor"] for story in parser.stories]
    if any(not anchor for anchor in anchors) or len(set(anchors)) != len(anchors):
        return "every user story needs one unique data-anchor"

    target_anchors = {}
    for story in parser.stories:
        label = story["anchor"]
        if story["user_facing"] not in {"true", "false"}:
            return f"story {label} needs data-user-facing=true|false"
        if story["user_facing"] == "false":
            continue
        if story["guided"] not in {"yes", "no"}:
            return f"user-facing story {label} needs data-guided-journey=yes|no"
        if story["guided"] == "no":
            if story["steps"] or story["mode"] or story["milestone"]:
                return f"no declaration on story {label} must not include journey extension fields"
            continue
        if story["mode"] not in {"passive", "required"}:
            return f"yes declaration on story {label} needs passive|required mode"
        if len(story["steps"]) != 1:
            return f"yes declaration on story {label} needs exactly one guided-journey step link"
        if story["mode"] == "required" and not story["milestone"]:
            return f"required declaration on story {label} needs a semantic success milestone"

        href = story["steps"][0]
        parsed = urlsplit(href)
        if parsed.scheme or parsed.netloc or parsed.path.startswith("/") or not parsed.fragment:
            return f"guided-journey step link on story {label} must be repository-relative with an anchor"
        target = (spec.parent / parsed.path).resolve() if parsed.path else spec
        if not inside(target, repository) or not target.is_file():
            return f"guided-journey step link on story {label} does not resolve inside the repository"
        if target not in target_anchors:
            target_parser = AnchorParser()
            target_parser.feed(target.read_text(errors="replace"))
            target_parser.close()
            target_anchors[target] = target_parser.anchors
        if unquote(parsed.fragment) not in target_anchors[target]:
            return f"guided-journey step link on story {label} names a missing anchor"
    return None


def main(argv):
    if len(argv) != 4:
        return fail("usage: validate-style.py <repository> <spec-html> <exact-base>")

    repository = Path(argv[1]).resolve()
    spec = Path(argv[2]).resolve()
    base = argv[3]
    if not repository.is_dir() or not spec.is_file() or not inside(spec, repository):
        return fail("repository and spec must exist, with the spec inside the repository")
    if subprocess.run(("git", "-C", str(repository), "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"), stdout=subprocess.DEVNULL).returncode:
        return fail("exact base must resolve to a local Git commit")

    html = spec.read_bytes()
    parser = SpecParser()
    parser.feed(html.decode(errors="replace"))
    parser.close()
    if parser.style_blocks:
        return fail("material specs may not contain page-level <style> blocks")
    if not parser.stylesheets:
        return fail("material specs must reference exactly one repository-relative .style/spec.css")
    if len(parser.stylesheets) > 1:
        return fail("material specs must reference only one shared stylesheet")

    parsed_style = urlsplit(parser.stylesheets[0])
    style = (spec.parent / parsed_style.path).resolve()
    if parsed_style.scheme or parsed_style.netloc or parsed_style.path.startswith("/") or style.name != "spec.css" or ".style" not in style.parts:
        return fail("material specs must reference exactly one repository-relative .style/spec.css")
    if not inside(style, repository) or not style.is_file():
        return fail("shared spec stylesheet must exist inside the repository")

    fallback = Path(__file__).resolve().parents[1] / "assets" / "style" / "spec.css"
    current_style = style.read_bytes()
    style_relative = style.relative_to(repository).as_posix()
    prior_style = git_show(repository, base, style_relative)
    if current_style == fallback.read_bytes():
        style_state = "fallback"
    elif prior_style.returncode == 0 and prior_style.stdout == current_style:
        style_state = "established"
    elif prior_style.returncode == 0:
        return fail("shared spec stylesheet differs from the exact review base")
    else:
        return fail("new shared spec stylesheet must match the bundled fallback byte-for-byte")

    spec_relative = spec.relative_to(repository).as_posix()
    prior_spec = git_show(repository, base, spec_relative)
    if prior_spec.returncode == 0 and prior_spec.stdout == html:
        story_state = "unchanged"
    else:
        story_error = validate_stories(parser, repository, spec)
        if story_error:
            return fail(story_error)
        story_state = "valid"

    print(f"style={style_state} stories={story_state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
