#!/usr/bin/env python3
"""QA review-state collections (qa.toml nav acceptance-*, review-state.spec.html#acceptance).

usage: spools.py <site>   (after reset.sh copies the head repo to <site>/<collection>)

Each collection below gets seeded spool events on its report spec (a lane's on the page LANES names, SPEC_OF), as
the page and the
agent would have left them (human/ and agent/) (skill/review-spec/references/event-schema.md), and, where a
criterion needs it, the agent's edit to the spec or its answer to a saved comment. Seeded
human events name the head spec text as their `version` and the version file holds it, as
the service writes on serve. A mockup collection (SET_COLLECTIONS, mockups.spec.html) registers Report page alone,
whose mockup set is mockups/report-page/: its seeds write on the spec's spool and on its Report page mockup's, beside
the mockup, of the same shape (mockups #review-spool), and its agent edit waits beside the mockup (WATCHED).
Seeded reviewers are browsers other than the capture browser. Fixed names and stamps:
every reset is identical. LANES is the one table of the review index's lanes: reset.sh copies and registers each lane
from it.
"""

import functools
import hashlib
import importlib.util
import json
import os
import string
import subprocess
import sys
import time

SPEC = os.path.join("docs", "specs", "report.spec.html")
LATER = os.path.join("docs", "specs", "later-only.spec.html")  # Report filters: its only TBD is later, no open decision
SENTENCE = "The export button downloads a CSV of the current table."
KEY = SENTENCE[:40]  # the page's text target key: the selection's first 40 characters
EXPORT_LINE = '<p data-acceptance-criterion data-anchor="report-export" data-story="story-export">%s</p>\n' % SENTENCE
INSERTED = '<p data-anchor="report-scope">Totals and export cover the selected week only.</p>\n'
REWRITE = ("of the current table.", "of every row in the report.")
STAMP = 1767225600 * 10**9  # 2026-01-01T00:00:00Z, reset.sh's commit date
PENDING_EDIT = ".qa-agent-edit"  # beside the spec: the agent edit that lands on the next save
WATCHED_EDIT = ".qa-agent-edit-watched"  # beside a page: the agent edit that lands once the open page checks its file
# Report page: its mockup set, mockups/report-page/, holds 01 Report page, changed since the base, 02 Team page,
# unchanged, and 03 Export dialog, new since the base; a mockup collection registers it alone.
REPORT_PAGE = os.path.join("docs", "specs", "report-page.spec.html")
REPORT_MOCKUP = os.path.join("docs", "specs", "mockups", "report-page", "01-report-page.mockup.html")
EXPORT_FOOTER = '  <footer class="actions" data-anchor="desktop-actions">\n'  # holds the desktop frame's Export CSV button
# The agent's edit (WATCHED): a rule over the actions, as the bar's under the header; no words (mockups #fidelity-labels).
EXPORT_RULE = ('  <hr style="border: 0; border-top: 1px solid var(--ui-border); margin: var(--ui-space-5) 0 0"'
               ' data-anchor="desktop-actions-rule">\n')
# Walkthrough collections (WALKTHROUGHS, mockups #walkthrough, #step-threads, #mockup-questions): beside Report page's
# set, screens whose walkthrough the template draws from data-at, written at reset with the collection's head commit as
# their fidelity note's, so new since the base: Team panel walks three steps, Export flow all six.
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "walkthrough.mockup.template.html")
SET_FOLDER = os.path.join("docs", "specs", "mockups", "report-page")
TEAM_PANEL = os.path.join(SET_FOLDER, "04-team-panel.mockup.html")
EXPORT_FLOW = os.path.join(SET_FOLDER, "05-export-flow.mockup.html")
# (name, the parts whose press reaches it, the spec anchors it illustrates, its caption)
STEPS = (
    ("totals", "", "", "This week's total for each team"),
    ("team-open", "desktop-open phone-open", "page-team", "Payments opens its last three weeks"),
    ("export", "desktop-export phone-export", "page-export", "The export dialog lists the rows the CSV holds"),
    ("file-named", "desktop-file phone-file", "", "The file is named for the week"),
    ("downloading", "desktop-download phone-download", "", "The CSV downloads"),
    ("downloaded", "", "page-export-file", "weekly-totals-2026-W01.csv is downloaded"),
)
QUESTION_EDGE = ('<p data-anchor="question-edge" data-spec-tbd data-about="desktop-card-payments phone-card-payments" '
                 'data-step="team-open">Should Payments\' card keep its blue edge while its panel is open?\n'
                 '<button type="button" data-spec-choice="Keep the blue edge">Keep the blue edge (recommended)</button>\n'
                 '<button type="button" data-spec-choice="Plain card">Plain card</button></p>')
QUESTION_FILE = ('<p data-anchor="question-file" data-spec-tbd data-about="desktop-file phone-file" data-step="export">'
                 'Name the file after the week?\n'
                 '<button type="button" data-spec-choice="After the week">After the week (recommended)</button>\n'
                 '<button type="button" data-spec-choice="After the report">After the report</button></p>')
