"""The owner's side of a mockup's review: the zero-wait scan, edits, replies, and the cursor over a mockup's spool, and
the Accept spec hand-off over a spec's mockup set (mockups #boundary-agent, #review-spool, #acceptance-scan,
#acceptance-accept-covers, #accept-set), over a fixture worktree."""

import hashlib
import json
import re
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
<p data-anchor="acceptance-plan">Then the plan card shows the plan's price and an Upgrade plan button.</p>
<p data-anchor="acceptance-usage">Then the usage bar shows the month's usage.</p>
</section>
</article>
<script defer src="./.viz/runtime.js"></script></body></html>
"""

MOCKUP = """<!doctype html>
<html><head><title>Plan page - mockup</title>
<style>.card{border:1px solid #ccc}</style></head>
<body>
<section data-mockup-frame="desktop" data-anchor="desktop" data-illustrates="acceptance-usage">
<div class="card" data-anchor="desktop-plan-card" data-illustrates="acceptance-plan">
<h3>Team plan</h3>
<p>$24 per seat, billed monthly.</p>
<button>Upgrade plan</button>
</div>
</section>
<section data-mockup-frame="phone" data-anchor="phone" data-illustrates="acceptance-usage">
<div class="card" data-anchor="phone-plan-card" data-illustrates="acceptance-plan">
<h3>Team plan</h3>
<p>$24 per seat, billed monthly.</p>
<button>Upgrade plan</button>
</div>
</section>
<script defer src="../../.viz/runtime.js"></script>
</body></html>
"""

# The agent's edit: a new element above the desktop button.
EDITED = MOCKUP.replace("<p>$24 per seat, billed monthly.</p>\n<button>",
                        "<p>$24 per seat, billed monthly.</p>\n<p>Includes 5 seats.</p>\n<button>", 1)

# A first round's directions (mockups #directions-file, #markup-directions): a prototype mockup whose pick is its route
# and whose other direction is another route of the same prototype.
PROTOTYPE_DIRECTIONS = """<!doctype html>
<html><head><meta name="mockup-prototype" content="/plans">
<title>Plan list - mockup</title>
<script defer src="../../.viz/runtime.js"></script></head>
<body>
<ul data-mockup-regions>
<li data-anchor="plan-list" data-select="#plans" data-illustrates="acceptance-plan">Plan list</li>
</ul>
<ul data-mockup-directions data-anchor="directions">
<li data-direction="cards" data-gives-up="Fewer plans fit on one screen."
    data-pick="Each plan's price and Upgrade plan read at a glance.">Plan cards</li>
<li data-direction="table" data-gives-up="On a phone the columns squeeze." data-route="/plans/table">Plan table</li>
</ul>
</body></html>
"""

# A drawn mockup's directions: the pick is its two frames, the other direction its own two frames naming it.
DRAWN_DIRECTIONS = MOCKUP.replace("<script defer", """<section data-mockup-frame="desktop" data-direction="table" \
data-anchor="table-desktop" data-illustrates="acceptance-plan"><table><tr><td>Team plan</td><td>$24</td></tr></table>\
</section>
<section data-mockup-frame="phone" data-direction="table" data-anchor="table-phone" data-illustrates="acceptance-plan">\
<table><tr><td>Team plan</td><td>$24</td></tr></table></section>
<ul data-mockup-directions data-anchor="directions">
<li data-direction="cards" data-gives-up="Fewer plans fit on one screen." data-pick="The price reads first.">Plan cards</li>
<li data-direction="table" data-gives-up="On a phone the columns squeeze.">Plan table</li>
</ul>
<script defer""", 1)


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def comment(cid, anchor, target, quote, version=MOCKUP):
    return {"id": cid, "event": "comment", "anchorId": anchor, "target": target, "quote": quote,
            "text": "Make this the primary action.", "actor": "human", "browser": "b1", "author": "Mango",
            "version": sha(version), "createdAt": "2026-10-27T10:00:00Z", "schemaVersion": 1}


def handoff(hid, events=None, **fields):
    body = {"id": hid, "event": "handoff", "anchorId": "", "target": None, "quote": None, "text": "batch from http",
            "actor": "human", "browser": "b1", "author": "Mango", "createdAt": "2026-10-27T10:01:00Z",
            "schemaVersion": 1, **fields}
    if events is not None:
        body["events"] = events
    return body


def pick(cid, name, key, version, browser="b1"):
    """A press of Pick on a direction: the reviewer's answer on the directions list, the direction's name its words,
    as an answer button saves one (mockups #directions-pick)."""
    return {**comment(cid, "directions", {"type": "element", "key": key}, name, version), "text": name,
            "browser": browser}


def kind(lines, *kinds):
    return [line.split("\t") for line in lines if line.split("\t")[0] in kinds]


def scan(root, cursor=".cursor-owner"):
    """The zero-wait scan: (exit code, stdout rows, stderr lines)."""
    done = subprocess.run(["sh", str(SCRIPTS / "watch-specs.sh"), str(root), cursor, "0", "1"],
                          text=True, capture_output=True)
    return done.returncode, [row.split("\t") for row in done.stdout.splitlines()], done.stderr.splitlines()


def reply(review, responds_to, anchor, target, text, change="edited the mockup"):
    """The owner's reply through the bundled emitter: its event id."""
    out = subprocess.run(["sh", str(SCRIPTS / "emit-reply.sh"), str(review), responds_to, anchor, json.dumps(target),
                          "acknowledged", change, text], text=True, capture_output=True, check=True).stdout
    return re.search(r"-reply-(r\d+)\.json", out).group(1)


class MockupScanTest(unittest.TestCase):
    """A lane worktree holding a spec and its mockup set, with spools as the page and the service write them."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="spec-chat-mockup-scan-")
        self.addCleanup(temp.cleanup)
        self.docs = Path(temp.name).resolve() / "docs"
        specs = self.docs / "specs"
        self.set = specs / "mockups" / "plans"
        self.set.mkdir(parents=True)
        self.spec = specs / "plans.spec.html"
        self.spec.write_text(SPEC, encoding="utf-8")
        self.mockup = self.set / "01-plan-page.mockup.html"
        self.mockup.write_text(MOCKUP, encoding="utf-8")
        self.second = self.set / "02-usage-on-phone.mockup.html"
        self.second.write_text(MOCKUP.replace("Plan page", "Usage on phone"), encoding="utf-8")

    def review(self, page):
        return Path(str(page) + ".review")

    def save(self, page, name, body, version_text=None):
        """An event as the page stores it, with the text its page showed kept under versions/ as the service does."""
        review = self.review(page)
        (review / "human").mkdir(parents=True, exist_ok=True)
        (review / "human" / name).write_text(json.dumps(body), encoding="utf-8")
        if version_text is not None:
            (review / "versions").mkdir(exist_ok=True)
            (review / "versions" / (sha(version_text) + ".html")).write_text(version_text, encoding="utf-8")
        return name

    def test_scan_reports_a_mockup_comment_with_the_mockups_path_the_part_it_marks_and_its_quote(self):
        """#acceptance-scan: a handed-off comment on the button of the desktop frame's card, and a selected quote in
        the phone frame."""
        c1 = self.save(self.mockup, "100-comment-c1.json",
                       comment("c1", "desktop-plan-card", {"type": "element", "key": "button[1]"}, "Upgrade plan"),
                       MOCKUP)
        c2 = self.save(self.mockup, "110-comment-c2.json",
                       comment("c2", "phone-plan-card", {"type": "text", "key": "billed monthly"}, "billed monthly"))
        h1 = self.save(self.mockup, "120-handoff-h1.json", handoff("h1", ["c1", "c2"]))

        code, rows, lines = scan(self.docs)
        self.assertEqual(code, 0, lines)
        self.assertEqual(rows, [[str(self.mockup), c1], [str(self.mockup), c2], [str(self.mockup), h1]])
        button = MOCKUP.index("<button>")
        desktop_button = (button, MOCKUP.index("</button>", button) + len("</button>"))
        phone_card = MOCKUP.index('data-anchor="phone-plan-card"')
        quote = MOCKUP.index("billed monthly", phone_card)
        self.assertEqual(lines, [
            "\t".join(("place", c1, "kept", "#desktop-plan-card", "%d-%d" % desktop_button, "Upgrade plan")),
            "\t".join(("place", c2, "kept", "#phone-plan-card", "%d-%d" % (quote, quote + len("billed monthly")),
                       "billed monthly")),
        ])

    def test_a_mark_follows_the_owners_edit_to_the_mockup(self):
        """#boundary-agent: the owner edits the mockup; the scan places the button comment on the same button."""
        c1 = self.save(self.mockup, "100-comment-c1.json",
                       comment("c1", "desktop-plan-card", {"type": "element", "key": "button[1]"}, "Upgrade plan"),
                       MOCKUP)
        self.save(self.mockup, "110-handoff-h1.json", handoff("h1", ["c1"]))
        self.mockup.write_text(EDITED, encoding="utf-8")
        code, _, lines = scan(self.docs)
        button = EDITED.index("<button>")
        self.assertEqual(code, 0, lines)
        self.assertEqual(lines, ["\t".join(("place", c1, "kept", "#desktop-plan-card",
                                            "%d-%d" % (button, EDITED.index("</button>", button) + len("</button>")),
                                            "Upgrade plan"))])
        self.assertNotEqual(button, MOCKUP.index("<button>"))

    def test_a_region_comment_names_its_region_with_no_quote(self):
        """A click on an empty spot inside an anchored region marks the region: its place is the region."""
        c1 = self.save(self.mockup, "100-comment-c1.json", comment("c1", "phone-plan-card", None, None), MOCKUP)
        self.save(self.mockup, "110-handoff-h1.json", handoff("h1", ["c1"]))
        start = MOCKUP.index("<div", MOCKUP.index('data-mockup-frame="phone"'))
        end = MOCKUP.index("</div>", start) + len("</div>")
        self.assertEqual(scan(self.docs)[2], ["\t".join(("place", c1, "kept", "#phone-plan-card",
                                                          "%d-%d" % (start, end), ""))])

    def test_the_owner_replies_and_records_the_batch_in_the_mockups_spool(self):
        """#boundary-agent: replies and the cursor work on a mockup's spool as on a spec's."""
        target = {"type": "element", "key": "button[1]"}
        c1 = self.save(self.mockup, "100-comment-c1.json",
                       comment("c1", "desktop-plan-card", target, "Upgrade plan"), MOCKUP)
        h1 = self.save(self.mockup, "110-handoff-h1.json", handoff("h1", ["c1"]))
        review = self.review(self.mockup)
        code, rows, _ = scan(self.docs)
        self.assertEqual((code, [row[1] for row in rows]), (0, [c1, h1]))

        r1 = reply(review, "c1", "desktop-plan-card", target, "Made Upgrade plan the primary button. OK to resolve?")
        with open(review / ".cursor-owner", "a", encoding="utf-8") as stream:
            stream.write("".join(row[1] + "\n" for row in rows))
        self.assertEqual(scan(self.docs), (3, [], []))
        threads = spool.fold_threads(spool.read_events(str(review)))
        self.assertEqual(threads["c1"]["status"], "acknowledged")
        self.assertEqual(threads["c1"]["messages"][-1]["body"]["id"], r1)
        self.assertFalse(self.review(self.spec).exists(), "a mockup's reply stays in its own spool")

        # A follow-up on the mockup page wakes the scan again, in the same spool.
        u2 = self.save(self.mockup, "200-reply-u2.json", {"id": "u2", "event": "reply", "respondsTo": r1,
                                                            "threadId": "c1", "text": "And on the phone?",
                                                            "actor": "human", "browser": "b1", "schemaVersion": 1})
        h2 = self.save(self.mockup, "210-handoff-h2.json", handoff("h2", ["u2"]))
        self.assertEqual(scan(self.docs)[:2], (0, [[str(self.mockup), u2], [str(self.mockup), h2]]))

    def test_each_ready_page_reports_its_own_rows(self):
        """The spec's spool and a mockup's spool each hold a ready batch: each scan reports one page, the cursor of
        that page's spool advances it, and the next scan reports the other."""
        s1 = self.save(self.spec, "100-comment-s1.json",
                       comment("s1", "acceptance-plan", {"type": "text", "key": "plan card"}, "plan card", SPEC), SPEC)
        sh = self.save(self.spec, "110-handoff-h1.json", handoff("h1", ["s1"]))
        m1 = self.save(self.mockup, "100-comment-m1.json",
                       comment("m1", "desktop-plan-card", {"type": "element", "key": "button[1]"}, "Upgrade plan"),
                       MOCKUP)
        mh = self.save(self.mockup, "110-handoff-h1.json", handoff("h1", ["m1"]))
        seen = {}
        for _ in range(2):
            code, rows, _ = scan(self.docs)
            self.assertEqual(code, 0)
            page = rows[0][0]
            self.assertEqual({row[0] for row in rows}, {page})
            seen[page] = [row[1] for row in rows]
            with open(page + ".review/.cursor-owner", "a", encoding="utf-8") as stream:
                stream.write("".join(row[1] + "\n" for row in rows))
        self.assertEqual(seen, {str(self.spec): [s1, sh], str(self.mockup): [m1, mh]})
        self.assertEqual(scan(self.docs)[0], 3)

    def test_a_pick_on_a_prototype_mockups_directions_reports_the_mockup_its_directions_and_the_picked_name(self):
        """#acceptance-directions-scan, #directions-pick: the reviewer pressed Pick on the direction that is not the
        agent's pick and handed off; the scan's rows start with the mockup's path, and its answer line names the
        directions list and the picked direction's name, by which the owner finds its route in the file."""
        mockup = self.set / "03-plan-list.mockup.html"
        mockup.write_text(PROTOTYPE_DIRECTIONS, encoding="utf-8")
        p1 = self.save(mockup, "100-comment-p1.json", pick("p1", "Plan table", "li[2]", PROTOTYPE_DIRECTIONS),
                       PROTOTYPE_DIRECTIONS)
        h1 = self.save(mockup, "110-handoff-h1.json", handoff("h1", ["p1"]))
        code, rows, lines = scan(self.docs)
        self.assertEqual((code, rows), (0, [[str(mockup), p1], [str(mockup), h1]]), lines)
        self.assertEqual(kind(lines, "answer", "superseded"), [["answer", p1, "#directions", "", "", "Plan table"]])

    def test_the_newest_pick_on_a_drawn_mockups_directions_stands(self):
        """#directions-pick: the newest answer standing (threads #supersede): Pick on one direction, then on the other,
        then Hand off; another reviewer's draft pick is no answer yet."""
        self.mockup.write_text(DRAWN_DIRECTIONS, encoding="utf-8")
        p1 = self.save(self.mockup, "100-comment-p1.json", pick("p1", "Plan table", "li[2]", DRAWN_DIRECTIONS),
                       DRAWN_DIRECTIONS)
        p2 = self.save(self.mockup, "110-comment-p2.json", pick("p2", "Plan cards", "li[1]", DRAWN_DIRECTIONS))
        self.save(self.mockup, "115-comment-p3.json", pick("p3", "Plan table", "li[2]", DRAWN_DIRECTIONS, "b2"))
        h1 = self.save(self.mockup, "120-handoff-h1.json", handoff("h1", ["p1", "p2"]))
        code, rows, lines = scan(self.docs)
        self.assertEqual((code, [row[1] for row in rows]), (0, [p1, p2, h1]), lines)
        self.assertEqual(kind(lines, "answer", "superseded"), [
            ["superseded", p1, p2],
            ["answer", p2, "#directions", "", "", "Plan cards"],
        ])

    def test_a_mockup_with_no_directions_list_reads_no_comment_on_a_list_as_an_answer(self):
        """The directions list is the one the file names in data-mockup-directions: a comment on another list is a
        comment, and once the round after removes the list, comments on its anchor are no answers."""
        self.save(self.mockup, "100-comment-c1.json", pick("c1", "Plan table", "li[2]", MOCKUP), MOCKUP)
        self.save(self.mockup, "110-handoff-h1.json", handoff("h1", ["c1"]))
        code, _, lines = scan(self.docs)
        self.assertEqual((code, kind(lines, "answer", "superseded")), (0, []), lines)
        self.assertEqual(spool.page_questions(PROTOTYPE_DIRECTIONS), {"directions": ("", "")})
        self.assertEqual(spool.page_questions(PROTOTYPE_DIRECTIONS.replace(" data-mockup-directions", "")), {})

    def test_the_accept_spec_hand_off_names_each_mockup_of_the_set_with_its_accepted_version(self):
        """#acceptance-accept-covers, #accept-set: the scan reports the Accept spec hand-off, and stderr names each
        mockup it accepts with the version its page showed."""
        first, second = sha(MOCKUP), sha(self.second.read_text(encoding="utf-8"))
        accept = self.save(self.spec, "300-handoff-h9.json", handoff("h9", mockups=[
            {"path": "mockups/plans/01-plan-page.mockup.html", "version": first},
            {"path": "mockups/plans/02-usage-on-phone.mockup.html", "version": second},
        ]))
        code, rows, lines = scan(self.docs)
        self.assertEqual((code, rows), (0, [[str(self.spec), accept]]))
        self.assertEqual(lines, ["\t".join(("accept", accept, str(self.mockup), first)),
                                 "\t".join(("accept", accept, str(self.second), second))])

    def test_an_accept_entry_outside_the_set_names_no_mockup(self):
        """#set-where: the set is mockups/<spec name>/ beside the spec; the hand-off is written by a page with no
        login, so an entry naming any other file, or no path and version, is not reported as an accepted mockup."""
        accept = self.save(self.spec, "300-handoff-h9.json", handoff("h9", mockups=[
            {"path": "mockups/other/01-plan-page.mockup.html", "version": "v1"},
            {"path": "mockups/plans/../../plans.spec.html", "version": "v2"},
            {"path": "mockups/plans/01-plan-page.mockup.html"},
            "mockups/plans/01-plan-page.mockup.html",
            {"path": "./mockups/plans/01-plan-page.mockup.html", "version": "v3\tv4"},
        ]))
        code, rows, lines = scan(self.docs)
        self.assertEqual((code, rows), (0, [[str(self.spec), accept]]))
        self.assertEqual(lines, ["\t".join(("accept", accept, str(self.mockup), "v3 v4"))])

    def test_a_comment_hand_off_and_a_spec_with_no_set_have_no_accept_line(self):
        self.assertEqual(spool.accept_rows(str(self.review(self.spec)), []), [])
        name = self.save(self.spec, "100-handoff-h1.json", handoff("h1", []))
        self.assertEqual(spool.accept_rows(str(self.review(self.spec)), [name]), [])
        # review-state #live-accept: a hand-off listing its events is a comment hand-off, whatever else it carries.
        name = self.save(self.spec, "200-handoff-h2.json", handoff("h2", ["c1"], mockups=[
            {"path": "mockups/plans/01-plan-page.mockup.html", "version": "v1"}]))
        self.assertEqual(spool.accept_rows(str(self.review(self.spec)), [name]), [])
        # A mockup's own spool has no set: an entry there names nothing.
        name = self.save(self.mockup, "100-handoff-h1.json", handoff("h1", mockups=[
            {"path": "mockups/plans/01-plan-page.mockup.html", "version": "v1"}]))
        self.assertEqual(spool.accept_rows(str(self.review(self.mockup)), [name]), [])

    def test_the_cli_prints_accept_lines_for_the_names_it_reads(self):
        name = self.save(self.spec, "300-handoff-h9.json", handoff("h9", mockups=[
            {"path": "mockups/plans/01-plan-page.mockup.html", "version": "v1"}]))
        done = subprocess.run([sys.executable, str(ASSETS / "spool.py"), "accept", str(self.review(self.spec))],
                              input=name + "\n", text=True, capture_output=True, check=True)
        self.assertEqual(done.stdout, "accept\t%s\t%s\tv1\n" % (name, self.mockup))


if __name__ == "__main__":
    unittest.main()