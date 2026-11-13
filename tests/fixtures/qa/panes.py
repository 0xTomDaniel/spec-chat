#!/usr/bin/env python3
"""QA comment-layer collections (qa.toml nav acceptance-host-*, comment-layer.spec.html#acceptance).

usage: panes.py <site> <serve> <project>   (after reset.sh writes <serve>/registry.toml)

Each collection below gets two host pages beside its specs, docs/pane.html and docs/pane-other.html, from the pane.html
template: one item anchors the report spec's totals criterion (a criterion ID in the collection's scope, so its
comments are that spec's), one an agent turn (a subject that names no spec, so its comments are the scope's hosted
threads), and one an agent turn in a scope no row has; a PLACE collection's pages open on one more item, a band
keeping the layer's row clear, and end on a tall tail; a VIDEO collection's open on an item showing a video; a STAGE collection's on an evidence item in a scope
that holds no spec, naming a lane or none, its seed in that scope's hosted threads. The shell-native and
shell-box-picture collections are the scopes of the test shell's native canvas page (shell.py), which names lane cards
in them. The review service serves them at
/<collection>/docs/pane.html, and the test host (host.py) at another address. A collection's scope is its slug, the
lane its spec rows name. Seeded threads are other browsers' than the capture browser, but for the hand-off
collections' drafts, its own by its start point's identity: hosted threads under <serve>/comments/<scope>/ (the service
state holding the registry), spec-page comments in the report spec's spool.
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


def two_threads(spool):
    """Two threads on the agent turn, begun at different times: Papaya's, then Mango's, each handed off."""
    handed_off(spool, turn_comment(spool, "qa-turn-first", "Question: why a CSV and not a sheet?"))
    later = spool._append({"id": "qa-turn-second", "event": "comment", "actor": "human", "browser": "qa-browser-mango",
                           "author": "Mango", "subject": TURN, "kind": "Agent turn", "label": TURN_LABEL, "from": FROM,
                           "target": None, "quote": None, "text": "Change: name the columns after the report."})
    spool._append({"id": "qa-turn-second-handoff", "event": "handoff", "actor": "human", "browser": "qa-browser-mango",
                   "author": "Mango", "anchorId": "", "target": None, "quote": None, "text": "batch from layer",
                   "events": [later]})


def spec_comment(spool):
    """A reviewer's comment on the totals criterion, written on the spec page."""
    spools.comment(spool, "qa-spec-totals", "qa-browser-papaya", "Papaya", "report-totals", None,
                   "Round totals to whole units?")


# comment-layer #acceptance-host-handoff, -two: the Givens' drafts, saved on the host page, never handed off, the capture
# browser's own: Papaya's, the identity its start point keeps (qa.toml nav storage).
def turn_draft(text):
    """Papaya's draft on the agent turn, in the scope's hosted threads."""
    return lambda spool: turn_comment(spool, "qa-turn-draft", text)


def criterion_draft(spool):
    """Papaya's draft on Totals per week, as the service keeps a host page's comment on a criterion (review-serve.py
    placed_event): in the report spec's spool at the criterion's anchor, with its kind, label, and from, and no version."""
    spool._append({"id": "qa-totals-draft", "event": "comment", "actor": "human", "browser": "qa-browser-papaya",
                   "author": "Papaya", "anchorId": "report-totals", "kind": "Criterion evidence",
                   "label": "Totals per week", "from": FROM, "target": None, "quote": None,
                   "text": "Round to whole units."})


# comment-layer #ui-place, #acceptance-host-place-*: Agent turn 46 keeps the layer's row clear as a host does, a band
# 43 px tall (63 px on a phone) holding the row, its host button Full text just below the row at its right; Full text
# shows the turn's full text, so the item grows and the items below it move down. The tail makes the page tall enough
# to scroll the item's top above the screen.
PLACE = {"host-place-scrolled", "host-place-grows"}
PLACE_ITEM = """<section class="item banded" data-comment-subject="agent-turn:qa:46" data-comment-scope="{{scope}}" \
data-comment-kind="Agent turn" data-comment-label="Agent turn 46">
<button type="button" class="full" aria-expanded="false" aria-controls="turn-46-full">Full text</button>
<h2>Agent turn, in short</h2>
<p>I split the export into a CSV and a summary line.</p>
<div id="turn-46-full" hidden><p>The CSV keeps one row per week and team; the summary line adds the weeks up, so the \
totals match the table the manager reads.</p></div>
<script>document.currentScript.parentElement.querySelector('.full').addEventListener('click', e => {
  const full = document.getElementById(e.currentTarget.getAttribute('aria-controls'));
  full.hidden = !full.hidden;
  e.currentTarget.setAttribute('aria-expanded', String(!full.hidden));
});</script>
</section>
"""
PLACE_TAIL = """<p class="tail">Earlier turns.</p>
"""

# threads #acceptance-video-layer: an item showing the report page's walkthrough video (spools.py write_video), a thing in
# no spec, so its comments are the scope's hosted threads.
VIDEO = {"layer-video"}
VIDEO_ITEM = """<section class="item" data-comment-subject="qa-run:qa:7" data-comment-scope="{{scope}}" \
data-comment-kind="QA run" data-comment-label="Report walkthrough">
<h2>QA run</h2>
<p>The report page's walkthrough, recorded from the build.</p>
<video src="walkthrough.webm" controls preload="auto" width="320" height="200"></video>
</section>
"""