TEAM = (TEAM_PANEL, "Team panel", 3)
# collection -> each walkthrough screen it adds: (page, screen, steps, its questions)
WALKTHROUGHS = {
    "mockup-steps": (TEAM + ((),), (EXPORT_FLOW, "Export flow", 6, ())),
    "mockup-step-thread": (TEAM + ((),),),
    "mockup-step-pin": (TEAM + ((),),),
    "shell-mockup-step": (TEAM + ((),),),
    "mockup-questions": (TEAM + ((QUESTION_EDGE, QUESTION_FILE),),),
    **{collection: (TEAM + ((QUESTION_EDGE,),),) for collection in (
        "mockup-question-answer", "mockup-question-words", "mockup-question-phone", "mockup-question-accept")},
}
# Prototype collections (PROTOTYPES, mockups #prototype, #round-video): beside Report page's set, Weekly report, a
# prototype mockup of the fixture's running prototype (prototype.py, qa.toml services.prototype), committed in the
# collection's first round, then a second round's commit; its spool holds both rounds' videos, the second written last,
# each with the walkthrough's captions (videos/). Its row names the prototype's address (reset.sh), but prototype-down's
# a closed port, whose mockup holds Mango's thread on the team panel, as screens-index's does (mockups #screen-index);
# in prototype-updated the agent commits to the lane branch once the open page first checks its file (WATCHED_COMMIT,
# serve.py). They are served on their own registry, a service private on loopback (reset.sh, qa.toml review-prototype),
# so each lane's prototype origin is <slug>-prototype.localhost at its port (review-service #prototype-proxy).
# prototype-own-chrome's lane runs its own review service as its prototype, the one serving it (OWN_CHROME): its set also
# holds Report spec, a prototype mockup framing the lane's own Report spec page, whose region is its totals criterion
# (mockups #prototype-drawn, #acceptance-prototype-own-chrome).
PROTOTYPE_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prototype.mockup.html")
PROTOTYPE_MOCKUP = os.path.join(SET_FOLDER, "06-weekly-report.mockup.html")
VIDEOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "videos")
# Directions collections (mockups #directions): a screen's first round showing two directions, Team cards, the agent's
# pick, and Team table (DIRECTIONS_LIST). In the prototype ones (PROTOTYPE_DIRECTIONS, PROTOTYPES too) Weekly report, the
# prototype mockup, names them with no open question, Team table the prototype's reports/weekly-table route, and its
# spool holds no round video, as mockups 05 draws none; in directions-drawn (DRAWN_DIRECTIONS) Team panel, a drawn
# screen, draws Team table's own two frames of the fixture's data beside its own, and the spec's one thread is resolved,
# so Accept spec is offered with no Pick pressed. In directions-kept (KEPT_DIRECTIONS, PROTOTYPES too) the reviewer
# pressed Pick on Team table in the first round and handed off, and the agent answered with the round after (mockups
# #directions-next, prompt-first-shaping #acceptance-directions-kept): Weekly report is Team table's route alone, its
# region the table, with no directions list, its spool the answered Pick and no round video.
PROTOTYPE_DIRECTIONS = ("directions", "directions-pick", "directions-pick-panel")
DRAWN_DIRECTIONS = ("directions-drawn",)
KEPT_DIRECTIONS = ("directions-kept",)
TABLE_ROUTE = "/reports/weekly-table"
KEPT_ROUND = "round 2: Team table, the reviewer's pick"  # directions-kept's second commit, in place of ROUNDS[1]
TABLE_REGIONS = ('<ul data-mockup-regions>\n'
                 '<li data-anchor="team-table" data-select=".teams" data-illustrates="page-teams">Team table</li>\n'
                 '</ul>\n')
DIRECTIONS_LIST = ('<ul data-mockup-directions data-anchor="directions">\n'
                   '<li data-direction="cards" data-gives-up="a team\'s earlier weeks take a press to see." '
                   'data-pick="Every team\'s total reads at a glance on a phone, and a press opens its weeks.">Team cards</li>\n'
                   '<li data-direction="table"%s data-gives-up="on a phone the three weeks squeeze into narrow columns.">Team table</li>\n'
                   '</ul>')
WEEKLY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "base", "fixtures", "weekly-totals.json")
PROTOTYPES = ("prototype-frames", "prototype-comment", "prototype-press", "prototype-down", "prototype-updated",
              "prototype-video-comment", "screens-index", "prototype-own-chrome") + PROTOTYPE_DIRECTIONS + KEPT_DIRECTIONS
