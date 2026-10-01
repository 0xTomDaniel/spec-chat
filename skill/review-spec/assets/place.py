#!/usr/bin/env python3
"""Resolve a review mark to its place in the current spec (review-state #anchoring, #model-place).

One resolver for every reader: review-serve.py adds `place` to /api/events, and watch-specs.sh
prints it beside the zero-wait scan rows. A mark is a comment: a thread is placed by its root
comment (its version, target, quote); replies and edits never move it. A mark is located in the
spec version its event names (`version`, the sha256 of that spec text), then mapped through a
character diff (char_map) to the current text. Offsets are code-point indexes into the current
spec source; `quote` is the mapped range's visible text, the same text the page's text target
search reads.

An element mark also gets `key`, the element's target key in the current spec, so a pin follows
the element when its peers shift.

CLI: place.py SPEC < event file names  ->  place<TAB>name<TAB>state<TAB>#anchor<TAB>start-end<TAB>quote
"""

from __future__ import annotations

import bisect
import difflib
import hashlib
import html
import json
import os
import re
import sys
from html.parser import HTMLParser

VERSION_RE = re.compile(r"[0-9a-f]{64}\Z")
ENTITY_RE = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);?")
SEGMENT_RE = re.compile(r"([a-z0-9-]+)(?:\[(\d+)\]|#(.+))\Z")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
HIDDEN = {"script", "style", "template"}
ALIASES = {"title": "h1", "breadcrumbs": "nav"}


class _Element:
    __slots__ = ("tag", "attrs", "start", "end", "parent", "children")

    def __init__(self, tag, attrs, start, parent):
        self.tag, self.attrs, self.start, self.end, self.parent, self.children = tag, attrs, start, None, parent, []


class _Parser(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=False)
        self.text = text
        self.lines = [0] + [index + 1 for index, char in enumerate(text) if char == "\n"]
        self.root = _Element("#root", {}, 0, None)
        self.stack = [self.root]
        self.elements = []
        self.chars = []  # (source start, source end, visible text) in document order

    def _offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def _open(self, tag, attrs, void):
        start = self._offset()
        element = _Element(tag, dict(attrs), start, self.stack[-1])
        self.stack[-1].children.append(element)
        self.elements.append(element)
        if void:
            element.end = start + len(self.get_starttag_text() or "")
        else:
            self.stack.append(element)

    def handle_starttag(self, tag, attrs):
        self._open(tag, attrs, tag in VOID)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, attrs, True)

    def handle_endtag(self, tag):
        if not any(element.tag == tag for element in self.stack[1:]):
            return
        start = self._offset()
        close = self.text.find(">", start)
        end = len(self.text) if close < 0 else close + 1
        while True:
            element = self.stack.pop()
            element.end = end if element.tag == tag else start
            if element.tag == tag:
                return

    def _visible(self):
        return not any(element.tag in HIDDEN for element in self.stack)

    def handle_data(self, data):
        if self._visible():
            start = self._offset()
            self.chars.extend((start + index, start + index + 1, char) for index, char in enumerate(data))

    def _reference(self):
        if self._visible():
            start = self._offset()
            match = ENTITY_RE.match(self.text, start)
            end = match.end() if match else start + 1
            self.chars.append((start, end, html.unescape(self.text[start:end])))

    def handle_entityref(self, name):
        self._reference()

    def handle_charref(self, name):
        self._reference()

    def finish(self):
        self.feed(self.text)
        self.close()
        for element in self.stack[1:]:
            element.end = len(self.text)
        self.root.end = len(self.text)
        return self


