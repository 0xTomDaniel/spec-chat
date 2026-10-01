#!/usr/bin/env python3
"""QA review-state collections (qa.toml nav acceptance-*, review-state.spec.html#acceptance).

usage: spools.py <site>   (after reset.sh copies the head repo to <site>/<collection>)

Each collection below gets seeded spool events on its report spec, as the page and the
agent would have left them (skill/review-spec/references/event-schema.md), and, where a
criterion needs it, the agent's edit to the spec. Seeded human events name the head spec
text as their `version` and the version file holds it, as the service writes on serve.
Seeded reviewers are browsers other than the capture browser, so one capture browser
meets the "two reviewers" Givens. Fixed names and stamps: every reset is identical.
"""

import hashlib
import json
import os
import sys

SPEC = os.path.join("docs", "specs", "report.spec.html")
SENTENCE = "The export button downloads a CSV of the current table."
TOTALS = "The report page shows totals per week."
EXPORT_LINE = '<p data-acceptance-criterion data-anchor="report-export" data-story="story-export">%s</p>\n' % SENTENCE
INSERTED = '<p data-anchor="report-scope">Totals and export cover the selected week only.</p>\n'
REWRITE = ("of the current table.", "of every row in the report.")
STAMP = 1767225600 * 10**9  # 2026-01-01T00:00:00Z, reset.sh's commit date
PENDING_EDIT = ".qa-agent-edit"  # beside the spec: the agent edit that lands on the next save


def insert_above(text):
    """Agent edit: a new paragraph above the export sentence."""
    return _replace(text, EXPORT_LINE, INSERTED + EXPORT_LINE)


def rewrite(text):
    """Agent edit: part of the export sentence rewritten."""
    return _replace(text, REWRITE[0], REWRITE[1])


def delete(text):
    """Agent edit: the export sentence deleted, with its paragraph."""
    return _replace(text, EXPORT_LINE, "")


def _replace(text, old, new):
    if text.count(old) != 1:
        raise SystemExit("qa fixture: report spec no longer holds exactly one %r" % old)
    return text.replace(old, new)


class Spool:
    """Human events for one spec, named and stamped in order."""

    def __init__(self, version):
        self.version, self.files = version, []

    def add(self, event, ident, browser, author, **fields):
        index = len(self.files) + 1
        body = {
            "id": ident, "event": event, "actor": "human",
            "createdAt": "2026-01-01T00:00:%02dZ" % index, "schemaVersion": 1,
            "browser": browser, "author": author, "version": self.version,
            "anchorId": "", "target": None, "text": "", **fields,
        }
        self.files.append(("%d-%s-%s.json" % (STAMP + index * 10**9, event, ident), body))
        return ident


def comment(spool, ident, browser, author, anchor, quote, text, target=None):
    return spool.add("comment", ident, browser, author, anchorId=anchor, target=target, quote=quote, text=text)


def sentence_comment(spool, ident="qa-sentence"):
    """Papaya's comment on the selected export sentence (a text target)."""
    return comment(spool, ident, "qa-browser-papaya", "Papaya", "report-export", SENTENCE,
                   "Say which columns the CSV holds.", {"type": "text", "key": SENTENCE})


def pending_thread(spool):
    """Papaya's comment, handed off: one pending human message."""
    root = comment(spool, "qa-pending", "qa-browser-papaya", "Papaya", "report-totals", TOTALS, "Weeks start on Monday?")
    spool.add("handoff", "qa-pending-handoff", "qa-browser-papaya", "Papaya", events=[root])
    return root


def edit_race(spool):
    root = pending_thread(spool)
    # the other reviewer's racing edit; the capture browser's edit, saved now, names later
    spool.add("edit", "qa-edit-guava", "qa-browser-guava", "Guava", supersedes=root, threadId=root,
              anchorId="report-totals", quote=TOTALS, text="Do weeks start on Monday or Sunday?")
    return None


def name_collision(spool):
    comment(spool, "qa-mango-first", "qa-browser-mango-1", "Mango", "report-totals", TOTALS, "Totals per ISO week?")
    comment(spool, "qa-mango-second", "qa-browser-mango-2", "Mango", "report-export", SENTENCE, "Which delimiter?")
    return None


def agent_scan(spool):
    root = sentence_comment(spool)
    spool.add("handoff", "qa-sentence-handoff", "qa-browser-papaya", "Papaya", events=[root])
    return rewrite


def on_sentence(edit):
    """Seed: the sentence comment, then the agent's edit."""
    def seed(spool):
        sentence_comment(spool)
        return edit
    return seed


def no_events(spool):
    return None


# collection -> seeds the spool, returns the agent edit applied now (or None)
COLLECTIONS = {
    "edit-race": edit_race,
    "name-collision": name_collision,
    "anchor-moved": on_sentence(insert_above),
    "anchor-changed": on_sentence(rewrite),
    "anchor-gone": on_sentence(delete),
    "agent-scan": agent_scan,
    "stale-page": no_events,
}
# collection -> agent edit that lands when the page next saves (land_pending_edit)
PENDING = {"stale-page": insert_above}


def build(site):
    for collection, seed in COLLECTIONS.items():
        spec = os.path.join(site, collection, SPEC)
        with open(spec, "rb") as stream:
            raw = stream.read()
        version = hashlib.sha256(raw).hexdigest()
        spool = Spool(version)
        edit = seed(spool)
        review = spec + ".review"
        if spool.files:
            os.makedirs(os.path.join(review, "human"))
            os.makedirs(os.path.join(review, "versions"))
            with open(os.path.join(review, "versions", version + ".html"), "wb") as stream:
                stream.write(raw)
            for name, body in spool.files:
                with open(os.path.join(review, "human", name), "w", encoding="utf-8") as stream:
                    json.dump(body, stream)
        text = raw.decode("utf-8")
        if edit:
            _write(spec, edit(text))
        if collection in PENDING:
            _write(spec + PENDING_EDIT, PENDING[collection](text))


def land_pending_edit(spec):
    """The agent's edit lands between page load and save (serve.py, on each event post)."""
    try:
        os.replace(spec + PENDING_EDIT, spec)
    except FileNotFoundError:
        pass


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as stream:
        stream.write(text)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: spools.py <site>")
    build(sys.argv[1])