OWN_CHROME = ("prototype-own-chrome",)
OWN_CHROME_MOCKUP = os.path.join(SET_FOLDER, "07-report-spec.mockup.html")
OWN_CHROME_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="mockup-prototype" content="/%s/docs/specs/report.spec.html">
<title>Report spec - mockup</title>
<script defer src="../../.viz/runtime.js"></script>
</head>
<body>
<ul data-mockup-regions>
<li data-anchor="totals-criterion" data-select="[data-anchor=report-totals]" data-illustrates="page-teams">Totals criterion</li>
</ul>
</body>
</html>
"""
ROUNDS = ("round 1: the weekly report", "round 2: the team panel fades in")  # each round's commit, oldest first
WATCHED_COMMIT = ".qa-agent-commit-watched"  # beside a page: the agent commits once the open page checks its file
# One-screen collections (ONE_SCREEN, mockups #acceptance-next-screen-one): Report page's set holds 01 Report page alone,
# the other screens gone from the working tree, so its mockup page shows no Previous, no Next, and no place.
ONE_SCREEN = ("next-screen-one",)
ONE_SCREEN_GONE = (os.path.join(SET_FOLDER, "02-team-page.mockup.html"), os.path.join(SET_FOLDER, "03-export-dialog.mockup.html"))
# Video collections (threads #video-moment): Report walkthrough shows a short video of the report page, the first
# round's desktop recording (videos/), which each video collection holds at docs/walkthrough.webm (write_video), as its
# host page does (panes.py VIDEO).
REPORT_VIDEO = os.path.join("docs", "specs", "report-video.spec.html")
VIDEO_FILE = os.path.join("docs", "walkthrough.webm")
ANSWERS = ".qa-agent-answers"  # beside the spec: the agent answers each comment the page saves
EMIT_REPLY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "skill", "review-spec", "scripts", "emit-reply.sh")
SERVICE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "skill", "review-spec", "assets", "review-serve.py")
FOUR = ("onboarding", "report", "later-only", "one-open")  # the specs a collection registers unless LANES says
# Convention collections (project-rules #acceptance-convention-no-candidate): Onboarding gains, in the working tree, an
# unmarked criterion stating a design-system convention, its anchor, story, and text here, which the fixture's provider
# reads as this feature and as verifying its story (serve.py), so its read lists no candidate and no coverage gap.
CONVENTION_COLLECTIONS = ("convention-no-candidate",)
ONBOARDING_SPEC = os.path.join("docs", "specs", "onboarding.spec.html")
CONVENTION = ("rule-loading", "story-learn", "Every loading state uses the design system's shared skeleton.")
# Held collections (project-rules #acceptance-pending, #acceptance-pending-motion, #acceptance-pending-answer): Weekly
# report gains, in the working tree, a behavior sentence, its anchor and text here, each its own, so each rule check its
# own key; the fixture's general LLM holds the rule check of a spec text holding it for the seconds here from its
# first ask, None until the service stops, then answers it as any report spec's, missed; a stop ends a hold with no
# record (serve.py). rule-pending's rule check so stays pending while the page is open, and rule-answer's is answered
# while it is. An answer is a record the QA state keeps across resets, so an answered collection's sentence names the
# reset's project (reset.sh), its rule check each reset's own key: no earlier reset's answer matches it.
HELD = {"rule-pending": ("export-wait", "The export button waits until the totals have loaded.", None),
        "rule-answer": ("export-ready", "The export button shows once the totals have loaded.", 5)}


@functools.cache
def _service():
    spec = importlib.util.spec_from_file_location("qa_spools_review_serve", SERVICE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def served_version(page):
    """The version `page` is served at, the one a hand-off from its page names: the service's own reading of it
    (review-serve.py _read_page), so a seed never names a version the service does not serve (review-state
    #model-versions)."""
    return _service()._read_page(page)[1]


def insert_above(text):
    """Agent edit: a new paragraph above the export sentence."""
    return _replace(text, EXPORT_LINE, INSERTED + EXPORT_LINE)


def rewrite(text):
    """Agent edit: part of the export sentence rewritten."""
    return _replace(text, REWRITE[0], REWRITE[1])


def delete(text):
    """Agent edit: the export sentence deleted, with its paragraph."""
    return _replace(text, EXPORT_LINE, "")


def rule_above_export(text):
    """Agent edit to the Report page mockup: a new element, a rule, above the desktop frame's Export CSV button."""
    return _replace(text, EXPORT_FOOTER, EXPORT_RULE + EXPORT_FOOTER)


def one_question(text):
    """Agent edit: the file name TBD made later, so the export format is the page's one open question."""
    return _replace(text, '<p data-anchor="export-name" data-spec-tbd>', '<p data-anchor="export-name" data-spec-tbd="later">')


FILLER = "".join('<p data-anchor="filler-%d">Filler paragraph %d, so the criteria below sit far below the top of the page.</p>\n'
                 % (n, n) for n in range(1, 41))


def tall_above(text):
    """Agent edit: forty paragraphs under the header's context, so Report filters' criterion sits far below the top."""
    line = '<p data-anchor="context">QA fixture: a spec whose only TBD is for a later slice, with no comment thread.</p>\n'
    return _replace(text, line, line + FILLER)


def later_tbds(spool):
    """Advice: both open TBDs made later, so Accept spec shows beside the missed note."""
    return lambda text: _replace(text, "data-spec-tbd>", 'data-spec-tbd="later">', count=2)


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise SystemExit("qa fixture: page no longer holds exactly %d %r" % (count, old))
    return text.replace(old, new)


class Spool:
    """Human and agent events for one spec, named and stamped in order."""

    def __init__(self, version, page=None):
        self.version, self.page, self.files = version, page, []

    def _append(self, body):
        index = len(self.files) + 1
        body = {"createdAt": "2026-01-01T00:00:%02dZ" % index, "schemaVersion": 1, **body}
        self.files.append(("%d-%s-%s.json" % (STAMP + index * 10**9, body["event"], body["id"]), body))
        return body["id"]

    def add(self, event, ident, browser, author, **fields):
        return self._append({
            "id": ident, "event": event, "actor": "human", "browser": browser, "author": author,
            "version": self.version, "anchorId": "", "target": None, "text": "", **fields,
        })

    def agent_reply(self, ident, responds_to, anchor, text, **fields):
        """The agent's answer, as emit-reply.sh writes it: the thread is replied (acknowledged)."""
        return self._append({
            "id": ident, "event": "reply", "actor": "agent", "respondsTo": responds_to, "anchorId": anchor,
            "target": None, "text": text, "status": "acknowledged", "change": "no spec change", **fields,
        })


def comment(spool, ident, browser, author, anchor, quote, text, target=None, **fields):
    return spool.add("comment", ident, browser, author, anchorId=anchor, target=target, quote=quote, text=text, **fields)


def sentence_comment(spool, ident="qa-sentence"):
    """Papaya's comment on the selected export sentence (a text target)."""
    return comment(spool, ident, "qa-browser-papaya", "Papaya", "report-export", SENTENCE,
                   "Say which columns the CSV holds.", {"type": "text", "key": KEY})


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


def uncommitted(spool):
    """No events; the agent's edit left in the worktree, never committed, so the served spec differs from its head."""
    return insert_above


def replied(spool, ident, anchor, text, browser="qa-browser-papaya", author="Papaya"):
    """A handed-off comment the agent answered: open, awaiting the human's resolve."""
    root = comment(spool, ident, browser, author, anchor, None, text)
    spool.add("handoff", ident + "-handoff", browser, author, events=[root])
    spool.agent_reply(ident + "-answer", root, anchor, "Done; OK to resolve?")
    return root


def every_status(spool):
    """Index lane: one thread per status, draft, handed off, replied, and resolved (3 Open, 1 Resolved)."""
    comment(spool, "qa-draft", "qa-browser-mango", "Mango", "report-totals", None, "Round totals to whole units?")
    handed = comment(spool, "qa-handed", "qa-browser-papaya", "Papaya", "export-format", None, "Is the CSV UTF-8?")
    spool.add("handoff", "qa-handed-handoff", "qa-browser-papaya", "Papaya", events=[handed])
    replied(spool, "qa-replied", "export-name", "Date the file name.")
    done = replied(spool, "qa-resolved", "export-schedule", "Weekly is enough.")
    spool.add("status", "qa-resolved-status", "qa-browser-papaya", "Papaya", respondsTo=done, threadId=done,
              status="resolved")
    return None


def one_open(spool):
    """Exactly one open thread, replied by the agent: an index lane's for the reviewer to resolve, and a served page's."""
    replied(spool, "qa-open", "report-export", "Say which columns the CSV holds.")
    return None


# Index gate lanes (review-index #index-gates): one thread on Report filters, in the state its gate reads.
def with_agent(spool):
    """A handed-off comment the agent has not answered."""
    root = comment(spool, "qa-handed", "qa-browser-papaya", "Papaya", "filter-team", None, "Which teams are listed?")
    spool.add("handoff", "qa-handed-handoff", "qa-browser-papaya", "Papaya", events=[root])
    return None


def agent_replied(spool):
    """A handed-off comment the agent answered, not yet resolved."""
    replied(spool, "qa-replied", "filter-team", "Which teams are listed?")
    return None


def resolved(spool, ident, anchor, text):
    """A thread handed off, answered, then resolved by its reviewer."""
    done = replied(spool, ident, anchor, text)
    spool.add("status", ident + "-status", "qa-browser-papaya", "Papaya", respondsTo=done, threadId=done, status="resolved")
    return done


def all_resolved(spool):
    """Every thread resolved: one, handed off, answered, then resolved."""
    resolved(spool, "qa-resolved", "filter-team", "Which teams are listed?")
    return None


def unsent(spool):
    """A saved comment never handed off: a draft."""
    comment(spool, "qa-draft", "qa-browser-mango", "Mango", "filter-team", None, "Filter by region too?")
    return None


# Mockup collections (mockups.spec.html#acceptance): threads on Report page's spec and on its Report page mockup, each
# on an anchored part (a card, the Export CSV button) as the page saves a click on it.
def draft_on_export(spool):
    """Papaya's comment on the desktop Export CSV button, saved, not handed off: the capture browser's own draft, as its
    start point keeps Papaya's browser identity (qa.toml nav acceptance-handoff)."""
    comment(spool, "qa-export", "qa-browser-papaya", "Papaya", "desktop-export", None, "Name the file after the week?")
    return None


def handed_on_export(spool):
    """A comment on the desktop Export CSV button, handed off, not answered: an open thread."""
    root = comment(spool, "qa-export", "qa-browser-papaya", "Papaya", "desktop-export", None, "Name the file after the week?")
    spool.add("handoff", "qa-export-handoff", "qa-browser-papaya", "Papaya", events=[root])
    return None


def handed_on_card(spool):
    """A comment on the desktop Search card, handed off, not answered: an open thread."""
    root = comment(spool, "qa-card", "qa-browser-papaya", "Papaya", "desktop-card-search", None, "Show last week's total too?")
    spool.add("handoff", "qa-card-handoff", "qa-browser-papaya", "Papaya", events=[root])
    return None


def replied_on_card(spool):
    """A comment on the desktop Payments card, handed off and answered by the owner."""
    replied(spool, "qa-card", "desktop-card-payments", "Show last week's total too?")
    return None


def resolved_on_card(spool):
    resolved(spool, "qa-card", "desktop-card-search", "Show last week's total too?")
    return None


def resolved_on_spec(spool):
    resolved(spool, "qa-teams", "page-teams", "Order cards by total?")
    return None


def handed_at_step(spool):
    """Mango's comment on the desktop Payments card at the second step, Team open, handed off: an open thread there
    (mockups #step-threads)."""
    root = spool.add("comment", "qa-step", "qa-browser-mango", "Mango", anchorId="desktop-card-payments", target=None,
                     quote=None, text="Show the week number on the card?", step="team-open")
    spool.add("handoff", "qa-step-handoff", "qa-browser-mango", "Mango", events=[root])
    return None


def thread_on_panel(spool):
    """Mango's comment on the Weekly report prototype's team panel at its second step, handed off; a prototype's comment
    carries no version (mockups #prototype-marks)."""
    root = spool.add("comment", "qa-panel", "qa-browser-mango", "Mango", anchorId="team-panel", target=None,
                     quote=None, text="Show the change from last week?", step="team-open")
    spool.files[-1][1].pop("version")
    spool.add("handoff", "qa-panel-handoff", "qa-browser-mango", "Mango", events=[root])
    return None


def picked_table(spool):
    """Mango's press of Pick on Team table in the first round, the answer on the directions list as runtime-directions.mjs
    saves it, handed off, and the agent's answer (KEPT_DIRECTIONS); a prototype's comment carries no version."""
    root = spool.add("comment", "qa-pick", "qa-browser-mango", "Mango", anchorId="directions",
                     target={"type": "element", "key": "li[2]"}, quote="Team table", text="Team table")
    spool.files[-1][1].pop("version")
    spool.add("handoff", "qa-pick-handoff", "qa-browser-mango", "Mango", events=[root])
    spool.agent_reply("qa-pick-answer", root, "directions", "Team table it is: the weekly report is now the table alone.")
    return None


def two_resolved_on_spec(spool):
    resolved(spool, "qa-teams", "page-teams", "Order cards by total?")
    resolved(spool, "qa-export", "page-export", "CSV only?")
    return None


def accepted_before_export(spool):
    """Accept spec pressed on Report page while its set held 01 Report page and 02 Team page: the hand-off names each
    at the version its page showed (mockups #accept-set). 03 Export dialog joined the set after it, so the hand-off does
    not name it (review-index #index-gate-sent)."""
    folder = os.path.join(os.path.dirname(spool.page), "mockups", "report-page")
    named = ("01-report-page.mockup.html", "02-team-page.mockup.html")
    spool.add("handoff", "qa-accept", "qa-browser-papaya", "Papaya", version=served_version(spool.page),
              mockups=[{"path": "mockups/report-page/" + name, "version": served_version(os.path.join(folder, name))}
                       for name in named])
    return None


# Threads collections (threads.spec.html#acceptance, spec-page-design #acceptance-color-consistency): Report filters'
# threads, read as one conversation. Papaya is the capture browser where a criterion's reviewer owns a thread (qa.toml
# nav storage); Mango is another reviewer. Each seed's comments are written at their own times, in the order given.
ME = ("qa-browser-papaya", "Papaya")
OTHER = ("qa-browser-mango", "Mango")


def sent(spool, ident, who, anchor, text, **fields):
    """A comment handed off."""
    root = comment(spool, ident, *who, anchor, None, text, **fields)
    spool.add("handoff", ident + "-handoff", *who, events=[root])
    return root


def three_times(spool):
    """Three threads begun at different times on different blocks: the last block first, the first block second."""
    sent(spool, "qa-first", OTHER, "filter-saved", "Is saving filters in this slice?")
    sent(spool, "qa-second", ME, "context", "Say which week the report shows.")
    sent(spool, "qa-third", OTHER, "filter-team", "Name the teams the filter lists.")
    return None


def answered_mine(spool):
    """The capture browser's comment, handed off, and the agent's reply."""
    root = sent(spool, "qa-mine", ME, "filter-team", "List the teams alphabetically?")
    spool.agent_reply("qa-mine-answer", root, "filter-team", "Yes: the filter lists teams A to Z.")
    return None


def three_replies_by(long, short):
    """Two threads: long's with three replies, the agent's, long's, and the agent's; then short's with none."""
    def seed(spool):
        root = sent(spool, "qa-long", long, "filter-team", "Does the filter remember the last team?")
        spool.agent_reply("qa-long-answer", root, "filter-team", "Not yet; it resets on each visit.")
        reply = spool.add("reply", "qa-long-again", *long, anchorId="filter-team", respondsTo="qa-long-answer", threadId=root,
                          text="Please keep it between visits.")
        spool.add("handoff", "qa-long-again-handoff", *long, events=[reply])
        spool.agent_reply("qa-long-done", reply, "filter-team", "Done: the filter keeps the last team.")
        sent(spool, "qa-short", short, "context", "Say which week the report shows.")
        return None
    return seed


three_replies = three_replies_by(OTHER, ME)  # replies-folded, replies-open: the long thread is another reviewer's
own_three_replies = three_replies_by(ME, OTHER)  # focus, focus-phone: the long thread is the capture browser's own


def one_on_block(spool):
    """One thread on the filter criterion, answered by the agent."""
    replied(spool, "qa-pin", "filter-team", "Name the teams the filter lists.", *OTHER)
    return None


def far_below(spool):
    """One thread on the filter criterion, which the agent's edit puts far below the top of the page."""
    sent(spool, "qa-far", OTHER, "filter-team", "Name the teams the filter lists.")
    return tall_above


def own_draft(spool):
    """The capture browser's comment, saved, not handed off."""
    comment(spool, "qa-draft", *ME, "filter-team", None, "List the teams alphabetically?")
    return None


def seen(spool):
    """A handed-off comment the agent acknowledged with no reply: a status event, no message."""
    root = sent(spool, "qa-seen", OTHER, "filter-team", "Name the teams the filter lists.")
    spool._append({"id": "qa-seen-ack", "event": "status", "actor": "agent", "respondsTo": root, "threadId": root,
                   "status": "acknowledged"})
    return None


def resolved_three(spool):
    """A thread of three messages, Mango's, the agent's, and Mango's, then resolved."""
    root = sent(spool, "qa-done", OTHER, "filter-team", "Name the teams the filter lists.")
    spool.agent_reply("qa-done-answer", root, "filter-team", "Done: the filter lists every team by name.")
    reply = spool.add("reply", "qa-done-thanks", *OTHER, anchorId="filter-team", respondsTo="qa-done-answer", threadId=root,
                      text="Thanks, that reads well.")
    spool.add("handoff", "qa-done-thanks-handoff", *OTHER, events=[reply])
    spool.add("status", "qa-done-status", *OTHER, respondsTo=root, threadId=root, status="resolved")
    return None


def waiting(spool):
    """Two threads: Mango's on the filter criterion, ending with the agent's reply; Papaya's on the context, ending with
    Papaya's own reply to the agent."""
    replied(spool, "qa-waits", "filter-team", "Name the teams the filter lists.", *OTHER)
    root = replied(spool, "qa-answered", "context", "Say which week the report shows.")
    reply = spool.add("reply", "qa-answered-again", *ME, anchorId="context", respondsTo="qa-answered-answer", threadId=root,
                      text="And the year too, please.")
    spool.add("handoff", "qa-answered-again-handoff", *ME, events=[reply])
    return None


def waiting_staged(spool):
    """The waiting Givens on a lane at Spec review (STAGES), each message stamped with it as written there: the capture
    browser's thread on the filter criterion, its own reply folded among the agent's two, ending with the agent's; then
    its thread on the context, ending with its own reply to the agent."""
    stage = {"stage": "Spec review"}
    root = sent(spool, "qa-waits", ME, "filter-team", "Name the teams the filter lists.", **stage)
    spool.agent_reply("qa-waits-answer", root, "filter-team", "The filter lists every team by name.", **stage)
    reply = spool.add("reply", "qa-waits-again", *ME, anchorId="filter-team", respondsTo="qa-waits-answer", threadId=root,
                      text="Sort them A to Z, please.", **stage)
    spool.add("handoff", "qa-waits-again-handoff", *ME, events=[reply])
    spool.agent_reply("qa-waits-done", reply, "filter-team", "Done; OK to resolve?", **stage)
    root = sent(spool, "qa-answered", ME, "context", "Say which week the report shows.", **stage)
    spool.agent_reply("qa-answered-answer", root, "context", "Done; OK to resolve?", **stage)
    reply = spool.add("reply", "qa-answered-again", *ME, anchorId="context", respondsTo="qa-answered-answer", threadId=root,
                      text="And the year too, please.", **stage)
    spool.add("handoff", "qa-answered-again-handoff", *ME, events=[reply])
    return None


def thread_and_question(spool):
    """One thread on the totals criterion, and the export format as the page's one open question."""
    sent(spool, "qa-totals", OTHER, "report-totals", "Round totals to whole units?")
    return one_question


def question_only(spool):
    """No thread; the export format as the page's one open question."""
    return one_question


# A box on the walkthrough's frame, as fractions of it: the Payments card as the video shows the team panel.
VIDEO_BOX = {"left": 0.05, "top": 0.3, "width": 0.3, "height": 0.45}


def boxed_moment(spool):
    """Mango's comment on a box on the walkthrough video's frame at 1.8 seconds, handed off."""
    root = spool.add("comment", "qa-moment", *OTHER, anchorId="walkthrough-video", target={"type": "element", "key": "video[1]"},
                     quote=None, text="The team panel should fade in faster.", moment=1.8, box=VIDEO_BOX)
    spool.add("handoff", "qa-moment-handoff", *OTHER, events=[root])
    return None


def three_statuses(spool):
    """One draft, one acknowledged, and one resolved thread, and no other status."""
    comment(spool, "qa-draft", *OTHER, "context", None, "Say which week the report shows.")
    replied(spool, "qa-replied", "filter-team", "Name the teams the filter lists.", *OTHER)
    resolved(spool, "qa-resolved", "filter-saved", "Is saving filters in this slice?")
    return None


# Shell collections (shell #acceptance-shell-*): the test shell (shell.py) holds one collection's page, and the
# comment UI page shows its conversation. Mango's thread is another reviewer's. Each registers its one spec; not
# shell-page-tools, which registers the four, so its report spec shows the onboarding rule's Jev mark.
def shell_thread(spool):
    """One thread on the totals criterion, Mango's, handed off."""
    sent(spool, "qa-shell", OTHER, "report-totals", "Round totals to whole units?")
    return None


def resolved_and_question(spool):
    """Every thread resolved, and the export format as the page's one open question."""
    resolved(spool, "qa-resolved", "report-totals", "Round totals to whole units?")
    return one_question


def shell_draft(spool):
    """The capture browser's one comment on the totals criterion, saved in the comment UI, not handed off: Papaya's, the
    browser id its start point keeps in the test shell's storage (qa.toml acceptance-shell-handoff)."""
    comment(spool, "qa-shell-draft", *ME, "report-totals", None, "Round totals to whole units?")
    return None


# collection -> (its seed, the spec it seeds)
SHELL = {
    **{collection: (shell_thread, SPEC) for collection in ("shell-held", "shell-alone", "shell-unnamed", "shell-close")},
    **{collection: (no_events, SPEC) for collection in ("shell-pick", "shell-pick-key", "shell-wake")},
    "shell-handoff": (shell_draft, SPEC),
    "shell-publish": (question_only, SPEC), "shell-answer": (question_only, SPEC),
    "shell-phone": (no_events, SPEC), "shell-phone-id": (no_events, SPEC),
    "shell-show": (far_below, LATER), "shell-pin-focus": (one_on_block, LATER),
    "shell-accept": (all_resolved, LATER), "shell-accept-waits": (resolved_and_question, SPEC),
}

# The stage a message was written in (threads.spec.html#stage-tag): the stage the box names for each lane, as the
# evidence provider's review facts answer it (review-index #index-gates-provider): the review-plugged service's evidence
# service declares it (reset.sh), and serve.py answers it on every other service, which has no provider. qa-17 is at
# Spec review; qa-18 now at Code review, its comment written at Spec review; qa-19 now at Merge ask, its agent's reply
# written at Code review; qa-20 registers nothing, the lane a host page's evidence item names (panes.py STAGE); qa-21 at
# Spec review, its waiting threads written there.
STAGES = {"qa-10": "Spec review", "qa-17": "Spec review", "qa-18": "Code review", "qa-19": "Merge ask", "qa-20": "QA",
          "qa-21": "Spec review"}


def stage_kept(spool):
    """Mango's comment on the filter criterion, written while the lane was at Spec review, handed off."""
    root = spool.add("comment", "qa-stage", *OTHER, anchorId="filter-team", target=None, quote=None,
                     text="Name the teams the filter lists.", stage="Spec review")
    spool.add("handoff", "qa-stage-handoff", *OTHER, events=[root])
    return None


def stage_reply(spool):
    """stage_kept, then the agent's reply, written while the lane was at Code review, as emit-reply.sh stamps it."""
    stage_kept(spool)
    spool.agent_reply("qa-stage-answer", "qa-stage", "filter-team", "Done: the filter lists every team by name.",
                      stage="Code review")
    return None


# collection -> its seed, on Report filters (later-only.spec.html) unless THREADS_ON names Weekly report
THREADS = {
    "threads-order": three_times, "threads-sides": answered_mine, "threads-replies": three_replies,
    "threads-focus": own_three_replies,
    "threads-focus-reply": answered_mine, "threads-pin": one_on_block, "threads-chip": far_below,
    "threads-sent": own_draft, "threads-new": no_events, "threads-seen": seen, "threads-resolved": resolved_three,
    "threads-waiting": waiting, "threads-question": thread_and_question, "threads-answer": question_only,
    "threads-answer-same": question_only, "threads-colors": three_statuses,
    "threads-video": no_events, "threads-video-box": no_events, "threads-video-chip": boxed_moment,
}
VIDEO = ("threads-video", "threads-video-box", "threads-video-chip")
THREADS_ON = {**{collection: SPEC for collection in ("threads-question", "threads-answer", "threads-answer-same")},
              **{collection: REPORT_VIDEO for collection in VIDEO}}

# The review index's lanes, lane-key collections, in their one table (review-index #index-gates, #index-sections,
# #index-marks, #index-entry-stale; mockups #accept-index): lane -> (the registry folder, under $QA_ROOT, of the service
# that serves it, the specs it registers, the page its COLLECTIONS seed writes on, or None). reset.sh copies and
# registers each lane from here; every one is reached from its service's review index (qa.toml).
LANES = {
    "qa-1": ("serve", FOUR, SPEC),  # every status on Weekly report
    "qa-2": ("serve", FOUR, SPEC),  # one open thread, and a spec from a removed worktree (reset.sh)
    **{lane: ("serve", ("later-only",), LATER) for lane in ("qa-3", "qa-4", "qa-5", "qa-6", "qa-7")},  # a gate each
    **{lane: ("serve-plugged", ("later-only",), None) for lane in ("qa-8", "qa-9")},  # named accepted, named a proof
    "qa-10": ("serve-plugged", ("report", "later-only", "long-title"), LATER),  # three marks, a long title
    "qa-11": ("serve-sections", ("report",), None),  # a decision open alone
    "qa-12": ("serve-sections", ("later-only",), LATER),  # with the agent alone
    **{lane: ("serve", ("report-page",), None) for lane in ("qa-13", "qa-14", "qa-15")},  # SET_COLLECTIONS seeds
    "qa-16": ("serve", ("later-only",), LATER),  # UNREADABLE
    "qa-17": ("serve", ("later-only",), None),  # at Spec review (STAGES)
    **{lane: ("serve", ("later-only",), LATER) for lane in ("qa-18", "qa-19")},  # a comment, a reply, stamped earlier
    "qa-21": ("serve", ("later-only",), LATER),  # waiting threads, stamped at Spec review (STAGES)
}
# collection -> the spec its COLLECTIONS seed writes on, when not the report spec
SPEC_OF = {lane: page for lane, (_, _, page) in LANES.items() if page}
SPEC_OF.update({collection: THREADS_ON.get(collection, LATER) for collection in THREADS})
SPEC_OF.update({collection: spec for collection, (_, spec) in SHELL.items()})

# collection -> seeds the spool, returns the agent edit applied now (or None)
COLLECTIONS = {
    "anchor-moved": on_sentence(insert_above),
    "anchor-changed": on_sentence(rewrite),
    "anchor-gone": on_sentence(delete),
    "agent-scan": agent_scan,
    "stale-page": no_events,
    "name-reply": no_events,
    "typed-kept": no_events,
    "qa-1": every_status,
    "qa-2": one_open,
    "advice": later_tbds,
    "served-threads": one_open,
    "qa-3": with_agent,
    "qa-4": all_resolved,
    "qa-5": all_resolved,
    "qa-6": agent_replied,
    "qa-7": unsent,
    "qa-10": with_agent,
    "qa-12": with_agent,
    "qa-18": stage_kept,
    "qa-19": stage_reply,
    "qa-21": waiting_staged,
    "bar-dirty": uncommitted,
    **THREADS,
    **{collection: seed for collection, (seed, _) in SHELL.items()},
}
# Collections whose seeded page's spool cannot be read (review-index #index-entry-stale): an event name in it is a link,
# which the service never follows (review-state #trust-immutable). Its target is the page itself, inside the collection.
UNREADABLE = ("qa-16",)
# Mockup collections (reset.sh mockups): collection -> {page: seed} (each seed as above, on that page's spool). The rest
# of them (mockup-view, the comment, control, and agent edit collections, and the walkthrough ones but mockup-step-pin)
# have no thread.
SET_COLLECTIONS = {
    "mockup-handoff": {REPORT_MOCKUP: draft_on_export},
    "mockup-reply": {REPORT_MOCKUP: replied_on_card},
    "mockup-mark-follows": {REPORT_MOCKUP: handed_on_export},
    "mockup-after": {REPORT_MOCKUP: handed_on_card},
    "mockup-accept-waits": {REPORT_PAGE: resolved_on_spec, REPORT_MOCKUP: handed_on_card},
    "mockup-accept-offered": {REPORT_PAGE: resolved_on_spec, REPORT_MOCKUP: resolved_on_card},
    "qa-13": {REPORT_PAGE: two_resolved_on_spec, REPORT_MOCKUP: handed_on_card},
    "qa-14": {REPORT_MOCKUP: replied_on_card},
    "qa-15": {REPORT_PAGE: accepted_before_export},
    "mockup-step-pin": {TEAM_PANEL: handed_at_step},
    "mockup-question-accept": {REPORT_PAGE: resolved_on_spec},
    "shell-mockup-step": {TEAM_PANEL: handed_at_step},
    "prototype-down": {PROTOTYPE_MOCKUP: thread_on_panel},
    "screens-index": {PROTOTYPE_MOCKUP: thread_on_panel},
    "directions-drawn": {REPORT_PAGE: resolved_on_spec},
    "directions-kept": {PROTOTYPE_MOCKUP: picked_table},
}
# collection -> agent edit that lands when the page next saves (land_pending_edit)
PENDING = {"stale-page": insert_above}
# collection -> (page, agent edit that lands once the open page first checks its file: land_watched_edit)
WATCHED = {collection: (REPORT_MOCKUP, rule_above_export) for collection in ("mockup-agent-edit", "mockup-mark-follows")}
# collections whose agent answers each saved comment (answer_comment), so the page offers a reply
ANSWERING = {"name-reply"}


def seed_page(page, seed):
    """Seed one page's spool, beside it, and apply the agent edit the seed returns; returns the page's text as seeded."""
    with open(page, "rb") as stream:
        raw = stream.read()
    version = hashlib.sha256(raw).hexdigest()
    spool = Spool(version, page)
    edit = seed(spool)
    review = page + ".review"
    if spool.files:
        os.makedirs(os.path.join(review, "versions"))
        with open(os.path.join(review, "versions", version + ".html"), "wb") as stream:
            stream.write(raw)
        for name, body in spool.files:
            os.makedirs(os.path.join(review, body["actor"]), exist_ok=True)
            with open(os.path.join(review, body["actor"], name), "w", encoding="utf-8") as stream:
                json.dump(body, stream)
    text = raw.decode("utf-8")
    if edit:
        _write(page, edit(text))
    return text


def write_walkthrough(root, page, screen, count, questions, directions=""):
    """One walkthrough screen in the collection at `root` (WALKTHROUGHS): its first `count` steps and its questions, and
    the frames and list of its directions (DRAWN_DIRECTIONS)."""
    commit = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    steps = "\n".join("<li data-step=\"%s\"%s%s>%s</li>" % (
        name, ' data-step-by="%s"' % by if by else "", ' data-illustrates="%s"' % shows if shows else "", caption)
        for name, by, shows, caption in STEPS[:count])
    asked = "<aside data-mockup-questions>\n%s\n</aside>" % "\n".join(questions) if questions else ""
    with open(TEMPLATE, encoding="utf-8") as stream:
        text = string.Template(stream.read()).substitute(commit=commit, title=screen, steps=steps, questions=asked,
                                                         directions=directions)
    _write(os.path.join(root, page), text)


def write_prototype(root, collection):
    """Weekly report, the prototype mockup, in `collection` at `root` (PROTOTYPES): committed in the first round, then
    the second round's commit, each round's videos in its spool by its commit, the newer written last. In a directions
    collection it names its two directions in its question's place, and its spool holds no video; in directions-kept
    the second round is Team table's route alone (PROTOTYPE_DIRECTIONS, KEPT_DIRECTIONS)."""
    git = lambda *args: subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, check=True).stdout.strip()
    with open(PROTOTYPE_TEMPLATE, encoding="utf-8") as stream:
        text = stream.read()
    directions = collection in PROTOTYPE_DIRECTIONS + KEPT_DIRECTIONS
    if directions:
        text = text.replace(_between(text, "<aside data-mockup-questions>", "</aside>"),
                            DIRECTIONS_LIST % ' data-route="%s"' % TABLE_ROUTE)
    rounds = list(zip(ROUNDS, (text, text)))
    if collection in KEPT_DIRECTIONS:
        rounds[1] = (KEPT_ROUND, kept_table(text))
    commits = []
    for message, page in rounds:
        _write(os.path.join(root, PROTOTYPE_MOCKUP), page)
        _write(os.path.join(root, "prototype-round.txt"), message + "\n")
        git("add", "-A", PROTOTYPE_MOCKUP, "prototype-round.txt")
        git("commit", "-qm", message)
        commits.append(git("rev-parse", "HEAD"))
    if directions:
        return
    videos = os.path.join(root, PROTOTYPE_MOCKUP + ".review", "videos")
    os.makedirs(videos, exist_ok=True)
    for at, commit in enumerate(commits):
        for viewport in ("desktop", "phone"):
            for source, name in (("round-%s.webm" % viewport, "%s-%s.webm" % (commit, viewport)),
                                 ("round.vtt", "%s-%s.vtt" % (commit, viewport))):
                with open(os.path.join(VIDEOS, source), "rb") as stream, open(os.path.join(videos, name), "wb") as out:
                    out.write(stream.read())
                os.utime(os.path.join(videos, name), (STAMP / 10**9 + at, STAMP / 10**9 + at))