class Document:
    """One spec text parsed once: anchored blocks, element tree, and visible characters."""

    def __init__(self, text):
        self.text = text
        parser = _Parser(text).finish()
        self.root, self.elements, self.chars = parser.root, parser.elements, parser.chars
        self.char_starts = [at for at, _, _ in self.chars]
        self.blocks = {}
        for element in self.elements:
            anchor = element.attrs.get("data-anchor")
            if anchor and anchor not in self.blocks:
                self.blocks[anchor] = element

    def _chars(self, start, end):
        """Visible characters whose source starts in [start, end), by bisection: no whole-document scan."""
        return self.chars[bisect.bisect_left(self.char_starts, start):bisect.bisect_left(self.char_starts, end)]

    def visible(self, start, end):
        return "".join(char for _, _, char in self._chars(start, end))

    def innermost(self, start, end):
        best = None
        for anchor, element in self.blocks.items():
            if element.start <= start and end <= element.end and (best is None or element.start >= best[1].start):
                best = (anchor, element)
        return best[0] if best else None

    def _descendants(self, element):
        for child in element.children:
            yield child
            yield from self._descendants(child)

    def _element(self, block, key):
        """The element a target key names within its block, as the runtime's resolveElement reads it."""
        segments = str(key or "").split("/")
        scope, found = list(self._descendants(block)), None
        for index, segment in enumerate(segments):
            match = SEGMENT_RE.match(segment)
            if not match:
                return None
            name, nth, ident = match.groups()
            if name == "figure" and index == 0:
                peers = [element for element in scope if "data-render-target" in element.attrs]
            else:
                tag = ALIASES.get(name, name) if index == 0 else name
                peers = [element for element in scope if element.tag == tag]
            if ident is not None:
                peers = [element for element in peers if element.attrs.get("id") == ident]
                found = peers[0] if peers else None
            else:
                position = int(nth) - 1
                found = peers[position] if 0 <= position < len(peers) else None
            if found is None:
                return None
            scope = found.children
        return found

    def key(self, block, element):
        """The target key the runtime's elementDescriptor/svgDescriptor gives `element` within `block`."""
        if element is block:
            return None
        chain, node = [], element
        while node is not None and node is not block and node.tag != "svg":
            chain.insert(0, node)
            node = node.parent
        if node is None:
            return None
        inside = list(self._descendants(block))
        if node.tag == "svg":
            svg = node
            ident = svg.attrs.get("id")
            parts = ["svg#" + ident if ident else "svg[%d]" % ([e for e in inside if e.tag == "svg"].index(svg) + 1)]
            for item in chain:
                if item.attrs.get("id"):
                    parts.append(item.tag + "#" + item.attrs["id"])
                else:
                    peers = [e for e in item.parent.children if e.tag == item.tag]
                    parts.append("%s[%d]" % (item.tag, peers.index(item) + 1))
            return "/".join(parts)
        figure = "data-render-target" in element.attrs
        ident = element.attrs.get("id")
        if not figure and ident and sum(1 for e in inside if e.tag == element.tag and e.attrs.get("id") == ident) == 1:
            return element.tag + "#" + ident
        peers = [e for e in inside if ("data-render-target" in e.attrs if figure else e.tag == element.tag)]
        name = "figure" if figure else {"h1": "title", "nav": "breadcrumbs"}.get(element.tag, element.tag)
        return "%s[%d]" % (name, peers.index(element) + 1)

    def element_at(self, tag, at, start, end):
        """The current element a mapped element became: the same tag at its mapped start, else the
        innermost same-tag element around the mapped range."""
        best = None
        for element in self.elements:
            if element.tag != tag:
                continue
            if element.start == at:
                return element
            if element.start <= start and end <= element.end and (best is None or element.start >= best.start):
                best = element
        return best

    def locate(self, body):
        """Source range of a mark: its anchored block, then the target within it (the quote for text)."""
        block = self.blocks.get(body.get("anchorId"))
        if block is None:
            return None
        target = body.get("target") or {}
        if target.get("type") == "text":
            needle = "".join(str(body.get("quote") or target.get("key") or "").split())  # the key is only a prefix
            chars = [char for char in self._chars(block.start, block.end) if not char[2].isspace()]
            haystack, owner = "", []  # an entity may decode to several code points
            for index, (_, _, char) in enumerate(chars):
                owner.extend([index] * len(char))
                haystack += char
            at = haystack.find(needle) if needle else -1
            if at < 0:
                return None
            return chars[owner[at]][0], chars[owner[at + len(needle) - 1]][1]
        if target.get("type") == "element":
            element = self._element(block, target.get("key"))
            if element is not None:
                return element.start, element.end
        return block.start, block.end


