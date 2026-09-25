#!/usr/bin/env python3
"""Validate style provenance and changed-spec story declaration structure."""

from html.parser import HTMLParser
from pathlib import Path
import subprocess
import sys
import re
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
        self.sections = []
        self.section_stack = []
        self.active_criteria = []
        self.active_boundaries = []
        self.captures = []
        self.contract = None

    def _section(self):
        return self.section_stack[-1] if self.section_stack else None

    def _capture(self, kind, tag, depth, target):
        capture = {"kind": kind, "tag": tag, "depth": depth, "target": target, "parts": []}
        self.captures.append(capture)
        return capture

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        depth = len(self.stack)
        if tag == "article":
            self.contract = values.get("data-spec-contract")
        anchor = values.get("data-anchor")
        if anchor:
            self.anchors.add(anchor)
        if tag == "style":
            self.style_blocks += 1
        if tag == "link" and "stylesheet" in values.get("rel", "").split():
            self.stylesheets.append(values.get("href", ""))
        if tag == "section":
            section = {
                "depth": depth,
                "anchor": anchor,
                "kind": values.get("data-spec-section"),
                "scope": values.get("data-acceptance-scope", "").strip(),
                "headings": [],
                "criteria": [],
                "boundaries": [],
                "has_tbd": False,
            }
            self.sections.append(section)
            self.section_stack.append(section)
        section = self._section()
        if section and "data-spec-tbd" in values:
            section["has_tbd"] = True
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
                "section": section,
            }
            self.stories.append(story)
            self.current_story = story
        if self.current_story and tag == "a" and "data-guided-journey-step" in values:
            self.current_story["steps"].append(values.get("href", ""))
        if tag in {"h2", "h3"} and section:
            self._capture("heading", tag, depth, section)
        if "data-acceptance-criterion" in values:
            criterion = {"anchor": anchor, "fields": {}, "tag": tag, "depth": depth, "has_tbd": "data-spec-tbd" in values}
            if section:
                section["criteria"].append(criterion)
            self.active_criteria.append(criterion)
        if "data-modular-boundary" in values:
            boundary = {"anchor": anchor, "fields": {}, "tag": tag, "depth": depth}
            if section:
                section["boundaries"].append(boundary)
            self.active_boundaries.append(boundary)
        if "data-acceptance-scenario" in values or "data-acceptance-observable" in values:
            if self.active_criteria:
                field = "scenario" if "data-acceptance-scenario" in values else "observable"
                self._capture("criterion-field", tag, depth, (self.active_criteria[-1], field))
        boundary_fields = {
            "data-boundary-responsibility": "responsibility",
            "data-boundary-seam": "seam",
            "data-boundary-dependency": "dependency",
            "data-boundary-scope": "scope",
        }
        field = next((name for attr, name in boundary_fields.items() if attr in values), None)
        if field and self.active_boundaries:
            self._capture("boundary-field", tag, depth, (self.active_boundaries[-1], field))
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_data(self, data):
        for capture in self.captures:
            capture["parts"].append(data)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        depth = len(self.stack) - 1
        for capture in list(self.captures):
            if capture["tag"] == tag and capture["depth"] == depth:
                text = " ".join("".join(capture["parts"]).split())
                if capture["kind"] == "heading":
                    capture["target"]["headings"].append((capture["tag"], text))
                else:
                    target, field = capture["target"]
                    target["fields"][field] = text
                self.captures.remove(capture)
        for active in (self.active_criteria, self.active_boundaries):
            active[:] = [item for item in active if not (item["tag"] == tag and item["depth"] == depth)]
        if self.current_story and tag == self.current_story["tag"] and len(self.stack) - 1 == self.current_story["depth"]:
            self.current_story = None
        if self.section_stack and tag == "section" and self.section_stack[-1]["depth"] == depth:
            self.section_stack.pop()
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


def section_heading(section):
    return {(tag, heading) for tag, heading in section["headings"]}


def has_heading(section, text):
    return ("h2", text) in section_heading(section)