def kept_table(text):
    """The round after the reviewer's Pick of Team table (mockups #directions-next): the mockup's route is Team table's,
    its one region the table, and its walkthrough of the cards, its regions, and its directions list are gone."""
    text = _replace(text, '<meta name="mockup-prototype" content="/reports/weekly">',
                    '<meta name="mockup-prototype" content="%s">' % TABLE_ROUTE)
    text = _replace(text, _between(text, "<ul data-mockup-regions>", "</ol>\n"), TABLE_REGIONS)
    return _replace(text, DIRECTIONS_LIST % ' data-route="%s"' % TABLE_ROUTE + "\n", "")


def _between(text, start, end):
    """The part of `text` from `start` through `end`."""
    at = text.index(start)
    return text[at:text.index(end, at) + len(end)]


def table_frames():
    """Team table's own desktop and phone frames, a drawn screen's other direction naming it (mockups #directions-file):
    every team's last three weeks of the fixture's data in one table."""
    with open(WEEKLY, encoding="utf-8") as stream:
        data = json.load(stream)
    head = "".join('<th class="number">%s</th>' % week for week in data["weeks"])
    rows = "".join("<tr><td>%s</td>%s</tr>" % (team["team"], "".join(
        '<td class="number%s">%s</td>' % (" now" if at == len(team["totals"]) - 1 else "", "{:,}".format(total))
        for at, total in enumerate(team["totals"]))) for team in data["teams"])
    return "\n".join(
        '<section data-mockup-frame="%s" data-direction="table" data-anchor="table-%s" data-illustrates="page-teams">\n'
        '  <header class="bar" data-anchor="table-%s-bar">\n    <h1>Weekly report</h1>\n    <p class="muted">Week %s</p>\n  </header>\n'
        '  <table class="teams" data-anchor="table-%s-teams">\n    <thead><tr><th>Team</th>%s</tr></thead>\n    <tbody>%s</tbody>\n'
        '  </table>\n</section>' % (viewport, viewport, viewport, data["weeks"][-1], viewport, head, rows)
        for viewport in ("desktop", "phone"))


