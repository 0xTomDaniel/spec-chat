"""The owner's scan reports each comment's spot, each message's stage, and each TBD question's newest answer (threads
#story-owner, #acceptance-scan-spot, #acceptance-supersede, #supersede, #stage-tag; mockups #acceptance-step-scan,
#acceptance-question-scan; review-state #model-fields-spot, #model-fields-stage), over a fixture worktree of a spec
showing a video and a mockup with a walkthrough."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "skill" / "review-spec" / "assets"
SCRIPTS = ROOT / "skill" / "review-spec" / "scripts"
sys.path.insert(0, str(ASSETS))
import spool  # noqa: E402

SPEC = """<!doctype html>
<html><head><title>Plans - spec</title></head>
<body><article class="spec">
<section data-anchor="plans"><h2 data-anchor="plans-title">Plans</h2>
<figure data-anchor="upgrade-clip"><video src="upgrade.webm" controls></video>
<figcaption>Upgrading a plan, start to end.</figcaption></figure>
<p data-anchor="acceptance-plan">Then the plan card shows the plan's price and an Upgrade plan button.</p>
<p data-anchor="trial-length" data-spec-tbd="later">How long is the trial? Left for a later slice.</p>
<p data-anchor="seat-limit" data-spec-tbd>How many seats does the Team plan include?
<button type="button" data-spec-choice="Five seats">Five seats (recommended)</button>
<button type="button" data-spec-choice="Ten seats">Ten seats</button>
</p>
</section>
</article>
<script defer src="./.viz/runtime.js"></script></body></html>
"""

MOCKUP = """<!doctype html>
<html><head><title>Plan page - mockup</title></head>
<body>
<section data-mockup-frame="desktop" data-anchor="desktop" data-illustrates="acceptance-plan">
<div class="card" data-anchor="desktop-plan-card"><h3>Team plan</h3><button>Upgrade plan</button></div>
</section>
<section data-mockup-frame="phone" data-anchor="phone" data-illustrates="acceptance-plan">
<div class="card" data-anchor="phone-plan-card"><h3>Team plan</h3><button>Upgrade plan</button></div>
</section>
<ol data-mockup-steps>
<li data-step="plans-listed">The plans are listed.</li>
<li data-step="plan-chosen" data-step-by="desktop-plan-card phone-plan-card">The Team plan is chosen.</li>
</ol>
<aside data-mockup-questions>
<p data-anchor="upgrade-label" data-spec-tbd data-about="desktop-plan-card phone-plan-card" data-step="plan-chosen">
Should the button read Upgrade plan or Choose plan?
<button type="button" data-spec-choice="Upgrade plan">Upgrade plan (recommended)</button>
<button type="button" data-spec-choice="Choose plan">Choose plan</button>
</p>
</aside>
<script defer src="../../.viz/runtime.js"></script>
</body></html>
"""

MANGO = {"browser": "b1", "author": "Mango"}
PAPAYA = {"browser": "b2", "author": "Papaya"}


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def comment(cid, anchor, target, quote, text, page, who=MANGO, **spot):
    return {"id": cid, "event": "comment", "anchorId": anchor, "target": target, "quote": quote, "text": text,
            "actor": "human", **who, "version": sha(page), "createdAt": "2026-11-02T10:00:00Z", "schemaVersion": 1,
            **spot}


def answer(cid, anchor, choice, page, who=MANGO, key="button[1]"):
    """An answer button's press: an ordinary comment on that button, the answer as its text (#choice-save)."""
    return comment(cid, anchor, {"type": "element", "key": key}, choice, choice, page, who)


def handoff(hid, events, who=MANGO):
    return {"id": hid, "event": "handoff", "events": events, "anchorId": "", "target": None, "text": "",
            "actor": "human", **who, "createdAt": "2026-11-02T10:05:00Z", "schemaVersion": 1}


def scan(root):
    """The zero-wait scan: (exit code, stdout rows, stderr lines by kind)."""
    done = subprocess.run(["sh", str(SCRIPTS / "watch-specs.sh"), str(root), ".cursor-owner", "0", "1"],
                          text=True, capture_output=True)
    lines = [line.split("\t") for line in done.stderr.splitlines()]
    return done.returncode, [row.split("\t") for row in done.stdout.splitlines()], lines


