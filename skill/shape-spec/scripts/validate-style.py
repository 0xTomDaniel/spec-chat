#!/usr/bin/env python3
"""Validate style provenance, changed-spec story declaration structure, proof clauses kept from the base, and the spec's
mockup set, with fidelity, walkthrough, and directions checks on each mockup the change adds or edits; warn on a Then
that may span two screens, on a clause stating look or motion mechanics, and on a mockup's fidelity note reading none.
With --whole-spec, repair's whole-spec check (repair-specs #rule-source-validator), every check on what a change adds or
edits runs on every clause, criterion, and mockup of the spec, the chrome check only warning, and a proof clause still
fails against the base when no criterion of any spec of the repository carries it, the clauses of each spec deleted
since the base among them."""

from html.parser import HTMLParser
from pathlib import Path
import subprocess
import sys
import re
from urllib.parse import unquote, urlsplit


VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
QUOTED = re.compile(r"“[^“”]*”")  # a quotation in curly marks; a <q> element is one too
# prompt-first-shaping #complete-mechanics-warning: a clause is the innermost anchored or block element; its text is its
# own, not that of a clause inside it. Inline elements join the words around them; any other tag separates them.
CLAUSE_TAGS = {"p", "li", "tr", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6", "caption", "figcaption", "pre", "blockquote", "summary"}
INLINE_TAGS = {
    "a", "abbr", "b", "bdi", "bdo", "cite", "code", "data", "dfn", "em", "i", "kbd", "mark", "q", "s", "samp", "small",
    "span", "strong", "sub", "sup", "time", "u", "var",
}


class SpecParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stylesheets = []
        self.runtimes = []
        self.style_blocks = 0
        self.anchors = set()
        self.stories = []
        self.current_story = None
        self.stack = []
        self.sections = []
        self.top_level_sections = []
        self.section_stack = []
        self.active_criteria = []
        self.criteria = []
        self.active_boundaries = []
        self.captures = []
        self.contract = None
        self.rule_marks = []
        self.quotes = 0
        self.clauses = []  # each clause and table: kind, label, text, and the words it states, not quotes, per table row
        self.open_clauses = []
        self.open_tables = []
        self.open_anchors = []
        self.raw = None  # a script or style element, whose text is no clause's

    def _section(self):
        return self.section_stack[-1] if self.section_stack else None

    def _capture(self, kind, tag, depth, target):
        capture = {"kind": kind, "tag": tag, "depth": depth, "target": target, "parts": [], "carried": []}
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
        if tag == "q":
            self.quotes += 1
        if tag == "link" and "stylesheet" in values.get("rel", "").split():
            self.stylesheets.append(values.get("href", ""))
        if tag == "script" and Path(urlsplit(values.get("src") or "").path).name == "runtime.js":
            self.runtimes.append(values["src"])
        if tag == "section":
            top_level = not self.section_stack
            section = {
                "depth": depth,
                "anchor": anchor,
                "kind": values.get("data-spec-section"),
                "scope": values.get("data-acceptance-scope", "").strip(),
                "headings": [],
                "criteria": [],
                "boundaries": [],
            }
            self.sections.append(section)
            if top_level:
                self.top_level_sections.append(section)
            self.section_stack.append(section)
        section = self._section()
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
        if "data-project-rule" in values:  # project-rules #declare: a rule's short name on its criterion
            self.rule_marks.append((anchor, "data-acceptance-criterion" in values, values.get("data-project-rule") or ""))
        if tag in {"h2", "h3"} and section:
            self._capture("heading", tag, depth, section)
        if "data-acceptance-criterion" in values:
            criterion = {"anchor": anchor, "fields": {}, "tag": tag, "depth": depth, "stories": (values.get("data-story") or "").split(), "one_screen": values.get("data-one-screen") or ""}
            if section:
                section["criteria"].append(criterion)
            self.criteria.append(criterion)
            self.active_criteria.append(criterion)
            self._capture("criterion-field", tag, depth, (criterion, "text"))
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
        self._open_clause(tag, depth, anchor)
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def _open_clause(self, tag, depth, anchor):
        if tag in {"script", "style"}:
            self.raw = tag
        if tag not in INLINE_TAGS:
            self._clause_data(" ")
        if tag in VOID_TAGS:
            return
        within = self.open_anchors[-1][1] if self.open_anchors else None
        if anchor or tag in CLAUSE_TAGS:
            self.open_clauses.append({"kind": "clause", "anchor": anchor, "within": within, "depth": depth, "parts": [], "carried": []})
        if tag == "table":
            self.open_tables.append({"kind": "table", "anchor": anchor, "within": within, "depth": depth, "rows": [], "row": None})
        if tag == "tr" and self.open_tables:
            row = {"depth": depth, "parts": [], "carried": []}
            self.open_tables[-1]["rows"].append(row)
            self.open_tables[-1]["row"] = row
        if anchor:
            self.open_anchors.append((depth, anchor))

    def _clause_data(self, data):
        if self.raw:
            return
        for item in (self.open_clauses[-1] if self.open_clauses else None, self.open_tables[-1]["row"] if self.open_tables else None):
            if item is not None:
                item["parts"].append(data)
                item["carried"].append(" " if self.quotes else data)

    def _close_clauses(self, depth):
        """Close each clause, table, and row opened at depth or deeper."""
        def text(parts):
            return " ".join("".join(parts).split())

        def label(item):
            return item["anchor"] or (f"in {item['within']}" if item["within"] else "without an anchor")

        while self.open_clauses and self.open_clauses[-1]["depth"] >= depth:
            clause = self.open_clauses.pop()
            carried = text(QUOTED.sub(" ", "".join(clause["carried"])))
            self.clauses.append({"kind": "clause", "label": label(clause), "text": text(clause["parts"]), "stated": [carried]})
        for table in self.open_tables:
            if table["row"] and table["row"]["depth"] >= depth:
                table["row"] = None
        while self.open_tables and self.open_tables[-1]["depth"] >= depth:
            table = self.open_tables.pop()
            rows = [text(row["parts"]) for row in table["rows"]]
            carried = [text(QUOTED.sub(" ", "".join(row["carried"]))) for row in table["rows"]]
            self.clauses.append({"kind": "table", "label": label(table), "text": " | ".join(rows), "rows": rows, "stated": carried})
        while self.open_anchors and self.open_anchors[-1][0] >= depth:
            self.open_anchors.pop()

    def close(self):
        super().close()
        self._close_clauses(0)

    def handle_data(self, data):
        for capture in self.captures:
            capture["parts"].append(data)
            if not self.quotes:
                capture["carried"].append(data)
        self._clause_data(data)

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
                    if field == "text":  # the words the criterion states itself, not those it quotes
                        target["carried"] = " ".join(QUOTED.sub(" ", "".join(capture["carried"])).split())
                self.captures.remove(capture)
        for active in (self.active_criteria, self.active_boundaries):
            active[:] = [item for item in active if not (item["tag"] == tag and item["depth"] == depth)]
        if self.current_story and tag == self.current_story["tag"] and len(self.stack) - 1 == self.current_story["depth"]:
            self.current_story = None
        if self.section_stack and tag == "section" and self.section_stack[-1]["depth"] == depth:
            self.section_stack.pop()
        if tag == "q" and self.quotes:
            self.quotes -= 1
        while self.stack:
            opened = self.stack.pop()
            if opened == tag:
                break
        if tag not in INLINE_TAGS:
            self._clause_data(" ")
        if tag == self.raw:
            self.raw = None
        self._close_clauses(len(self.stack))


class AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors = set()

    def handle_starttag(self, tag, attrs):
        anchor = dict(attrs).get("data-anchor")
        if anchor:
            self.anchors.add(anchor)


CONTROL_TAGS = {"button", "select", "textarea"}
CONTROL_ROLES = {"button", "link", "textbox", "searchbox", "combobox", "checkbox", "radio", "switch"}
DISABLEABLE_TAGS = {"button", "input", "select", "textarea", "fieldset", "optgroup", "option"}
PAINT_ATTRIBUTES = {"fill", "stroke", "color", "stop-color", "flood-color", "lighting-color", "bgcolor"}
URL_ATTRIBUTES = {"href", "src", "action", "formaction", "xlink:href"}


def page_attribute(values, name):
    """An attribute whose presence matters, as the page reads it: None when absent, else its value stripped, "" when
    empty or bare (HTMLParser gives a bare one None)."""
    return (values[name] or "").strip() if name in values else None


class MockupParser(HTMLParser):
    """mockups #markup-table: a mockup's frames, anchors, illustrated spec anchors, route, chrome, fidelity note,
    styles, scripts, walkthrough steps, questions, and directions, as the validator checks them."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors = []
        self.frames = []
        self.illustrated = []
        self.stack = []
        self.scopes = []  # (kind, item, depth) of each open frame, steps list, and questions block
        self.route = None
        self.chrome = None
        self.source = None
        self.styles = []
        self.inline_styles = []
        self.paints = []
        self.scripts = []
        self.handlers = []
        self.steps = []
        self.questions = []
        self.directions_lists = []  # each directions list's own anchor, "" with none
        self.directions = []

    def _open(self, kind):
        return [item for open_kind, item, _ in self.scopes if open_kind == kind]

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("data-anchor"):
            self.anchors.append(values["data-anchor"])
        if tag == "meta" and values.get("name") == "mockup-prototype" and self.route is None:
            self.route = (values.get("content") or "").strip()
        if tag == "meta" and values.get("name") == "mockup-chrome" and self.chrome is None:
            self.chrome = (values.get("content") or "").strip()
        if tag == "meta" and values.get("name") == "mockup-design-source" and self.source is None:
            self.source = values.get("content") or ""
        if tag == "script":
            self.scripts.append(values.get("src"))
        for name, value in attrs:
            if name == "style" and value:
                self.inline_styles.append(value)
            elif name in PAINT_ATTRIBUTES and value:
                self.paints.append((name, value))
            if name.startswith("on") or (name in URL_ATTRIBUTES and (value or "").strip().lower().startswith("javascript:")):
                self.handlers.append(f"{name} on <{tag}>")
        frames = self._open("frame")
        # mockups #markup-frame, #markup-directions: a frame carrying data-direction draws another direction, the screen's,
        # the pick's, carrying none; a directions list item carrying it names a direction; its key "" when empty or bare.
        direction = page_attribute(values, "data-direction")
        if "data-mockup-frame" in values:
            frame = {"viewport": values["data-mockup-frame"] or "", "direction": direction, "illustrates": 0, "controls": False, "disabled": False, "aria-disabled": False}
            self.frames.append(frame)
            frames.append(frame)
            if tag not in VOID_TAGS:
                self.scopes.append(("frame", frame, len(self.stack)))
        # mockups #illustrates-rule: a frame or a part with its own data-anchor names what it shows, as the service reads it.
        names = (values.get("data-illustrates") or "").split() if values.get("data-anchor") else []
        self.illustrated.extend(names)
        kind = (values.get("type") or "").lower()
        role = (values.get("role") or "").lower()
        control = (
            tag in CONTROL_TAGS
            or (tag == "input" and kind != "hidden")
            or (tag in ("a", "area") and "href" in values)
            or role in CONTROL_ROLES
            or "contenteditable" in values
        )
        for frame in frames:
            frame["illustrates"] += len(names)
            frame["controls"] = frame["controls"] or control
            frame["disabled"] = frame["disabled"] or (tag in DISABLEABLE_TAGS and "disabled" in values)
            frame["aria-disabled"] = frame["aria-disabled"] or (values.get("aria-disabled") or "").lower() == "true"
        # mockups #markup-steps, #markup-questions: steps sit in the steps list, questions in the questions block; a step
        # names the spec anchors it illustrates as a part does (#illustrates-rule).
        if self._open("steps") and "data-step" in values:
            self.steps.append({"name": (values.get("data-step") or "").strip(), "by": (values.get("data-step-by") or "").split()})
            if not values.get("data-anchor"):
                self.illustrated.extend((values.get("data-illustrates") or "").split())
        if self._open("questions") and ("data-about" in values or "data-step" in values):
            name = values.get("data-anchor") or f"#{len(self.questions) + 1}"
            self.questions.append({"name": name, "about": (values.get("data-about") or "").split(), "step": (values.get("data-step") or "").strip()})
        # mockups #markup-directions: one item per direction, its name its text, outside the frames.
        if tag == "li" and direction is not None and self._open("directions") and not self._open("direction"):
            item = {
                "key": direction,
                "text": "",
                "gives-up": (values.get("data-gives-up") or "").strip(),
                "pick": page_attribute(values, "data-pick"),
                "route": (values.get("data-route") or "").strip(),
            }
            self.directions.append(item)
            self.scopes.append(("direction", item, len(self.stack)))
        if tag not in VOID_TAGS:
            for marker, scope in (("data-mockup-steps", "steps"), ("data-mockup-questions", "questions"), ("data-mockup-directions", "directions")):
                if marker in values:
                    self.scopes.append((scope, None, len(self.stack)))
            if "data-mockup-directions" in values:
                self.directions_lists.append((values.get("data-anchor") or "").strip())
            self.stack.append(tag)

    def handle_data(self, data):
        if self.stack and self.stack[-1] == "style":
            self.styles.append(data)
        for direction in self._open("direction"):
            direction["text"] += data

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        depth = len(self.stack) - 1 - self.stack[::-1].index(tag)  # closes any element left open inside it
        del self.stack[depth:]
        self.scopes = [scope for scope in self.scopes if scope[2] < depth]


MOCKUP_NAME = re.compile(r"[0-9]{2}-[a-z0-9]+(?:-[a-z0-9]+)*\.mockup\.html")
VIEWPORTS = ("desktop", "phone")


def frame_name(frame):
    """A drawn frame as a problem names it: its viewport, and the direction it draws when it is another's."""
    direction = f' of direction "{frame["direction"]}"' if frame["direction"] is not None else ""
    return f"{frame['viewport']} frame{direction}"


def css_close(text, start, opener, closer):
    """The index just past the closer matching the opener at start, skipping strings."""
    depth, index, quote = 0, start, None
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(text)


CSS_COMMENT = re.compile(r"""("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|/\*.*?(?:\*/|$)""", re.S)


def css_items(text):
    """A style sheet or block as its items: ("decl", property, value) and ("rule", prelude, items)."""
    text = CSS_COMMENT.sub(lambda match: match.group(1) or " ", text)
    items, start, index, parens, quote = [], 0, 0, 0, None
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "(":
            parens += 1
        elif char == ")":
            parens = max(parens - 1, 0)
        elif char == "{" and not parens:
            end = css_close(text, index, "{", "}")
            items.append(("rule", " ".join(text[start:index].split()), css_items(text[index + 1 : end - 1])))
            start = index = end
            continue
        elif char == ";" and not parens:
            items.extend(css_declaration(text[start:index]))
            start = index + 1
        index += 1
    items.extend(css_declaration(text[start:]))
    return items


def css_declaration(chunk):
    name, colon, value = chunk.partition(":")
    name = name.strip()
    if not colon or not name or name.startswith("@"):
        return []
    value = re.sub(r"!\s*important\s*$", "", value.strip(), flags=re.I).strip()
    return [("decl", name if name.startswith("--") else name.lower(), value)]


def css_declarations(items, context=()):
    """Each declaration with the preludes of the rules around it, outermost first."""
    for item in items:
        if item[0] == "decl":
            yield context, item[1], item[2]
        else:
            yield from css_declarations(item[2], context + (item[1],))


def css_preludes(items):
    for item in items:
        if item[0] == "rule":
            yield item[1]
            yield from css_preludes(item[2])


def css_split(text, separator=","):
    """Text split on the separator outside parentheses, brackets, and strings."""
    parts, start, depth, quote = [], 0, 0, None
    for index, char in enumerate(text):
        if quote:
            quote = None if char == quote else quote
        elif char in "\"'":
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == separator and not depth:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return [part.strip() for part in parts if part.strip()]


CSS_URL = re.compile(r"""url\(\s*("[^"]*"|'[^']*'|[^)]*?)\s*\)""", re.I)
CSS_STRING = re.compile(r""""(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'""")
HEX_COLOR = re.compile(r"(?<![\w-])#(?:[0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})(?![\w-])", re.I)
CSS_CALL_OR_NAME = re.compile(r"(?<![\w#-])([a-z][\w-]*)(\()?", re.I)
DURATION = re.compile(r"(?<![\w.#-])(?:\d+(?:\.\d+)?|\.\d+)(?:ms|s)(?![\w%-])", re.I)
COLOR_FUNCTIONS = {"rgb", "rgba", "hsl", "hsla", "hwb", "lab", "lch", "oklab", "oklch", "color"}
EASING_FUNCTIONS = {"cubic-bezier", "steps", "linear"}
EASING_NAMES = {"ease", "ease-in", "ease-out", "ease-in-out", "linear", "step-start", "step-end"}
COLOR_NAMES = set(
    """aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown burlywood
    cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod darkgray
    darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen
    darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue
    firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow grey honeydew
    hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan
    lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray
    lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine mediumblue
    mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred
    midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab orange orangered orchid
    palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum powderblue purple
    rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue
    slateblue slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet wheat white
    whitesmoke yellow yellowgreen""".split()
)
CSS_WIDE = {"inherit", "initial", "unset", "revert", "revert-layer"}
TOKEN_CALL = r"var\(\s*--[\w-]+\s*(?:,(?:[^()]|\([^()]*\))*)?\)"
FONT_FROM_TOKEN = {"font-family": re.compile(rf"{TOKEN_CALL}"), "font": re.compile(rf"(?:.*\s)?{TOKEN_CALL}", re.S)}
STATE_STYLES = {
    "focus": re.compile(r":focus(?:-visible)?(?![\w-])", re.I),
    "disabled": re.compile(r":disabled(?![\w-])|\[\s*disabled\s*[\]=~|^$*]", re.I),
    "aria-disabled": re.compile(r"\[\s*aria-disabled\b", re.I),
}
FRAME_SELECTOR = re.compile(r"""data-mockup-frame\s*=\s*["']?([\w-]+)""")
STILL = {"none", "0s", "0ms"} | CSS_WIDE


def literal_values(value):
    """mockups #check-tokens: each color, duration, and easing a value states itself, as (kind, text)."""
    text = CSS_STRING.sub(" ", CSS_URL.sub(" ", value))
    found = [("color", match.group(0)) for match in HEX_COLOR.finditer(text)]
    found += [("duration", match.group(0)) for match in DURATION.finditer(text)]
    for match in CSS_CALL_OR_NAME.finditer(text):
        name = match.group(1).lower()
        if match.group(2):
            call = text[match.start() : css_close(text, match.end() - 1, "(", ")")]
            if name in COLOR_FUNCTIONS:
                found.append(("color", call))
            elif name in EASING_FUNCTIONS:
                found.append(("easing", call))
        elif name in COLOR_NAMES:
            found.append(("color", match.group(1)))
        elif name in EASING_NAMES:
            found.append(("easing", match.group(1)))
    return found


def frame_coverage(items, parent, out):
    """mockups #check-focus, #check-disabled: the frames each state style can reach; a selector naming one
    viewport's frame reaches that frame only."""
    for item in items:
        if item[0] != "rule":
            continue
        prelude, children = item[1], item[2]
        if prelude.startswith("@"):
            frame_coverage(children, parent, out)
            continue
        styled = any(child[0] == "decl" for child in children)
        for branch in css_split(prelude):
            named = set(FRAME_SELECTOR.findall(branch))
            reach = (named & set(VIEWPORTS) if named else set(VIEWPORTS)) & parent
            for state, pattern in STATE_STYLES.items():
                if styled and pattern.search(branch):
                    out[state] |= reach
            frame_coverage(children, reach, out)


def file_in_repo(repository, path):
    return inside(path, repository) and path.is_file()


def git_commit(repository, revision):
    return not subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode


def source_problems(repository, label, note):
    """mockups #check-source, #check-source-moved, #checks-warn: the fidelity note names design source files in the
    repo, each at a commit Git holds and unchanged since it; a note reading none warns."""
    if note is None:
        return [f'{label}: no fidelity note, the missing design source: add <meta name="mockup-design-source"> naming each design source file as <path>@<commit>, or none'], []
    entries = note.split()
    if entries == ["none"]:
        return [], [f"warning: {label}: fidelity note reads none, the mockup names no design source"]
    if not entries:
        return [f"{label}: fidelity note names no design source file, name each as <path>@<commit>, or none"], []
    problems, warnings = [], []
    for entry in entries:
        relative, at, commit = entry.rpartition("@")
        if not at or not relative or not commit:
            problems.append(f'{label}: fidelity note entry "{entry}" must be <path>@<commit>')
            continue
        path = repository / relative
        if not file_in_repo(repository, path):
            problems.append(f"{label}: fidelity note names design source {relative}, which is not in the repo")
        elif not git_commit(repository, commit):
            problems.append(f"{label}: fidelity note names design source {relative} at {commit}, a commit Git cannot find")
        else:
            read = git_show(repository, commit, path.resolve().relative_to(repository).as_posix())
            if read.returncode or read.stdout != path.read_bytes():
                problems.append(f"{label}: design source {relative} changed since {commit}, the commit its fidelity note names: read it again, then update the mockup and its note")
    return problems, warnings


def script_file(folder, src):
    """The file a page in folder loads for the script src, percent-decoded as a browser decodes it; None for an inline,
    remote, or root-relative script."""
    parsed = urlsplit(src or "")
    if not src or parsed.scheme or parsed.netloc or parsed.path.startswith("/"):
        return None
    return (folder / unquote(parsed.path)).resolve()


def page_runtime(spec, sources):
    """mockups #set-self-contained: the page runtime, the one file the spec page's runtime.js scripts (sources) load;
    None when they load none or more than one. Preflight migrates this file (review-spec scripts/preflight.py)."""
    resolved = {script_file(spec.parent, src) for src in sources} - {None}
    return resolved.pop() if len(resolved) == 1 else None


def drawn_problems(repository, runtime, path, label, parser):
    """mockups #checks-fidelity: tokens, font, focus and disabled styles, reduced motion, and scripts of a drawn mockup;
    its only script may load runtime, its spec page's runtime (page_runtime), None when the page loads no single one."""
    problems = []
    sheet = css_items("\n".join(parser.styles))
    declarations = list(css_declarations(sheet))
    declarations += [((), name, value) for style in parser.inline_styles for _, name, value in css_declarations(css_items(style))]
    declarations += [((), name, value) for name, value in parser.paints]
    literals, fonts, files, moves, font_from_token = {}, [], [], [], False
    for context, name, value in declarations:
        if name.startswith("--") or any(prelude.lower().startswith("@property") for prelude in context):
            continue  # a token's declaration
        for kind, text in literal_values(value):
            literals.setdefault((kind, text), None)
        if any(prelude.lower().startswith("@font-face") for prelude in context):
            if name == "src":
                files += [match.group(1).strip("\"'") for match in CSS_URL.finditer(value)]
            continue
        if name in FONT_FROM_TOKEN:
            if value.lower() in CSS_WIDE:
                continue
            if FONT_FROM_TOKEN[name].fullmatch(value):
                font_from_token = True
            else:
                fonts.append(value)
        if name.startswith(("transition", "animation")) and value.lower() not in STILL:
            moves.append(name)
    for kind, text in literals:
        problems.append(f"{label}: {kind} {text} outside a token's declaration, take it from a token")
    for value in fonts:
        problems.append(f"{label}: font {value} does not come from a token")
    if not font_from_token and not fonts:
        problems.append(f"{label}: frames take no font from a token, set the frames' font from a token naming the design source's families")
    for url in dict.fromkeys(files):
        parsed = urlsplit(url)
        if parsed.scheme == "data":
            continue
        font_file = (path.parent / unquote(parsed.path)).resolve()
        if parsed.scheme or parsed.netloc or not parsed.path or not file_in_repo(repository, font_file):
            problems.append(f"{label}: font file {url} is not in the repo")
    preludes = [prelude.lower() for prelude in css_preludes(sheet)]
    moves += ["@keyframes"] if any(re.match(r"@(?:-[a-z]+-)?keyframes\b", prelude) for prelude in preludes) else []
    reduced = any(prelude.startswith("@media") and "prefers-reduced-motion" in prelude for prelude in preludes)
    if moves and not reduced:
        what = ", ".join(dict.fromkeys(moves))
        problems.append(f"{label}: styles move ({what}) and hold no reduced-motion rule, the missing @media (prefers-reduced-motion)")
    reach = {state: set() for state in STATE_STYLES}
    frame_coverage(sheet, set(VIEWPORTS), reach)
    for frame in parser.frames:
        viewport, name = frame["viewport"], frame_name(frame)
        if frame["controls"] and viewport not in reach["focus"]:
            problems.append(f"{label}: {name} holds a button, link, or field and no focus style, the missing focus state")
        if frame["disabled"] and viewport not in reach["disabled"]:
            problems.append(f"{label}: {name} holds a disabled control and no :disabled style, the missing disabled state")
        if frame["aria-disabled"] and viewport not in reach["aria-disabled"]:
            problems.append(f'{label}: {name} holds an aria-disabled="true" control and no [aria-disabled] style, the missing disabled state')
    for src in parser.scripts:
        loaded = script_file(path.parent, src)
        if loaded is None or loaded != runtime:
            none = ", the spec page loads no single runtime.js" if loaded and runtime is None else ""
            problems.append(f"{label}: script {src or '(inline)'} is not the page runtime{none}, a mockup carries no script of its own")
    for handler in dict.fromkeys(parser.handlers):
        problems.append(f"{label}: script in {handler}, a mockup carries no script of its own")
    return problems


def walkthrough_problems(label, parser):
    """mockups #check-steps, #check-question-step: unique step names, each one word, since data-at lists the steps
    reached separated by spaces (#markup-at), and every part a step or question names and every question's step in the
    mockup."""
    problems = []
    anchors = set(parser.anchors)
    names = [step["name"] for step in parser.steps]
    if "" in names:
        problems.append(f"{label}: a step with no name, name it in data-step")
    for name in sorted({name for name in names if len(name.split()) > 1}):
        problems.append(f'{label}: step "{name}" holds a space, which data-at separates steps by; name it in one word, as {"-".join(name.split())}')
    for name in sorted({name for name in names if name and names.count(name) > 1}):
        problems.append(f'{label}: step "{name}" named more than once')
    for step in parser.steps:
        for anchor in step["by"]:
            if anchor not in anchors:
                problems.append(f'{label}: step "{step["name"]}" names part {anchor}, which the mockup lacks')
    for question in parser.questions:
        for anchor in question["about"]:
            if anchor not in anchors:
                problems.append(f"{label}: question {question['name']} names part {anchor}, which the mockup lacks")
        if question["step"] and question["step"] not in names:
            problems.append(f'{label}: question {question["name"]} is asked at step "{question["step"]}", which its walkthrough lacks')
    return problems


def directions_problems(label, parser):
    """mockups #check-directions: a first round's directions list holds two or three directions with unique names,
    exactly one the pick with its reason, each with its gives-up line, and each other direction its route in a prototype
    mockup or, in a drawn one, its two frames naming it; the pick is the mockup itself, its route or the frames naming no
    direction (#directions-file), so no frame names the pick, a direction the list lacks, or none in an empty
    data-direction; one list, its own anchor where a Pick's answer is saved (#markup-directions)."""
    problems = [f'{label}: {frame["viewport"]} frame names no direction in its empty data-direction: name the direction it draws, '
                "or drop data-direction from the screen's own frame" for frame in parser.frames if frame["direction"] == ""]
    drawn = sorted({frame["direction"] for frame in parser.frames if frame["direction"]})
    if len(parser.directions_lists) > 1:  # which list a direction is in is unknown, so its other checks would mislead
        return problems + [f"{label}: {len(parser.directions_lists)} directions lists, a mockup names its directions in one"]
    if not parser.directions_lists:
        return problems + [f'{label}: frames name direction "{key}", and the mockup holds no directions list naming it' for key in drawn]
    if not parser.directions_lists[0]:
        problems.append(f"{label}: directions list lacks its own anchor in data-anchor, where a Pick's answer is saved")
    directions = [dict(direction, name=" ".join(direction["text"].split())) for direction in parser.directions]
    if not 2 <= len(directions) <= 3:
        problems.append(f"{label}: directions list holds {len(directions)} direction{'s' * (len(directions) != 1)}, needs two or three")
    picks = [direction for direction in directions if direction["pick"] is not None]
    if not picks:
        problems.append(f"{label}: directions list marks no pick, the missing pick: mark the agent's pick with its reason in data-pick")
    elif len(picks) > 1:
        names = ", ".join(f'"{direction["name"] or direction["key"]}"' for direction in picks)
        problems.append(f"{label}: directions list marks {len(picks)} picks ({names}), needs exactly one")
    for field in ("name", "key"):
        values = [direction[field] for direction in directions if direction[field]]
        for value in sorted({value for value in values if values.count(value) > 1}):
            problems.append(f'{label}: direction {field} "{value}" used more than once, needs a unique one per direction')
    keys = {direction["key"] for direction in directions}
    for number, direction in enumerate(directions, 1):
        lacks = []
        if not direction["name"]:
            lacks.append("its short name, as its text")
        if not direction["key"]:
            lacks.append("its key in data-direction")
        if not direction["gives-up"]:
            lacks.append("its gives-up line in data-gives-up")
        if direction["pick"] is not None:
            if not direction["pick"]:
                lacks.append("its reason in data-pick")
        elif picks and parser.route and not direction["route"]:
            lacks.append("its route in data-route")
        elif picks and parser.frames and direction["key"] and direction["key"] not in drawn:
            lacks.append(f'its desktop and phone frames, each naming it in data-direction="{direction["key"]}"')
        named = direction["name"] or direction["key"]
        who = f'"{named}"' if named else f"#{number}"
        for what in lacks:
            problems.append(f"{label}: direction {who} lacks {what}")
    for direction in picks:
        if direction["key"] in drawn:
            problems.append(f'{label}: frames name direction "{direction["key"]}", the pick, whose frames name no direction')
    for key in drawn:
        if key not in keys:
            problems.append(f'{label}: frames name direction "{key}", which the directions list lacks')
    return problems


def mockup_pages(repository, spec):
    """Each file of the spec's mockup set as (path, label, page bytes, parsed page)."""
    folder = spec.parent / "mockups" / spec.name[: -len(".spec.html")]
    if not spec.name.endswith(".spec.html") or not folder.is_dir():
        return
    for path in sorted(folder.glob("*.mockup.html")):
        if path.is_file():
            page = path.read_bytes()
            parser = MockupParser()
            parser.feed(page.decode(errors="replace"))
            parser.close()
            yield path, f"mockup {path.relative_to(repository).as_posix()}", page, parser


def mockup_problems(repository, spec, spec_anchors):
    """mockups #checks-validator: every problem in the spec's mockup set, each naming the mockup and what is wrong."""
    problems = []
    for path, label, _, parser in mockup_pages(repository, spec):
        if not MOCKUP_NAME.fullmatch(path.name):
            problems.append(f"{label}: name must be <NN>-<screen>.mockup.html, two digits then lowercase words joined by hyphens")
        viewports = [frame["viewport"] for frame in parser.frames]
        # mockups #frame-viewports: a drawn mockup holds exactly one desktop and one phone frame for its screen, which
        # name no direction, and for each other direction it draws, which name it (#markup-frame).
        drawn = {None: []} if parser.frames else {}
        for frame in parser.frames:
            if frame["direction"] != "":  # an empty data-direction draws no direction to count; directions_problems names it
                drawn.setdefault(frame["direction"], []).append(frame["viewport"])
        for direction, found in drawn.items():
            whose = f' of direction "{direction}"' if direction else ""
            for viewport in VIEWPORTS:
                count = found.count(viewport)
                if count == 0:
                    problems.append(f"{label}: no {viewport} frame{whose}")
                elif count > 1:
                    problems.append(f"{label}: {count} {viewport} frames{whose}, needs exactly one")
        for viewport in sorted(set(viewports) - set(VIEWPORTS)):
            problems.append(f'{label}: unknown frame data-mockup-frame="{viewport}", use desktop or phone')
        duplicates = sorted({anchor for anchor in parser.anchors if parser.anchors.count(anchor) > 1})
        if duplicates:
            problems.append(f"{label}: anchor used more than once: " + ", ".join(duplicates))
        for frame in parser.frames:
            if not frame["illustrates"]:
                problems.append(f"{label}: {frame_name(frame)} names no spec anchor it illustrates")
        missing = sorted({anchor for anchor in parser.illustrated if anchor not in spec_anchors})
        if missing:
            problems.append(f"{label}: illustrates anchors the spec lacks: " + ", ".join(missing))
    return problems


def changed_mockup_problems(repository, spec, base, runtime):
    """mockups #checks-fidelity, #checks-warn: the fidelity and walkthrough checks on each mockup the change adds or
    edits against the exact base, as (problems, warnings), runtime being the spec page's (page_runtime); a mockup the
    change leaves untouched passes as it is. With no base, repair's whole-spec check, the chrome check only warns: only
    the project's own code replaces a drawn mockup of a screen it owns, and repair writes none."""
    problems, warnings = [], []
    for path, label, page, parser in mockup_pages(repository, spec):
        if base is not None:  # no base: repair's whole-spec check, where no mockup passes as it is
            prior = git_show(repository, base, path.relative_to(repository).as_posix())
            if prior.returncode == 0 and prior.stdout == page:
                continue
        # mockups #check-route: a prototype mockup names its route and draws no frame; a drawn one draws its frames.
        if parser.route and parser.frames:
            problems.append(f"{label}: names prototype route {parser.route} and draws frames, a prototype mockup draws no frame")
        elif not parser.route and not parser.frames:
            problems.append(f'{label}: names no prototype route and draws no frame, the missing route: add <meta name="mockup-prototype"> or its desktop and phone frames')
        elif parser.frames:
            if not parser.chrome:  # mockups #check-chrome, #prototype-drawn: only chrome the project does not own is drawn
                chrome = f'{label}: names no product whose chrome it draws in <meta name="mockup-chrome">; a screen the project owns is its running prototype, its route in <meta name="mockup-prototype">'
                if base is None:
                    warnings.append(f"warning: {chrome}")
                else:
                    problems.append(chrome)
            found, cautions = source_problems(repository, label, parser.source)
            problems += found + drawn_problems(repository, runtime, path, label, parser)
            warnings += cautions
        problems += walkthrough_problems(label, parser) + directions_problems(label, parser)
    return problems, warnings


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
        return "governing spec has no explicit data-user-story"
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


def validate_rule_marks(parser):
    """project-rules #declare-text: a project rule mark names the rule and sits on an acceptance criterion."""
    for anchor, criterion, name in parser.rule_marks:
        if not criterion:
            return f"project rule mark on {anchor or 'an element'} is not on an acceptance criterion"
        if not name.strip():
            return f"project rule mark on {anchor or 'a criterion'} needs a short name"
    return None


def validate_shape_sections(parser, unchanged):
    if parser.contract is None:
        return None
    if parser.contract != "shaped-sections-v1":
        return "unsupported data-spec-contract; use shaped-sections-v1"
    if parser.sections:
        expected_order = ("user-stories", "acceptance", "modular-boundaries")
        if tuple(section["kind"] for section in parser.top_level_sections[:3]) != expected_order:
            return "shaped spec sections must start with User stories, Acceptance criteria, Modular boundaries"
    user_sections = [
        section for section in parser.sections
        if section["kind"] == "user-stories" and has_heading(section, "User stories")
    ]
    if len(user_sections) != 1:
        return "governing spec needs exactly one visible User stories section"
    user_section = user_sections[0]
    if any(story["section"] is not user_section for story in parser.stories):
        return "every user story must be visibly contained in the User stories section"

    acceptance_sections = [
        section for section in parser.sections
        if section["kind"] == "acceptance" and has_heading(section, "Acceptance criteria")
    ]
    if len(acceptance_sections) != 1:
        return "governing spec needs a visible Acceptance criteria section"
    scopes = []
    for acceptance in acceptance_sections:
        scope = acceptance["scope"].casefold()
        if scope:
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", scope):
                return "Acceptance criteria data-acceptance-scope must be descriptive"
            if scope in {"deferred", "tbd", "unknown"}:
                return f"Acceptance criteria scope {scope} cannot be deferred"
            if scope in scopes:
                return f"Acceptance criteria scope {scope} is duplicated"
            scopes.append(scope)
        label = scope or "governing"
        if not acceptance["criteria"]:
            return f"{label} Acceptance criteria section needs at least one data-acceptance-criterion"
        criterion_anchors = [criterion["anchor"] for criterion in acceptance["criteria"]]
        if any(not anchor for anchor in criterion_anchors) or len(set(criterion_anchors)) != len(criterion_anchors):
            return f"every {label} acceptance criterion needs one unique data-anchor"
        for criterion in acceptance["criteria"]:
            if criterion["fields"].get("text") in unchanged:
                continue  # prompt-first-shaping #complete-criterion-judged: an unchanged criterion passes as it is
            if not criterion["fields"].get("scenario"):
                return f"{label} acceptance criterion {criterion['anchor']} needs an observable scenario"
            if not criterion["fields"].get("observable"):
                return f"{label} acceptance criterion {criterion['anchor']} needs an observable outcome"
            scenario_text = criterion["fields"]["scenario"]
            if not re.search(r'\bWhen\b', scenario_text):
                return f"{label} acceptance criterion {criterion['anchor']} scenario needs a When keyword"
            observable_text = criterion["fields"]["observable"]
            if len(observable_text.split()) < 2:
                return f"{label} acceptance criterion {criterion['anchor']} observable needs a verifiable outcome"

    story_anchors = {story["anchor"] for story in parser.stories}
    claimed = set()
    for acceptance in acceptance_sections:
        for criterion in acceptance["criteria"]:
            if not criterion["stories"]:
                return f"acceptance criterion {criterion['anchor']} needs data-story naming its user stories"
            unknown = [name for name in criterion["stories"] if name not in story_anchors]
            if unknown:
                return f"acceptance criterion {criterion['anchor']} names unknown story " + ", ".join(unknown)
            claimed.update(criterion["stories"])
    for story in parser.stories:
        if story["anchor"] not in claimed:
            return f"story {story['anchor']} is named by no acceptance criterion"

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


def unchanged_texts(base):
    """prompt-first-shaping #complete-criterion-judged: the text of each base criterion. A criterion that reads one
    word for word is unchanged, whatever its anchor, so the markup checks and the two-screen warning skip it."""
    return {criterion["fields"]["text"] for criterion in base.criteria if criterion["fields"].get("text")}


# prompt-first-shaping #complete-criterion-judged: a proof clause runs from "proven by" to the end of its sentence.
PROOF_CLAUSE = re.compile(r"\bproven\s+by\b.*?(?=[.!?](?:\s|$)|[;“”\"]|$)", re.I)


def dropped_proof_clauses(parser, base, kept=None, where="the changed spec", spec=""):
    """Each base criterion whose proof clause no criterion of the changed spec carries word for word. A criterion
    rewritten, renamed, split, merged, or deleted passes while some criterion carries its clause. A clause the criterion
    only quotes is none. kept, when given, also keeps a clause it carries elsewhere, as named by where."""
    carried = [criterion.get("carried", "") for criterion in parser.criteria]
    problems = []
    for criterion in base.criteria:
        for match in PROOF_CLAUSE.finditer(criterion.get("carried", "")):
            clause = match.group(0).strip()
            if not any(clause in text for text in carried) and not (kept and kept(clause)):
                problems.append(
                    f'criterion {criterion["anchor"] or "without an anchor"}{spec} at the base carries the proof clause '
                    f'"{clause}", which no criterion of {where} carries; keep it word for word '
                    "(prompt-first-shaping #complete-criterion-proof-clause)"
                )
    return problems


def parse_spec(page):
    parser = SpecParser()
    parser.feed(page.decode(errors="replace"))
    parser.close()
    return parser


def spec_names(repository, revision=None):
    """Each *.spec.html of the repository, tracked at the revision, or else in the working tree, ignored files aside."""
    if revision:
        listing = ("ls-tree", "-r", "-z", "--name-only", revision)
    else:
        listing = ("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    listed = subprocess.run(("git", "-C", str(repository), *listing), stdout=subprocess.PIPE, check=True).stdout
    names = sorted({name for name in listed.decode(errors="replace").split("\0") if name.endswith(".spec.html")})
    return names if revision else [name for name in names if (repository / name).is_file()]


def dropped_repository_proof_clauses(repository, spec, parser, base_spec, base):
    """repair-specs #rule-source-validator, prompt-first-shaping #acceptance-proof-clause-rule-repair: the committed
    specs carry each base proof clause word for word, so under the whole-spec check a clause is kept when a criterion
    of any spec of the repository carries it, as after a Home move or a split, and a clause of each spec deleted
    since the base that none carries fails too."""
    others = None

    def kept(clause):
        nonlocal others
        if others is None:  # read the repository's other specs once, only when a clause is missing from this one
            others = [
                criterion.get("carried", "")
                for name in spec_names(repository) if (repository / name).resolve() != spec
                for criterion in parse_spec((repository / name).read_bytes()).criteria
            ]
        return any(clause in text for text in others)

    where = "any spec of the repository"
    problems = dropped_proof_clauses(parser, base_spec, kept, where)
    for name in spec_names(repository, base):
        if not (repository / name).is_file():
            deleted = parse_spec(git_show(repository, base, name).stdout)
            problems += dropped_proof_clauses(parser, deleted, kept, where, f" of deleted spec {name}")
    return problems


# prompt-first-shaping #complete-criterion-two-screen-warning: words that suggest a Then reads from two screens.
NOT_A_NAME = frozenset(
    "a an the this that these those its their his her your my our one two both all each every any no same whole "
    "other another new current open per in on at to of into from by for with and or "
    # a key pressed, as in "pressing Tab" or "Shift Tab", is no surface
    "press presses pressing pressed hit hits hitting shift ctrl control alt option cmd command meta".split()
)
SURFACE = r"(screen|tab|page|route)"
NAMED_SURFACE = re.compile(r"\b([\w'’-]+)\s+" + SURFACE + r"(?![\w-])", re.I)
JOINED_SURFACES = re.compile(r"\b([\w'’-]+)\s+and\s+(?:the\s+)?([\w'’-]+)\s+" + SURFACE + r"s\b", re.I)
# A surface word after a possessive, followed by a noun it modifies, names no surface, as in "the user's page title";
# a name before it still names one, as in "the Board page header".
POSSESSIVE = re.compile(r"['’]s$")
MODIFIES = re.compile(
    r"\s+(?:titles?|headers?|headings?|bars?|keys?|order|content|contents|layout|load|readers?|width|height|size"
    r"|url|address|links?|icons?|labels?|name|number|count|footer|body|state|position|view|templates?|components?"
    r"|elements?|frames?|panels?|background|top|bottom|edge|border|strip|path|slug)\b",
    re.I,
)
NAVIGATION = re.compile(
    r"(?:\band\b|\bthen\b|[,;.])\s+((?:(?!(?:and|then)\b)[\w'’-]+\s+){0,4}?"
    r"(?:(?:returns|returning|goes\s+back|going\s+back|navigates|navigating|redirects|redirecting"
    r"|(?:is|are|gets?)\s+(?:taken|sent|brought|returned|redirected|navigated)"
    r"|takes\s+(?:the\s+\w+|them|you)|taking\s+(?:the\s+\w+|them|you))\s+(?:back\s+)?to\s+(?:the\s+)?[\w'’-]+"
    r"|(?:opens|opening|switches\s+to|switching\s+to)\s+(?:another|a\s+different|the\s+other|a\s+new|the\s+[\w'’-]+)\s+"
    + SURFACE + r"))",
    re.I,
)
# Focus moving is no navigation: it stays within one screen.
WITHIN_SCREEN = re.compile(r"\b(?:focus|cursor|caret|selection)\b", re.I)
OUTCOMES = re.compile(r"\s*(?:\band\b|\bthen\b|[,;.])\s*", re.I)
CLOSING = re.compile(r"\b(?:closes|closed|dismissed|disappears|goes\s+away)$", re.I)
# A surface the Then says it does not show, as in "no Settings page" or "not the whole tall page", is not read.
NOT_SHOWN = re.compile(r"\b(?:no|not)\s+(?:(?:the|a|an|any|whole|entire)\s+)*$", re.I)
NO_SCREEN = re.compile(r"\bno\s+screen\s+shows?\s+(?:it|them|this|these|either|any)\b", re.I)


def then_text(criterion):
    observable = criterion["fields"].get("observable")
    if observable:
        return observable
    match = re.search(r"\bThen\b.*", criterion["fields"].get("text", ""))
    return match.group(0) if match else ""


def navigation_step(then):
    """The first navigation step after which the Then still reads an outcome from the screen it left, or None.
    A Then whose only outcomes before the move are something closing reads from the screen the move reaches."""
    for match in NAVIGATION.finditer(then):
        if WITHIN_SCREEN.search(match.group(1)):
            continue
        before = [part.strip() for part in OUTCOMES.split(then[:match.start()]) if part.strip()]
        if all(CLOSING.search(part) for part in before):
            continue
        return match.group(1)
    return None


def two_screen_words(then):
    """The words that make a Then look to span two screens, or none."""
    if NO_SCREEN.search(then):  # the Then's outcome is on no screen
        return []
    then = PROOF_CLAUSE.sub("", re.sub(r"^\s*Then\b", "", then))  # how it is proven names no surface
    named = {}

    def name(qualifier, noun, end, words):
        key = POSSESSIVE.sub("", qualifier).casefold()
        if key not in NOT_A_NAME and not (POSSESSIVE.search(qualifier) and MODIFIES.match(then, end)):
            named.setdefault((key, noun.casefold()), words)

    for match in JOINED_SURFACES.finditer(then):
        first, second, noun = match.groups()
        if first.casefold() not in NOT_A_NAME and second.casefold() not in NOT_A_NAME:
            name(first, noun, match.end(), f"{first} {noun}")
            name(second, noun, match.end(), f"{second} {noun}")
    for match in NAMED_SURFACE.finditer(then):
        if not NOT_SHOWN.search(then, 0, match.start()):
            name(match.group(1), match.group(2), match.end(), match.group(0))
    words = list(named.values()) if len(named) > 1 else []
    navigation = navigation_step(then)
    if navigation:
        words.append(navigation)
    return words


def two_screen_warnings(parser, unchanged):
    """Warn on each added or edited criterion whose Then looks to span two screens and carries no reason."""
    warnings = []
    for criterion in parser.criteria:
        if criterion["one_screen"].strip() or criterion["fields"].get("text") in unchanged:
            continue
        words = two_screen_words(then_text(criterion))
        if words:
            label = criterion["anchor"] or "without an anchor"
            quoted = ", ".join(f'"{word}"' for word in words)
            warnings.append(
                f"warning: criterion {label} Then may span two screens ({quoted}); "
                'split it per the one-screen rule or give the reason as data-one-screen="<reason>"'
            )
    return warnings


# prompt-first-shaping #complete-mechanics-warning: look and motion mechanics a clause states, a color value, an easing
# curve, or a motion timeline of two or more durations under a second.
# A call counts only with the arguments CSS gives it, so prose such as "lab(s)" or "steps(1 to 3)" is none.
CSS_NUMBER = r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)"
COLOR_ARGUMENT = r"(?:" + CSS_NUMBER + r"(?:%|deg|grad|rad|turn)?|none)"
COLOR_VALUE = re.compile(
    r"(?<![\w#/.-])#(?:[0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})(?![\w-])"
    r"|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(\s*" + COLOR_ARGUMENT + r"(?:\s*[,/]?\s*" + COLOR_ARGUMENT + r")*\s*\)",
    re.I,
)
LINEAR_STOP = CSS_NUMBER + r"(?:\s+" + CSS_NUMBER + r"%){0,2}"
EASING_CALL = (
    r"cubic-bezier\(\s*" + r"\s*,\s*".join([CSS_NUMBER] * 4) + r"\s*\)"
    r"|steps\(\s*\d+\s*(?:,\s*(?:jump-start|jump-end|jump-none|jump-both|start|end)\s*)?\)"
    r"|linear\(\s*" + LINEAR_STOP + r"(?:\s*,\s*" + LINEAR_STOP + r")*\s*\)"
)
EASING_CURVE = re.compile(
    r"(?i:\b(" + EASING_CALL + r")"
    r"|\b(ease-in-out|ease-in|ease-out|step-start|step-end)\b(?!-)"
    # a bare named easing only where the clause names it as one, as in "a linear easing", never "the history is linear"
    r"|\b(ease|linear)(?=\s+(?:easing|curve|timing)\b)"
    r"|\beasing\s*(?::|of\b|is\b|set\s+to\b)?\s*\b(ease|linear)\b)"
    r"|\b(ease(?:In|Out|InOut)\w*)"
)
UNITS = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
TENS = "twenty thirty forty fifty sixty seventy eighty ninety"
NUMBERS = {word: value for value, word in enumerate(UNITS.split())}
NUMBERS.update({word: 10 * tens for tens, word in enumerate(TENS.split(), 2)})
NUMBER_WORD = r"(?:" + "|".join(sorted(NUMBERS, key=len, reverse=True)) + r"|hundred|thousand)"
SPOKEN_DURATION = re.compile(
    # a number with thousands separators is one number, as in "1,500 ms"
    r"(?i)(?<![\w.,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d*\.?\d+)[\s-]*(ms|msecs?|milliseconds?|s|secs?|seconds?)\b"
    r"|\b((?:(?:a|" + NUMBER_WORD + r")[\s-]+(?:and\s+)?)*" + NUMBER_WORD + r")\s+(milliseconds?|seconds?)\b"
    r"|\b(half\s+a\s+second|a\s+(?:half|quarter|tenth|fifth)(?:\s+of\s+a)?\s+second)\b"
)


def number_value(words):
    total = current = 0
    for word in re.split(r"[\s-]+", words.casefold()):
        if word == "hundred":
            current = (current or 1) * 100
        elif word == "thousand":
            total, current = total + (current or 1) * 1000, 0
        else:
            current += NUMBERS.get(word, 0)  # "a" and "and" add nothing
    return total + current


def short_durations(text):
    """Each duration under a second the text states, in order."""
    found = []
    for match in SPOKEN_DURATION.finditer(text):
        digits, unit, words, word_unit, fraction = match.groups()
        if digits:
            seconds = float(digits.replace(",", "")) / (1000 if unit.casefold().startswith("m") else 1)
        elif words:
            seconds = number_value(words) / (1000 if word_unit.casefold().startswith("m") else 1)
        if fraction or seconds < 1:  # half, a quarter, a tenth, or a fifth of a second is always under one
            found.append(match.group(0))
    return found


def color_hex(value):
    """A hex value reads as a color, every six- or eight-digit one, as #fafafa or #abcdef, and a three- or four-digit
    one holding a digit or repeating one letter or one pair, as #fff or #abab; a word of three or four hex letters
    after a hash, as #add, #cafe, or #fade, is none."""
    hexits = value[1:].casefold()
    return len(hexits) > 4 or any(char.isdigit() for char in hexits) or len(set(hexits)) == 1 or hexits == hexits[:2] * 2


def mechanics_words(clause, unchanged=frozenset()):
    """The words that make a clause or table state look or motion mechanics, or none. A clause's motion timeline is
    two durations under a second in it; a table's is two of the rows the change adds or edits holding one, a row as the
    base had it (unchanged) counting none; one duration is a timing the user feels."""
    words = []
    if clause["kind"] == "clause":
        text = clause["stated"][0]
        words += [match.group(0) for match in COLOR_VALUE.finditer(text) if match.group(0)[0] != "#" or color_hex(match.group(0))]
        words += [next(group for group in match.groups() if group) for match in EASING_CURVE.finditer(text)]
        durations = short_durations(text)
    else:  # a table's colors and easings are its rows', each its own clause
        rows = [short_durations(stated) for row, stated in zip(clause["rows"], clause["stated"]) if ("row", row) not in unchanged]
        durations = [duration for row in rows for duration in row] if sum(1 for row in rows if row) > 1 else []
    if len(durations) > 1:
        words += durations
    return list(dict.fromkeys(words))


def stated_clauses(base):
    """prompt-first-shaping #complete-mechanics-warning: each clause, table, and table row of the base as its text reads;
    one that reads the same, whatever its anchor, is unchanged and raises no mechanics warning, and a table's unchanged
    row counts toward none of its warnings."""
    stated = {(clause["kind"], clause["text"]) for clause in base.clauses}
    return stated | {("row", row) for clause in base.clauses for row in clause.get("rows", ())}


def mechanics_warnings(parser, unchanged):
    """Warn on each added or edited clause or table that states look or motion mechanics."""
    warnings = []
    for clause in parser.clauses:
        if (clause["kind"], clause["text"]) in unchanged:
            continue
        words = mechanics_words(clause, unchanged)
        if words:
            quoted = ", ".join(f'"{word}"' for word in words)
            warnings.append(
                f"warning: {clause['kind']} {clause['label']} states look or motion mechanics ({quoted}); "
                "move them to the design system and link it, or state the outcome the user feels"
            )
    return warnings


def main(argv):
    whole_spec = "--whole-spec" in argv[1:]
    args = [arg for arg in argv[1:] if arg != "--whole-spec"]
    if len(args) != 3:
        return fail("usage: validate-style.py [--whole-spec] <repository> <spec-html> <exact-base>")

    repository = Path(args[0]).resolve()
    spec = Path(args[1]).resolve()
    base = args[2]
    if not repository.is_dir() or not spec.is_file() or not inside(spec, repository):
        return fail("repository and spec must exist, with the spec inside the repository")
    if subprocess.run(("git", "-C", str(repository), "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"), stdout=subprocess.DEVNULL).returncode:
        return fail("exact base must resolve to a local Git commit")

    html = spec.read_bytes()
    parser = parse_spec(html)
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
    if prior_spec.returncode == 0 and prior_spec.stdout == html and not whole_spec:
        story_state = "unchanged"
        section_state = "legacy"
    else:
        base_spec = parse_spec(prior_spec.stdout)
        unchanged = set() if whole_spec else unchanged_texts(base_spec)
        story_error = validate_stories(parser, repository, spec)
        if story_error:
            return fail(story_error)
        section_error = validate_rule_marks(parser) or validate_shape_sections(parser, unchanged)
        if section_error:
            return fail(section_error)
        if whole_spec:
            dropped = dropped_repository_proof_clauses(repository, spec, parser, base_spec, base)
        else:
            dropped = dropped_proof_clauses(parser, base_spec)
        if dropped:
            return fail("\n".join(dropped))
        story_state = "valid"
        section_state = "valid" if parser.contract else "legacy"
        for warning in two_screen_warnings(parser, unchanged):
            print(warning, file=sys.stderr)
        for warning in mechanics_warnings(parser, set() if whole_spec else stated_clauses(base_spec)):
            print(warning, file=sys.stderr)

    problems = mockup_problems(repository, spec, parser.anchors)
    changed, warnings = changed_mockup_problems(repository, spec, None if whole_spec else base, page_runtime(spec, parser.runtimes))
    problems += changed
    for warning in warnings:
        print(warning, file=sys.stderr)
    if problems:
        return fail("\n".join(problems))

    print(f"style={style_state} stories={story_state} sections={section_state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