TOKEN_RE = re.compile(r"\w+|\s+|[^\w\s]")
EXACT_TOKENS = 250_000  # token pairs an exact (no autojunk) diff may compare: about 20 ms in the worst case


def char_map(old, new):
    """Map each old character index to its new index, or None. Lines are diffed first; a replaced run
    of lines is refined by a token diff (words, spaces, punctuation), and 1:1 replaced tokens by a
    character diff. Characters of an unpaired token map to None, so a reworded mark is changed and a
    removed one gone."""
    mapping = [None] * len(old)
    old_lines, new_lines = old.splitlines(keepends=True), new.splitlines(keepends=True)
    old_at, new_at = [0], [0]
    for line in old_lines:
        old_at.append(old_at[-1] + len(line))
    for line in new_lines:
        new_at.append(new_at[-1] + len(line))
    lines = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for op, i1, i2, j1, j2 in lines.get_opcodes():
        a0, b0 = old_at[i1], new_at[j1]
        if op == "equal":
            for offset in range(old_at[i2] - a0):
                mapping[a0 + offset] = b0 + offset
        elif op == "replace":
            old_tokens = [(m.start(), m.group()) for m in TOKEN_RE.finditer(old, a0, old_at[i2])]
            new_tokens = [(m.start(), m.group()) for m in TOKEN_RE.finditer(new, b0, new_at[j2])]
            _map_tokens(mapping, old_tokens, new_tokens)
    return mapping


def _map_tokens(mapping, old_tokens, new_tokens):
    """Token diff of one replaced run. A run within EXACT_TOKENS is diffed exactly. A larger one uses
    popular-token junking, which keeps cost near linear in spec size but can miss text built of common
    words, so each of its unmatched gaps within EXACT_TOKENS is diffed again exactly."""
    exact = len(old_tokens) * len(new_tokens) <= EXACT_TOKENS
    tokens = difflib.SequenceMatcher(None, [t for _, t in old_tokens], [t for _, t in new_tokens], autojunk=not exact)
    for top, k1, k2, l1, l2 in tokens.get_opcodes():
        if not exact and top != "equal" and k1 < k2 and l1 < l2 and (k2 - k1) * (l2 - l1) <= EXACT_TOKENS:
            _map_tokens(mapping, old_tokens[k1:k2], new_tokens[l1:l2])
        elif top == "equal" or (top == "replace" and k2 - k1 == l2 - l1):
            for (at, token), (to, other) in zip(old_tokens[k1:k2], new_tokens[l1:l2]):
                same = [(0, 0, len(token))] if top == "equal" else difflib.SequenceMatcher(
                    None, token, other, autojunk=False).get_matching_blocks()  # 1:1 pair: refine by character
                for a, b, size in same:
                    for offset in range(size):
                        mapping[at + a + offset] = to + b + offset


