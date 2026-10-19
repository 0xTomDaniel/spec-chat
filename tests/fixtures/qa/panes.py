#!/usr/bin/env python3
"""QA comment-layer collections (qa.toml nav acceptance-host-*, comment-layer.spec.html#acceptance).

usage: panes.py <site> <serve> <project>   (after reset.sh writes <serve>/registry.toml)

Each collection below gets two host pages beside its specs, docs/pane.html and docs/pane-other.html, from the pane.html
template: one item anchors the report spec's totals criterion (a criterion ID in the collection's scope, so its
comments are that spec's), one an agent turn (a subject that names no spec, so its comments are the scope's hosted
threads), and one an agent turn in a scope no row has. The review service serves them at
/<collection>/docs/pane.html, and the test host (host.py) at another address. A collection's scope is its slug, the
lane its spec rows name. Seeded threads are other browsers' than the capture browser: hosted threads under
<serve>/comments/<scope>/ (the service state holding the registry), spec-page comments in the report spec's spool.
Fixed names and stamps: every reset is identical.
"""

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spools  # noqa: E402  (the fixture's event naming and spec spool seeds)

FIXTURE = os.path.dirname(os.path.abspath(__file__))
TURN = "agent-turn:qa:42"
TURN_LABEL = "Agent turn 42"
FROM = "https://review.qa.invalid/host-pages/docs/pane.html"  # where a seeded comment was written
LAYER = "/static/comment-layer.js"  # the page's own service; served at another address, its base address names it
STOPPED = "http://127.0.0.1:9/static/comment-layer.js"  # nothing listens: the review service is stopped


def turn_comment(spool, ident, text, quote=None):
    """Papaya's comment on the agent turn, written on a host page, as the service keeps it in hosted threads."""
    body = {"id": ident, "event": "comment", "actor": "human", "browser": "qa-browser-papaya", "author": "Papaya",
            "subject": TURN, "kind": "Agent turn", "label": TURN_LABEL, "from": FROM,
            "target": {"type": "text", "key": quote[:40]} if quote else None, "quote": quote, "text": text}
    return spool._append(body)


def handed_off(spool, root):
    spool._append({"id": root + "-handoff", "event": "handoff", "actor": "human", "browser": "qa-browser-papaya",
                   "author": "Papaya", "anchorId": "", "target": None, "quote": None, "text": "batch from layer",
                   "events": [root]})


def answered(spool):
    root = turn_comment(spool, "qa-turn", "Question: why name the columns after the table headers?")
    handed_off(spool, root)
    spool._append({"id": "qa-turn-answer", "event": "reply", "actor": "agent", "respondsTo": root, "threadId": root,
                   "target": None, "text": "So the CSV reads like the table the manager sees.", "status": "acknowledged",
                   "change": "no spec change"})


def pending(spool):
    handed_off(spool, turn_comment(spool, "qa-turn", "Question: why name the columns after the table headers?"))


def quote_gone(spool):
    """The comment quotes words the turn no longer holds: the host shows it again with other text."""
    turn_comment(spool, "qa-turn", "Say which headers.", quote="named the columns after the report headers")


# Markup in every field of a stored event a thread shows, which the layer draws as text (review 1, B1).
MARKUP = '<img src=x onerror="window.__xss=1">'


def markup(spool):
    """A thread as any writer on disk could leave it: markup in its author, kind, label, quote, and texts, a script
    address as its from, and a status no thread lifecycle has, on the agent's reply and on a human status event."""
    root = spool._append({"id": "qa-markup", "event": "comment", "actor": "human", "browser": "qa-browser-papaya",
                          "author": MARKUP, "subject": TURN, "kind": MARKUP, "label": MARKUP,
                          "from": "javascript:window.__xss=2", "target": {"type": "text", "key": MARKUP[:40]},
                          "quote": MARKUP, "text": MARKUP})
    handed_off(spool, root)
    spool._append({"id": "qa-markup-answer", "event": "reply", "actor": "agent", "respondsTo": root, "threadId": root,
                   "target": None, "text": MARKUP, "status": MARKUP, "change": "no spec change"})
    spool._append({"id": "qa-markup-status", "event": "status", "actor": "human", "browser": "qa-browser-papaya",
                   "author": "Papaya", "respondsTo": root, "threadId": root, "status": MARKUP})


def spec_comment(spool):
    """A reviewer's comment on the totals criterion, written on the spec page."""
    spools.comment(spool, "qa-spec-totals", "qa-browser-papaya", "Papaya", "report-totals", None,
                   "Round totals to whole units?")


# collection -> (hosted threads seed, report spec spool seed, layer address)
COLLECTIONS = {
    "host-draft": (None, None, LAYER),
    "host-starters": (None, None, LAYER),
    "host-handoff": (None, None, LAYER),
    "host-handoff-two": (None, None, LAYER),
    "host-reply": (answered, None, LAYER),
    "host-live": (pending, None, LAYER),
    "host-same-subject": (pending, None, LAYER),
    "host-quote": (None, None, LAYER),
    "host-quote-gone": (quote_gone, None, LAYER),
    "host-only-comments": (None, None, LAYER),
    "host-phone": (None, None, LAYER),
    "host-frame": (None, None, LAYER),
    "one-thread-spec": (None, None, LAYER),
    "one-thread-host": (None, spec_comment, LAYER),
    "no-scope": (None, None, LAYER),
    "no-service": (None, None, STOPPED),
    "layer-markup": (markup, None, LAYER),
    "layer-stores": (None, None, LAYER),
    "layer-focus": (None, None, LAYER),
}


def write_events(review, files):
    for name, body in files:
        os.makedirs(os.path.join(review, body["actor"]), exist_ok=True)
        with open(os.path.join(review, body["actor"], name), "w", encoding="utf-8") as stream:
            json.dump(body, stream)


def build(site, serve, project):
    with open(os.path.join(FIXTURE, "pane.html"), encoding="utf-8") as stream:
        template = stream.read()
    shutil.rmtree(os.path.join(serve, "comments"), ignore_errors=True)  # hosted threads outlive no reset
    for collection, (hosted, spec, layer) in COLLECTIONS.items():
        for page, name in (("pane.html", "first"), ("pane-other.html", "second")):
            text = template
            for key, value in (("project", project), ("scope", collection), ("layer", layer), ("pane", name)):
                text = text.replace("{{%s}}" % key, value)
            with open(os.path.join(site, collection, "docs", page), "w", encoding="utf-8") as stream:
                stream.write(text)
        if hosted:
            spool = spools.Spool(None)
            hosted(spool)
            write_events(os.path.join(serve, "comments", collection), spool.files)
        if spec:
            path = os.path.join(site, collection, spools.SPEC)
            with open(path, "rb") as stream:
                raw = stream.read()
            spool = spools.Spool(spools.hashlib.sha256(raw).hexdigest())
            spec(spool)
            review = path + ".review"
            os.makedirs(os.path.join(review, "versions"))
            with open(os.path.join(review, "versions", spool.version + ".html"), "wb") as stream:
                stream.write(raw)
            write_events(review, spool.files)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: panes.py <site> <serve> <project>")
    build(*sys.argv[1:])
