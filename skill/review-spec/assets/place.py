#!/usr/bin/env python3
"""Resolve a review mark to its place in the current spec (review-state #anchoring, #model-place).

One resolver for every reader: review-serve.py adds `place` to /api/events, and watch-specs.sh
prints it beside the zero-wait scan rows. A mark is located in the spec version its event names
(`version`, the sha256 of that spec text), then mapped through a character diff to the current
text. Offsets are code-point indexes into the current spec source; `quote` is the mapped range's
visible text, the same text the page's text target search reads.

CLI: place.py SPEC < event file names  ->  place<TAB>name<TAB>state<TAB>#anchor<TAB>start-end<TAB>quote
"""

from __future__ import annotations

import difflib
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
        self.blocks = {}
        for element in self.elements:
            anchor = element.attrs.get("data-anchor")
            if anchor and anchor not in self.blocks:
                self.blocks[anchor] = element

    def visible(self, start, end):
        return "".join(char for at, _, char in self.chars if start <= at < end)

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

    def locate(self, body):
        """Source range of a mark: its anchored block, then the target within it (the quote for text)."""
        block = self.blocks.get(body.get("anchorId"))
        if block is None:
            return None
        target = body.get("target") or {}
        if target.get("type") == "text":
            needle = "".join(str(target.get("key") or body.get("quote") or "").split())
            chars = [char for char in self.chars if block.start <= char[0] < block.end and not char[2].isspace()]
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


def char_map(old, new):
    """Map each old character index to its new index, or None: a line diff refined by a character diff."""
    mapping = [None] * len(old)
    old_lines, new_lines = old.splitlines(keepends=True), new.splitlines(keepends=True)
    old_at, new_at = [0], [0]
    for line in old_lines:
        old_at.append(old_at[-1] + len(line))
    for line in new_lines:
        new_at.append(new_at[-1] + len(line))
    lines = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for op, i1, i2, j1, j2 in lines.get_opcodes():
        old_start, new_start = old_at[i1], new_at[j1]
        if op == "equal":
            for offset in range(old_at[i2] - old_start):
                mapping[old_start + offset] = new_start + offset
        elif op == "replace":
            # same line count: diff each line pair, which keeps a long rewrite linear in lines
            pairs = zip(range(i1, i2), range(j1, j2)) if i2 - i1 == j2 - j1 else [(None, None)]
            for i, j in pairs:
                a0, a1 = (old_at[i], old_at[i + 1]) if i is not None else (old_start, old_at[i2])
                b0, b1 = (new_at[j], new_at[j + 1]) if j is not None else (new_start, new_at[j2])
                chunk = difflib.SequenceMatcher(None, old[a0:a1], new[b0:b1], autojunk=False)
                for a, b, size in chunk.get_matching_blocks():
                    for offset in range(size):
                        mapping[a0 + a + offset] = b0 + b + offset
    return mapping


class Resolver:
    """Resolves every mark against one current spec text; version parses and diff maps are cached."""

    def __init__(self, current):
        self.current = Document(current)
        self._versions = {}

    def _version(self, text):
        if text not in self._versions:
            self._versions[text] = (Document(text), char_map(text, self.current.text))
        return self._versions[text]

    def place(self, body, version_text=None):
        if not isinstance(body, dict) or not body.get("anchorId"):
            return None
        text_target = (body.get("target") or {}).get("type") == "text"
        if version_text is None:
            located = self.current.locate(body)
            if located is None:
                return self._gone(body, None)
            return self._found(body, located[0], located[1], "kept", text_target)
        source, mapping = self._version(version_text)
        located = source.locate(body)
        if located is None:
            return self._gone(body, None)
        mapped = [mapping[index] for index in range(*located) if mapping[index] is not None]
        if not mapped:
            return self._gone(body, source.blocks.get(body.get("anchorId")))
        state = "kept" if len(mapped) == located[1] - located[0] else "changed"
        return self._found(body, min(mapped), max(mapped) + 1, state, text_target)

    def _found(self, body, start, end, state, text_target):
        anchor = self.current.innermost(start, end)
        quote = self.current.visible(start, end) if text_target else None
        return {"anchorId": anchor, "start": start, "end": end, "quote": quote, "state": state}

    def _gone(self, body, block):
        """Nearest surviving anchored block: the mark's own anchor, else its nearest surviving ancestor."""
        anchor = body.get("anchorId")
        while anchor not in self.current.blocks and block is not None:
            block = block.parent
            while block is not None and not block.attrs.get("data-anchor"):
                block = block.parent
            anchor = block.attrs.get("data-anchor") if block is not None else None
        return {"anchorId": anchor if anchor in self.current.blocks else None,
                "start": None, "end": None, "quote": None, "state": "gone"}


def resolve(body, current, version_text=None):
    """One mark's current place: {anchorId, start, end, quote, state}, or None for an event without one."""
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
        event["place"] = resolver.place(body, read_version(review, body.get("version")))
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