def write_video(root):
    """The walkthrough video in the collection at `root`: the first round's desktop recording."""
    with open(os.path.join(VIDEOS, "round-desktop.webm"), "rb") as stream, open(os.path.join(root, VIDEO_FILE), "wb") as out:
        out.write(stream.read())


def append_to_section(path, section, line):
    """`line` after the last block of the spec's `section` (its data-spec-section), in the working tree."""
    with open(path, encoding="utf-8", newline="") as stream:
        text = stream.read()
    end = text.index("</section>", text.index('data-spec-section="%s"' % section))
    _write(path, text[:end] + line + text[end:])


def write_convention(collection):
    """The convention criterion, unmarked, after Onboarding's last criterion (CONVENTION)."""
    anchor, story, criterion = CONVENTION
    append_to_section(os.path.join(collection, ONBOARDING_SPEC), "acceptance",
                      '<p data-acceptance-criterion data-anchor="%s" data-story="%s">%s</p>\n' % (anchor, story, criterion))


def write_held(site, collection, reset):
    """The collection's held sentence after Weekly report's last behavior block (HELD), naming the reset once answered."""
    anchor, sentence, seconds = HELD[collection]
    text = sentence if seconds is None else "%s (reset %s)" % (sentence, reset)
    append_to_section(os.path.join(site, collection, SPEC), "behavior", '<p data-anchor="%s">%s</p>\n' % (anchor, text))