class Resolver:
    """Resolves every mark against one current spec text. Version parses and diff maps are cached by
    version, and each mark's place by its event body: a page polls often, and both are fixed per spec text."""

    def __init__(self, current):
        self.current = Document(current)
        self._versions = {}
        self._places = {}

    def _version(self, version, text):
        if version not in self._versions:
            self._versions[version] = (Document(text), char_map(text, self.current.text))
        return self._versions[version]

    def place(self, body, version_text=None, review=None):
        """One mark's place. With `review`, the version text is read from its spool only when not cached."""
        if not isinstance(body, dict) or body.get("event") != "comment" or not body.get("anchorId"):
            return None  # a thread is placed by its root comment; replies and edits never move it
        memo = json.dumps(body, sort_keys=True)
        if memo in self._places:
            return self._places[memo]
        version = body.get("version")
        if review is not None and version not in self._versions:
            version_text = read_version(review, version)
        if version not in self._versions and version_text is not None:
            version = version or hashlib.sha256(version_text.encode("utf-8")).hexdigest()
        known = version in self._versions or version_text is not None
        result = self._place(body, version if known else None, version_text)
        if known or not body.get("version"):  # a named version not yet stored may arrive: resolve again then
            self._places[memo] = result
        return result

    def _place(self, body, version, version_text):
        target = body.get("target") or {}
        text_target = target.get("type") == "text"
        if version is None:
            located = self.current.locate(body)
            if located is None:
                return self._gone(body, None)
            element = None
            if target.get("type") == "element":
                element = self.current._element(self.current.blocks[body["anchorId"]], target.get("key"))
            return self._found(located[0], located[1], "kept", text_target, element)
        source, mapping = self._version(version, version_text)
        located = source.locate(body)
        if located is None:
            return self._gone(body, None)
        mapped = [mapping[index] for index in range(*located) if mapping[index] is not None]
        if not mapped:
            return self._gone(body, source.blocks.get(body.get("anchorId")))
        state = "kept" if len(mapped) == located[1] - located[0] else "changed"
        start, end, element = min(mapped), max(mapped) + 1, None
        if target.get("type") == "element":
            old = source._element(source.blocks[body["anchorId"]], target.get("key"))
            if old is not None:
                element = self.current.element_at(old.tag, mapping[old.start], start, end)
        return self._found(start, end, state, text_target, element)

    def _found(self, start, end, state, text_target, element):
        anchor = self.current.innermost(start, end)
        quote = self.current.visible(start, end) if text_target else None
        key = None
        if element is not None and anchor is not None:
            block = self.current.blocks[anchor]
            if block.start <= element.start and element.end <= block.end:
                key = self.current.key(block, element)
        return {"anchorId": anchor, "start": start, "end": end, "quote": quote, "state": state, "key": key}

    def _gone(self, body, block):
        """Nearest surviving anchored block: the mark's own anchor, else its nearest surviving ancestor."""
        anchor = body.get("anchorId")
        while anchor not in self.current.blocks and block is not None:
            block = block.parent
            while block is not None and not block.attrs.get("data-anchor"):
                block = block.parent
            anchor = block.attrs.get("data-anchor") if block is not None else None
        return {"anchorId": anchor if anchor in self.current.blocks else None,
                "start": None, "end": None, "quote": None, "state": "gone", "key": None}


def resolve(body, current, version_text=None):
    """One mark's current place: {anchorId, start, end, quote, state, key}, or None for an event that is
    not a comment with an anchor."""
    return Resolver(current).place(body, version_text)


def read_version(review, version):
    """Spec text an event was written against: <spec>.review/versions/<sha256>.html, or None."""
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        return None
    path = os.path.join(review, "versions", version + ".html")
    if not os.path.realpath(path).startswith(os.path.realpath(review) + os.sep):
        return None
    try:
        with open(path, encoding="utf-8") as stream:
            return stream.read()
    except (OSError, UnicodeDecodeError):
        return None


_RESOLVERS = {}


def resolve_events(spec, events):
    """Set `place` on each {"body": event} from the spool beside spec; None where it has no mark."""
    try:
        with open(spec, encoding="utf-8") as stream:
            current = stream.read()
    except (OSError, UnicodeDecodeError):
        current = None
    resolver = _RESOLVERS.get(spec)
    if current is not None and (resolver is None or resolver.current.text != current):
        resolver = _RESOLVERS[spec] = Resolver(current)  # pages poll often; parse and diff once per spec text
    if current is None:
        resolver = None
    review = spec + ".review"
    for event in events:
        body = event.get("body")
        if resolver is None or not isinstance(body, dict):
            event["place"] = None
            continue
        event["place"] = resolver.place(body, review=review)
    return events


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: place.py SPEC < event file names", file=sys.stderr)
        return 2
    spec = argv[0]
    events = []
    for name in sys.stdin.read().splitlines():
        try:
            with open(os.path.join(spec + ".review", "human", name), encoding="utf-8") as stream:
                events.append({"name": name, "body": json.load(stream)})
        except (OSError, ValueError):
            continue
    for event in resolve_events(spec, events):
        value = event["place"]
        if not value:
            continue
        span = "-" if value["start"] is None else "%d-%d" % (value["start"], value["end"])
        quote = " ".join((value["quote"] or "").split())
        print("place\t%s\t%s\t#%s\t%s\t%s" % (event["name"], value["state"], value["anchorId"] or "", span, quote))
    return 0


if __name__ == "__main__":
    sys.exit(main())