# threads #acceptance-stage-lane, -none: an evidence item in a scope that holds no spec, a scope row reset.sh registers on
# the review service, so its comments are that scope's hosted threads; its anchor names the lane spools.py STAGES names at
# QA, or no lane. collection -> (its scope, the lane its anchor names or None)
STAGE = {"layer-stage-lane": ("stage-lane-evidence", "qa-20"), "layer-stage-none": ("stage-none-evidence", None),
         "layer-stage-reply-lane": ("stage-reply-lane-evidence", "qa-20")}
STAGE_ITEM = """<section class="item" data-comment-subject="qa-run:qa:9" data-comment-scope="{{scope}}" \
data-comment-kind="QA run" data-comment-label="Weekly report QA run"{{lane}}>
<h2>QA run</h2>
<p>The weekly report's criteria, captured from the build: three passed.</p>
</section>
"""

# threads #acceptance-stage-reply-lane: Mango's comment on that evidence item, which recorded its lane, qa-20, at QA, and
# the agent's reply to it, stamped QA as emit-reply.sh stamps the stage of its thread's lane (#stage-tag-source).
def stage_replied(spool):
    root = spool._append({"id": "qa-run-9", "event": "comment", "actor": "human", "browser": "qa-browser-mango",
                          "author": "Mango", "subject": "qa-run:qa:9", "kind": "QA run", "label": "Weekly report QA run",
                          "from": FROM, "target": None, "quote": None, "text": "Which criteria did this run skip?",
                          "lane": "qa-20", "stage": "QA"})
    spool._append({"id": "qa-run-9-handoff", "event": "handoff", "actor": "human", "browser": "qa-browser-mango",
                   "author": "Mango", "anchorId": "", "target": None, "quote": None, "text": "batch from layer",
                   "events": [root]})
    spool._append({"id": "qa-run-9-answer", "event": "reply", "actor": "agent", "respondsTo": root, "threadId": root,
                   "target": None, "text": "None: it ran all three.", "status": "acknowledged",
                   "change": "no spec change", "stage": "QA"})


# shell #acceptance-shell-native: the shell's native canvas page (shell.py /canvas/<collection>) shows lane cards,
# things with no page element in the collection's scope; Papaya's thread on the QA-1514 card, handed off.
NATIVE_CARD = "lane-card:qa:1514"


def native_thread(spool):
    root = spool._append({"id": "qa-card-1514", "event": "comment", "actor": "human", "browser": "qa-browser-papaya",
                          "author": "Papaya", "subject": NATIVE_CARD, "kind": "Lane card",
                          "label": "QA-1514 card on the floor", "from": FROM, "target": None, "quote": None,
                          "text": "Why does this card still say QA failed?"})
    handed_off(spool, root)


# threads #root-chip-box on a held host page: Mango's thread on a box drawn on Agent turn 42, a thing with no video.
def boxed_turn(spool):
    root = spool._append({"id": "qa-turn-box", "event": "comment", "actor": "human", "browser": "qa-browser-mango",
                          "author": "Mango", "subject": TURN, "kind": "Agent turn", "label": TURN_LABEL, "from": FROM,
                          "target": None, "quote": None, "text": "This line reads as an error.",
                          "box": {"left": 0.5, "top": 0.25, "width": 0.25, "height": 0.5}})
    handed_off(spool, root)


# collection -> (hosted threads seed, report spec spool seed, layer address)
COLLECTIONS = {
    "host-draft": (None, None, LAYER),
    "host-starters": (None, None, LAYER),
    "host-handoff": (turn_draft("Saved on the host page."), None, LAYER),
    "host-handoff-two": (turn_draft("Why the table headers?"), criterion_draft, LAYER),
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
    "layer-stream": (two_threads, None, LAYER),
    "layer-video": (None, None, LAYER),
    "host-place-scrolled": (None, None, LAYER),
    "host-place-grows": (None, None, LAYER),
    "shell-host-item": (two_threads, None, LAYER),  # held by the test shell (shell #acceptance-shell-host-item)
    "shell-host-box": (boxed_turn, None, LAYER),  # held by the test shell (threads #root-chip-box)
    "shell-native": (native_thread, None, LAYER),  # the shell's native page's scope (shell #acceptance-shell-native)
    "shell-box-picture": (None, None, LAYER),  # the same, no thread (threads #acceptance-box-picture)
    **{collection: (None, None, LAYER) for collection in STAGE},
    "layer-stage-reply-lane": (stage_replied, None, LAYER),  # its seed in its evidence scope's hosted threads
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
            place = collection in PLACE  # filled first: the item's markup names the scope
            first = PLACE_ITEM if place else VIDEO_ITEM if collection in VIDEO else ""
            if collection in STAGE:  # its own scope and lane, filled before the page's
                scope, lane = STAGE[collection]
                first = STAGE_ITEM.replace("{{scope}}", scope).replace(
                    "{{lane}}", ' data-comment-lane="%s"' % lane if lane else "")
            for key, value in (("place", first), ("tail", PLACE_TAIL if place else ""),
                               ("project", project), ("scope", collection), ("layer", layer), ("pane", name)):
                text = text.replace("{{%s}}" % key, value)
            with open(os.path.join(site, collection, "docs", page), "w", encoding="utf-8") as stream:
                stream.write(text)
        if collection in VIDEO:
            spools.write_video(os.path.join(site, collection))
        if hosted:  # in the scope its page's first item names: a stage collection's evidence scope, else its own
            spool = spools.Spool(None)
            hosted(spool)
            write_events(os.path.join(serve, "comments", STAGE[collection][0] if collection in STAGE else collection),
                         spool.files)
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