def kind(lines, *kinds):
    return [line for line in lines if line[0] in kinds]


class SpotScanTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="spec-chat-spot-scan-")
        self.addCleanup(temp.cleanup)
        self.docs = Path(temp.name).resolve() / "docs"
        specs = self.docs / "specs"
        (specs / "mockups" / "plans").mkdir(parents=True)
        self.spec = specs / "plans.spec.html"
        self.spec.write_text(SPEC, encoding="utf-8")
        self.mockup = specs / "mockups" / "plans" / "01-plan-page.mockup.html"
        self.mockup.write_text(MOCKUP, encoding="utf-8")

    def save(self, page, name, body):
        """An event as the page stores it, with the text its page showed kept under versions/ as the service does."""
        review = Path(str(page) + ".review")
        (review / "human").mkdir(parents=True, exist_ok=True)
        (review / "human" / name).write_text(json.dumps(body), encoding="utf-8")
        (review / "versions").mkdir(exist_ok=True)
        text = page.read_text(encoding="utf-8")
        (review / "versions" / (sha(text) + ".html")).write_text(text, encoding="utf-8")
        return name

    def cursor(self, page, rows):
        with open(str(page) + ".review/.cursor-owner", "a", encoding="utf-8") as stream:
            stream.write("".join(row[1] + "\n" for row in rows))

    def test_a_comment_on_a_box_at_a_moment_of_a_video_reports_its_block_moment_and_box(self):
        """threads #acceptance-scan-spot."""
        box = {"left": 0.25, "top": 0.1, "width": 0.5, "height": 0.3}
        c1 = self.save(self.spec, "100-comment-c1.json", comment(
            "c1", "upgrade-clip", {"type": "element", "key": "video[1]"}, None, "This dialog flashes before the price.",
            SPEC, moment=83.4, box=box))
        h1 = self.save(self.spec, "110-handoff-h1.json", handoff("h1", ["c1"]))
        code, rows, lines = scan(self.docs)
        self.assertEqual((code, rows), (0, [[str(self.spec), c1], [str(self.spec), h1]]), lines)
        self.assertEqual([line[:4] for line in kind(lines, "place")], [["place", c1, "kept", "#upgrade-clip"]])
        self.assertEqual(kind(lines, "spot"), [["spot", c1, "", "83.4", "0.25,0.1,0.5,0.3", ""]])

    def test_a_comment_at_a_moment_with_no_box_reports_the_moment_alone(self):
        c1 = self.save(self.spec, "100-comment-c1.json", comment(
            "c1", "upgrade-clip", {"type": "element", "key": "video[1]"}, None, "Too slow here.", SPEC, moment=12))
        self.save(self.spec, "110-handoff-h1.json", handoff("h1", ["c1"]))
        self.assertEqual(kind(scan(self.docs)[2], "spot"), [["spot", c1, "", "12", "", ""]])

    def test_a_comment_on_a_mockup_part_at_its_second_step_reports_the_path_part_step_and_quote(self):
        """mockups #acceptance-step-scan; a later reply in the thread reads at its thread's step."""
        c1 = self.save(self.mockup, "100-comment-c1.json", comment(
            "c1", "desktop-plan-card", {"type": "text", "key": "Team plan"}, "Team plan", "Bold the chosen plan.",
            MOCKUP, step="plan-chosen"))
        h1 = self.save(self.mockup, "110-handoff-h1.json", handoff("h1", ["c1"]))
        code, rows, lines = scan(self.docs)
        self.assertEqual((code, rows), (0, [[str(self.mockup), c1], [str(self.mockup), h1]]), lines)
        place = kind(lines, "place")
        self.assertEqual([(line[1], line[3], line[5]) for line in place], [(c1, "#desktop-plan-card", "Team plan")])
        self.assertEqual(kind(lines, "spot"), [["spot", c1, "plan-chosen", "", "", ""]])
        self.cursor(self.mockup, rows)

        u2 = self.save(self.mockup, "200-reply-u2.json", {
            "id": "u2", "event": "reply", "respondsTo": "c1", "threadId": "c1", "text": "And on the phone.",
            "actor": "human", **MANGO, "schemaVersion": 1})
        self.save(self.mockup, "210-handoff-h2.json", handoff("h2", ["u2"]))
        self.assertEqual(kind(scan(self.docs)[2], "spot"), [["spot", u2, "plan-chosen", "", "", ""]])

    def test_a_comment_with_no_spot_has_no_spot_line(self):
        self.save(self.spec, "100-comment-c1.json", comment(
            "c1", "acceptance-plan", {"type": "text", "key": "plan card"}, "plan card", "Name the card.", SPEC,
            step="", moment="12", box=[0.1, 0.1, 0.2, 0.2]))
        self.save(self.spec, "110-handoff-h1.json", handoff("h1", ["c1"]))
        code, _, lines = scan(self.docs)
        self.assertEqual((code, kind(lines, "spot", "answer", "superseded")), (0, []))

    def test_a_later_answer_supersedes_the_earlier_one(self):
        """threads #acceptance-supersede: one answer button, then the other, then Hand off."""
        a1 = self.save(self.spec, "100-comment-a1.json", answer("a1", "seat-limit", "Five seats", SPEC))
        a2 = self.save(self.spec, "110-comment-a2.json", answer("a2", "seat-limit", "Ten seats", SPEC, key="button[2]"))
        self.save(self.spec, "120-handoff-h1.json", handoff("h1", ["a1", "a2"]))
        code, rows, lines = scan(self.docs)
        self.assertEqual(code, 0, lines)
        self.assertEqual(kind(lines, "answer", "superseded"), [
            ["superseded", a1, a2],
            ["answer", a2, "#seat-limit", "", "", "Ten seats"],
        ])
        self.cursor(self.spec, rows)

        # A changed mind in words, in a later batch: it stands, and the earlier answers are no longer reported.
        a3 = self.save(self.spec, "200-comment-a3.json", comment(
            "a3", "seat-limit", None, None, "Eight seats,\n  billed per seat.", SPEC))
        self.save(self.spec, "210-handoff-h2.json", handoff("h2", ["a3"]))
        self.assertEqual(kind(scan(self.docs)[2], "answer", "superseded"),
                         [["answer", a3, "#seat-limit", "", "", "Eight seats, billed per seat."]])

    def test_an_edited_answer_reads_by_its_edit(self):
        a1 = self.save(self.spec, "100-comment-a1.json", comment("a1", "seat-limit", None, None, "Six", SPEC))
        e1 = self.save(self.spec, "110-edit-e1.json", {
            "id": "e1", "event": "edit", "supersedes": "a1", "threadId": "a1", "anchorId": "seat-limit",
            "target": None, "quote": None, "text": "Seven seats", "actor": "human", **MANGO, "schemaVersion": 1})
        self.save(self.spec, "120-handoff-h1.json", handoff("h1", ["a1", "e1"]))
        self.assertEqual(kind(scan(self.docs)[2], "answer", "superseded"), [
            ["answer", a1, "#seat-limit", "", "", "Seven seats"],
            ["answer", e1, "#seat-limit", "", "", "Seven seats"],
        ])

    def test_another_reviewers_draft_answer_supersedes_nothing(self):
        a1 = self.save(self.spec, "100-comment-a1.json", answer("a1", "seat-limit", "Five seats", SPEC))
        self.save(self.spec, "110-comment-a2.json", answer("a2", "seat-limit", "Ten seats", SPEC, PAPAYA, "button[2]"))
        self.save(self.spec, "120-handoff-h1.json", handoff("h1", ["a1"]))
        code, rows, lines = scan(self.docs)
        self.assertEqual([row[1] for row in rows], [a1, "120-handoff-h1.json"])
        self.assertEqual(kind(lines, "answer", "superseded"), [["answer", a1, "#seat-limit", "", "", "Five seats"]])

    def test_comments_on_a_block_that_asks_nothing_are_no_answers(self):
        self.save(self.spec, "100-comment-c1.json", comment(
            "c1", "acceptance-plan", None, None, "First thought.", SPEC))
        self.save(self.spec, "110-comment-c2.json", comment(
            "c2", "acceptance-plan", None, None, "Second thought.", SPEC))
        self.save(self.spec, "120-handoff-h1.json", handoff("h1", ["c1", "c2"]))
        self.assertEqual(kind(scan(self.docs)[2], "answer", "superseded"), [])

    def test_comments_on_a_block_left_for_later_are_no_answers(self):
        """prompt-first-shaping #finish-tbd-later: a `data-spec-tbd="later"` block asks nothing now, so two handed-off
        comments on it are ordinary comments, neither superseding the other."""
        self.save(self.spec, "100-comment-c1.json", comment("c1", "trial-length", None, None, "Two weeks.", SPEC))
        self.save(self.spec, "110-comment-c2.json", comment("c2", "trial-length", None, None, "A month.", SPEC))
        self.save(self.spec, "120-handoff-h1.json", handoff("h1", ["c1", "c2"]))
        code, _, lines = scan(self.docs)
        self.assertEqual((code, kind(lines, "answer", "superseded")), (0, []), lines)
        self.assertEqual(list(spool.page_questions(SPEC)), ["seat-limit"])

    def test_a_mockup_questions_answer_reports_the_question_its_parts_and_step_and_the_words(self):
        """mockups #acceptance-question-scan: rows start with the mockup's path."""
        a1 = self.save(self.mockup, "100-comment-a1.json", answer("a1", "upgrade-label", "Choose plan", MOCKUP,
                                                                  key="button[2]"))
        h1 = self.save(self.mockup, "110-handoff-h1.json", handoff("h1", ["a1"]))
        code, rows, lines = scan(self.docs)
        self.assertEqual((code, rows), (0, [[str(self.mockup), a1], [str(self.mockup), h1]]), lines)
        self.assertEqual(kind(lines, "answer", "superseded"), [
            ["answer", a1, "#upgrade-label", "desktop-plan-card phone-plan-card", "plan-chosen", "Choose plan"]])

    def test_hosted_threads_report_spots_and_no_answers(self):
        hosted = Path(self.docs.parent) / "comments" / "scope"
        (hosted / "human").mkdir(parents=True)
        body = {"id": "c1", "event": "comment", "subject": "clip:7", "kind": "clip", "label": "Clip 7",
                "from": "https://host.example/clips/7", "target": None, "quote": None, "text": "Here.",
                "actor": "human", **MANGO, "moment": 4.5, "createdAt": "2026-11-02T10:00:00Z", "schemaVersion": 1}
        (hosted / "human" / "100-comment-c1.json").write_text(json.dumps(body), encoding="utf-8")
        (hosted / "human" / "110-handoff-h1.json").write_text(json.dumps(handoff("h1", ["c1"])), encoding="utf-8")
        code, _, lines = scan(hosted)
        self.assertEqual(code, 0, lines)
        self.assertEqual(kind(lines, "spot", "answer"), [["spot", "100-comment-c1.json", "", "4.5", "", ""]])
        self.assertEqual(spool.answer_rows(str(hosted), ["100-comment-c1.json"]), [])

    def test_a_comment_with_a_picture_of_its_boxed_spot_reports_its_box_and_the_pictures_file(self):
        """threads #acceptance-scan-picture, #box-picture-scan: a comment on a box with no video, written through a
        page that drew on a canvas and made a picture of the boxed spot, kept in its hosted threads: the scan reports its
        box and the path of the picture's file in the spool, so the agent can look at what the human saw; a reply reads
        at its thread's. A picture field no file name holds names nothing."""
        hosted = Path(self.docs.parent) / "comments" / "scope"
        (hosted / "human").mkdir(parents=True)
        (hosted / "pictures").mkdir()
        picture = sha("png bytes") + ".png"
        (hosted / "pictures" / picture).write_bytes(b"\x89PNG\r\n\x1a\n")
        box = {"left": 0.5, "top": 0.25, "width": 0.25, "height": 0.125}
        base = {"event": "comment", "subject": "lane-card:1514", "kind": "Lane card", "label": "QA-1514 card",
                "target": None, "quote": None, "actor": "human", **MANGO, "createdAt": "2026-11-02T10:00:00Z",
                "schemaVersion": 1}
        events = {"100-comment-c1.json": {**base, "id": "c1", "text": "QA passed on the rerun.", "box": box,
                                          "picture": picture},
                  "105-comment-c2.json": {**base, "id": "c2", "text": "Odd.", "picture": "../../registry.toml"},
                  "110-reply-u1.json": {"id": "u1", "event": "reply", "respondsTo": "c1", "threadId": "c1",
                                        "text": "And here.", "actor": "human", **MANGO, "schemaVersion": 1},
                  "120-handoff-h1.json": handoff("h1", ["c1", "c2", "u1"])}
        for name, body in events.items():
            (hosted / "human" / name).write_text(json.dumps(body), encoding="utf-8")
        code, _, lines = scan(hosted)
        self.assertEqual(code, 0, lines)
        shown = str(hosted / "pictures" / picture)
        self.assertEqual(kind(lines, "spot"), [["spot", "100-comment-c1.json", "", "", "0.5,0.25,0.25,0.125", shown],
                                               ["spot", "110-reply-u1.json", "", "", "0.5,0.25,0.25,0.125", shown]])
        self.assertTrue(Path(shown).is_file())

    def test_each_message_reports_the_stage_it_was_written_in(self):
        """threads #stage-tag, review-state #model-fields-stage: a comment's and a reply's own stage; an edit's, its
        message's, since it carries none; a message with none has no line."""
        c1 = self.save(self.spec, "100-comment-c1.json", comment(
            "c1", "acceptance-plan", None, None, "Name the card.", SPEC, stage="Spec review"))
        c2 = self.save(self.spec, "105-comment-c2.json", comment("c2", "plans", None, None, "Shorter title.", SPEC))
        self.save(self.spec, "110-handoff-h1.json", handoff("h1", ["c1", "c2"]))
        code, rows, lines = scan(self.docs)
        self.assertEqual(code, 0, lines)
        self.assertEqual(kind(lines, "stage"), [["stage", c1, "Spec review"]])
        self.cursor(self.spec, rows)

        e1 = self.save(self.spec, "200-edit-e1.json", {
            "id": "e1", "event": "edit", "supersedes": "c1", "threadId": "c1", "anchorId": "acceptance-plan",
            "target": None, "quote": None, "text": "Name the plan card.", "actor": "human", **MANGO, "schemaVersion": 1})
        u2 = self.save(self.spec, "210-reply-u2.json", {
            "id": "u2", "event": "reply", "respondsTo": "c2", "threadId": "c2", "text": "Or drop it.",
            "actor": "human", **MANGO, "stage": " Code\n review", "schemaVersion": 1})
        self.save(self.spec, "220-handoff-h2.json", handoff("h2", ["e1", "u2"]))
        self.assertEqual(kind(scan(self.docs)[2], "stage"), [["stage", e1, "Spec review"], ["stage", u2, "Code review"]])

    def test_the_cli_prints_spot_and_answer_lines_for_the_names_it_reads(self):
        c1 = self.save(self.spec, "100-comment-c1.json", comment(
            "c1", "upgrade-clip", None, None, "Here.", SPEC, moment=1.5))
        a1 = self.save(self.spec, "110-comment-a1.json", answer("a1", "seat-limit", "Five seats", SPEC))
        self.save(self.spec, "120-handoff-h1.json", handoff("h1", ["c1", "a1"]))
        review = str(self.spec) + ".review"
        run = lambda what: subprocess.run([sys.executable, str(ASSETS / "spool.py"), what, review],  # noqa: E731
                                          input=c1 + "\n" + a1 + "\n", text=True, capture_output=True, check=True)
        self.assertEqual(run("spot").stdout, "spot\t%s\t\t1.5\t\t\n" % c1)
        self.assertEqual(run("answer").stdout, "answer\t%s\t#seat-limit\t\t\tFive seats\n" % a1)


if __name__ == "__main__":
    unittest.main()