def build(site, reset):
    for collection in CONVENTION_COLLECTIONS:
        write_convention(os.path.join(site, collection))
    for collection in HELD:
        write_held(site, collection, reset)
    for collection in VIDEO:
        write_video(os.path.join(site, collection))
    for collection, screens in WALKTHROUGHS.items():
        for screen in screens:
            write_walkthrough(os.path.join(site, collection), *screen)
    for collection in PROTOTYPES:
        write_prototype(os.path.join(site, collection), collection)
    for collection in OWN_CHROME:
        _write(os.path.join(site, collection, OWN_CHROME_MOCKUP), OWN_CHROME_PAGE % collection)
    for collection in DRAWN_DIRECTIONS:
        write_walkthrough(os.path.join(site, collection), *TEAM, (), table_frames() + "\n" + DIRECTIONS_LIST % "")
    _write(os.path.join(site, "prototype-updated", PROTOTYPE_MOCKUP + WATCHED_COMMIT), "")
    for collection in ONE_SCREEN:
        for page in ONE_SCREEN_GONE:
            os.remove(os.path.join(site, collection, page))
    for collection, seed in COLLECTIONS.items():
        spec = os.path.join(site, collection, SPEC_OF.get(collection, SPEC))
        text = seed_page(spec, seed)
        if collection in PENDING:
            _write(spec + PENDING_EDIT, PENDING[collection](text))
        if collection in ANSWERING:
            _write(spec + ANSWERS, "")
    for collection, pages in SET_COLLECTIONS.items():
        for page, seed in pages.items():
            seed_page(os.path.join(site, collection, page), seed)
    for collection in UNREADABLE:
        page = os.path.join(site, collection, SPEC_OF.get(collection, SPEC))
        human = os.path.join(page + ".review", "human")
        os.makedirs(human)
        os.symlink(os.path.join("..", "..", os.path.basename(page)), os.path.join(human, "%d-comment-qa-linked.json" % STAMP))
    for collection, (page, edit) in WATCHED.items():
        page = os.path.join(site, collection, page)
        with open(page, encoding="utf-8", newline="") as stream:
            _write(page + WATCHED_EDIT, edit(stream.read()))