def validate_shape_sections(parser):
    if parser.contract is None:
        return None
    if parser.contract != "shaped-sections-v1":
        return "unsupported data-spec-contract; use shaped-sections-v1"
    if parser.sections:
        section_depth = min(section["depth"] for section in parser.sections)
        top_level_sections = [section for section in parser.sections if section["depth"] == section_depth]
        expected_order = ("user-stories", "acceptance", "modular-boundaries")
        if len(top_level_sections) >= 3 and tuple(section["kind"] for section in top_level_sections[:3]) != expected_order:
            return "shaped spec sections must start with User stories, Acceptance criteria, Modular boundaries"
    user_sections = [
        section for section in parser.sections
        if section["kind"] == "user-stories" and has_heading(section, "User stories")
    ]
    if len(user_sections) != 1:
        return "changed governing spec needs exactly one visible User stories section"
    user_section = user_sections[0]
    if any(story["section"] is not user_section for story in parser.stories):
        return "every user story must be visibly contained in the User stories section"

    acceptance_sections = [
        section for section in parser.sections
        if section["kind"] == "acceptance" and has_heading(section, "Acceptance criteria")
    ]
    if len(acceptance_sections) != 1:
        return "changed governing spec needs a visible Acceptance criteria section"
    scopes = []
    for acceptance in acceptance_sections:
        scope = acceptance["scope"].casefold()
        if scope:
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", scope):
                return "Acceptance criteria data-acceptance-scope must be descriptive"
            if scope in {"deferred", "tbd", "unknown"} or acceptance["has_tbd"]:
                return f"Acceptance criteria scope {scope} cannot be deferred"
            if scope in scopes:
                return f"Acceptance criteria scope {scope} is duplicated"
            scopes.append(scope)
        elif acceptance["has_tbd"]:
            return "Acceptance criteria without a scope cannot be deferred"
        label = scope or "governing"
        if not acceptance["criteria"]:
            return f"{label} Acceptance criteria section needs at least one data-acceptance-criterion"
        criterion_anchors = [criterion["anchor"] for criterion in acceptance["criteria"]]
        if any(not anchor for anchor in criterion_anchors) or len(set(criterion_anchors)) != len(criterion_anchors):
            return f"every {label} acceptance criterion needs one unique data-anchor"
        for criterion in acceptance["criteria"]:
            if criterion["has_tbd"]:
                return f"{label} Acceptance criteria {criterion['anchor']} cannot be deferred"
            if not criterion["fields"].get("scenario"):
                return f"{label} acceptance criterion {criterion['anchor']} needs an observable scenario"
            if not criterion["fields"].get("observable"):
                return f"{label} acceptance criterion {criterion['anchor']} needs an observable outcome"

    modular_sections = [
        section for section in parser.sections
        if section["kind"] == "modular-boundaries" and has_heading(section, "Modular boundaries")
    ]
    if len(modular_sections) != 1:
        return "shaped spec needs exactly one visible Modular boundaries section"
    modular = modular_sections[0]
    if not modular["boundaries"]:
        return "Modular boundaries section needs at least one data-modular-boundary"
    boundary_anchors = [boundary["anchor"] for boundary in modular["boundaries"]]
    if any(not anchor for anchor in boundary_anchors) or len(set(boundary_anchors)) != len(boundary_anchors):
        return "every modular boundary needs one unique data-anchor"
    for boundary in modular["boundaries"]:
        missing = [
            field for field in ("responsibility", "seam", "dependency", "scope")
            if not boundary["fields"].get(field)
        ]
        if missing:
            return f"modular boundary {boundary['anchor']} needs observable " + ", ".join(missing)
        dependency = boundary["fields"]["dependency"].casefold()
        if "self-contained" not in dependency and "external" not in dependency:
            return f"modular boundary {boundary['anchor']} needs self-contained or external-provider direction"
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
        section_state = "legacy"
    else:
        story_error = validate_stories(parser, repository, spec)
        if story_error:
            return fail(story_error)
        section_error = validate_shape_sections(parser)
        if section_error:
            return fail(section_error)
        story_state = "valid"
        section_state = "valid" if parser.contract else "legacy"

    print(f"style={style_state} stories={story_state} sections={section_state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