def land_pending_edit(spec):
    """The agent's edit lands between page load and save (serve.py, on each event post)."""
    try:
        os.replace(spec + PENDING_EDIT, spec)
    except FileNotFoundError:
        pass


def land_watched_edit(page):
    """The agent's edit lands once the open page has checked its file (serve.py, after each HEAD of a page), so the page
    loaded the text before it. Its time is past the loaded file's whole second, the Last-Modified the page compares."""
    try:
        before = os.stat(page).st_mtime
        os.replace(page + WATCHED_EDIT, page)
    except FileNotFoundError:
        return
    stamp = max(time.time(), int(before) + 1)
    os.utime(page, (stamp, stamp))


def land_watched_commit(page):
    """The agent commits a change to the prototype on the lane branch once the open page has checked its file (serve.py,
    after each HEAD of a page), so the page loaded at the commit before it (mockups #prototype-updated)."""
    try:
        os.remove(page + WATCHED_COMMIT)
    except FileNotFoundError:
        return
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(page)))))
    _write(os.path.join(root, "prototype-round.txt"), "round 3: the export dialog names the file\n")
    identity = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "qa",
                "GIT_AUTHOR_EMAIL": "qa@example.invalid", "GIT_COMMITTER_NAME": "qa", "GIT_COMMITTER_EMAIL": "qa@example.invalid"}
    subprocess.run(["git", "-C", root, "commit", "-qam", "round 3"], check=True, capture_output=True, env={**os.environ, **identity})


def answer_comment(spec, body):
    """The agent answers a comment the page just saved (serve.py, after each stored event post),
    through the agent's own writer, emit-reply.sh."""
    if not os.path.exists(spec + ANSWERS) or not isinstance(body, dict) or body.get("event") != "comment":
        return
    subprocess.run(["sh", EMIT_REPLY, spec + ".review", body["id"], body.get("anchorId") or "",
                    json.dumps(body.get("target")), "acknowledged", "no spec change", "Noted."], check=True)


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as stream:
        stream.write(text)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: spools.py <site> <reset>")
    build(*sys.argv[1:])
