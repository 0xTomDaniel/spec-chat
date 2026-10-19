"""The owner's side of a host page's comments: the zero-wait scan, replies, and the cursor over a scope's hosted threads
and a spec's spool (comment-layer #boundary-agent, #acceptance-host-scan, #acceptance-host-scan-spec; review-state
#model-fields-hosted; host-wake #wake-rule-message)."""

import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "skill" / "review-spec" / "assets"
SCRIPTS = ROOT / "skill" / "review-spec" / "scripts"
sys.path.insert(0, str(ASSETS))
import spool  # noqa: E402

_hosted_spec = importlib.util.spec_from_file_location("review_scan_hosted_fixture", ROOT / "tests" / "review-serve-hosted.py")
_hosted = importlib.util.module_from_spec(_hosted_spec)
_hosted_spec.loader.exec_module(_hosted)
serve, comment, FakeWaker = _hosted.serve, _hosted.comment, _hosted.FakeWaker
LANE, SCOPE, CRITERION, TURN = _hosted.LANE, _hosted.SCOPE, _hosted.CRITERION, _hosted.TURN

FROM = "https://host.example/panes/w1:p3#turn-42"
QUOTE = "exit status 2"
WAKE_RE = re.compile(r"scope (\S+), hosted threads (.+), cursor (\S+), (\d+) events\. ")


def scan(root, cursor=".cursor-owner"):
    """The zero-wait scan the wake prompt asks for: (exit code, stdout rows, stderr lines)."""
    done = subprocess.run(["sh", str(SCRIPTS / "watch-specs.sh"), str(root), cursor, "0", "1"],
                          text=True, capture_output=True)
    return done.returncode, [row.split("\t") for row in done.stdout.splitlines()], done.stderr.splitlines()


def reply(review, responds_to, target, text):
    """The owner's reply through the bundled emitter: its event id."""
    out = subprocess.run(["sh", str(SCRIPTS / "emit-reply.sh"), str(review), responds_to, "", json.dumps(target),
                          "acknowledged", "no spec change", text], text=True, capture_output=True, check=True).stdout
    return re.search(r"-reply-(r\d+)\.json", out).group(1)


class OwnerScanTest(_hosted.HostedServiceCase):
    """A box with a lane's spec row and an operator's scope row, a running service, and a wake provider."""

    def setUp(self):
        super().setUp()
        self.serve()
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        self.waker = FakeWaker(self.dir, self.state)
        for owner in ("w1:pLane", "w1:pOps"):
            self.waker.agent(owner)
        self.controller = serve.WakeController(types.SimpleNamespace(mount_state=self.mount_state,
                                                                     state_dir=str(self.state)))

    def wake(self):
        """The last wake prompt's scope, hosted threads, cursor, and event count."""
        self.controller.poll()
        return WAKE_RE.search(self.waker.says()[-1][2]).groups()

    def kept(self, scope, subject, event):
        status, answer, _ = self.save(scope, subject, event)
        self.assertEqual(status, 200, answer)
        return answer["name"]

    def test_scan_of_the_hosted_threads_the_wake_names_reports_each_host_comment(self):
        """comment-layer #acceptance-host-scan; host-wake #wake-rule-message: the prompt's path and cursor are what
        the scan takes, and the scan's batch is the prompt's count."""
        anchor = {"subject": TURN, "kind": "agent turn", "label": "Turn 42", "from": FROM,
                  "quote": QUOTE, "target": {"type": "text", "key": QUOTE}}
        c1 = self.kept(SCOPE, TURN, comment("c1", **anchor))
        h1 = self.kept(SCOPE, TURN, {"event": "handoff", "id": "h1", "events": ["c1"]})
        scope, hosted, cursor, count = self.wake()
        self.assertEqual((scope, hosted, cursor), (SCOPE, str(self.state / "comments" / SCOPE), ".cursor-owner"))

        code, rows, lines = scan(hosted, cursor)
        self.assertEqual(code, 0, lines)
        self.assertEqual(rows, [[hosted, c1], [hosted, h1]])
        self.assertEqual(len(rows), int(count))
        self.assertEqual(lines, ["\t".join(("host", c1, TURN, "agent turn", "Turn 42", FROM, QUOTE))])

        # The owner replies in the hosted thread, records the batch, and the human reads the reply where they wrote.
        r1 = reply(hosted, "c1", anchor["target"], "The test runner exits 2 on a missing fixture.")
        with open(Path(hosted, cursor), "a", encoding="utf-8") as stream:
            stream.write("".join(row[1] + "\n" for row in rows))
        self.assertEqual(scan(hosted, cursor)[0], 3)
        status, events = self.read_events(SCOPE, TURN)
        self.assertEqual({e["body"]["id"]: e["actor"] for e in events}, {"c1": "human", "h1": "human", r1: "agent"})
        self.assertEqual(spool.fold_threads(events)["c1"]["status"], "acknowledged")

        # A follow-up on the host page carries no subject of its own; the scan routes it by its thread's.
        u2 = self.kept(SCOPE, TURN, {"event": "reply", "id": "u2", "respondsTo": r1, "threadId": "c1", "text": "Which?"})
        h2 = self.kept(SCOPE, TURN, {"event": "handoff", "id": "h2", "events": ["u2"]})
        code, rows, lines = scan(hosted, cursor)
        self.assertEqual((code, rows), (0, [[hosted, h2], [hosted, u2]]))
        self.assertEqual(lines, ["\t".join(("host", u2, TURN, "agent turn", "Turn 42", FROM, QUOTE))])

    def test_a_lane_scope_wakes_its_owner_with_a_path_the_scan_takes(self):
        """host-wake #wake-rule-message: a lane's hosted threads sit in the service state, not under its spec root."""
        c1 = self.kept(LANE, TURN, comment("c1", subject=TURN, kind="agent turn", **{"from": FROM}))
        self.kept(LANE, TURN, {"event": "handoff", "id": "h1", "events": ["c1"]})
        _, hosted, cursor, _ = self.wake()
        self.assertEqual(self.waker.says()[-1][0], "w1:pLane")
        code, rows, lines = scan(hosted, cursor)
        self.assertEqual((code, rows[0]), (0, [hosted, c1]))
        self.assertEqual(lines, ["\t".join(("host", c1, TURN, "agent turn", "", FROM, ""))])
        self.assertEqual(scan(self.repo / "docs")[0], 3, "a hosted thread is not in the lane's spec spool")

    def test_scan_of_a_spec_reports_a_host_comment_at_its_criterion_with_the_hosts_fields(self):
        """comment-layer #acceptance-host-scan-spec: kept in the spec's spool, placed at the criterion, and given
        the host's kind, label, and from address."""
        anchor = {"kind": "criterion", "label": "Alpha", "from": "https://host.example/lanes/ann1",
                  "quote": "Alpha criterion", "target": {"type": "text", "key": "Alpha criterion"}}
        c1 = self.kept(LANE, CRITERION, comment("c1", subject=CRITERION, **anchor))
        h1 = self.kept(LANE, CRITERION, {"event": "handoff", "id": "h1", "events": ["c1"]})
        code, rows, lines = scan(self.repo / "docs")
        self.assertEqual(code, 0, lines)
        self.assertEqual(rows, [[str(self.spec), c1], [str(self.spec), h1]])
        text = self.spec.read_text(encoding="utf-8")
        start = text.index("Alpha criterion")
        self.assertEqual(lines, [
            "\t".join(("place", c1, "kept", "#acceptance-a", "%d-%d" % (start, start + len("Alpha criterion")),
                       "Alpha criterion")),
            "\t".join(("host", c1, "", "criterion", "Alpha", "https://host.example/lanes/ann1", "Alpha criterion")),
        ])


class HostRowsTest(unittest.TestCase):
    """spool.host_rows over a fixture spool: every event of a thread written on a host page, by its root comment."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="spec-chat-host-rows-")
        self.addCleanup(temp.cleanup)
        self.review = Path(temp.name)
        for actor in ("human", "agent"):
            (self.review / actor).mkdir()

    def write(self, actor, name, body):
        (self.review / actor / name).write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
        return name

    def test_every_message_of_a_host_thread_gets_its_roots_fields(self):
        host = {"subject": "evidence:ANN-9\tcriterion a", "kind": "evidence", "label": "Run 3\nlog", "from": FROM,
                "quote": "  two\n lines ", "target": {"type": "text", "key": "two lines"}}
        names = [
            self.write("human", "100-comment-c1.json", comment("c1", **host)),
            self.write("human", "110-comment-p1.json", comment("p1", anchorId="spec-block")),
            self.write("human", "120-edit-e1.json", {"event": "edit", "id": "e1", "supersedes": "c1", "threadId": "c1",
                                                     "text": "Question: and why?", "actor": "human"}),
            self.write("human", "130-handoff-h1.json", {"event": "handoff", "id": "h1", "events": ["c1", "p1", "e1"]}),
            self.write("human", "150-reply-u2.json", {"event": "reply", "id": "u2", "respondsTo": "r1",
                                                      "threadId": "c1", "text": "ok", "actor": "human"}),
            self.write("human", "160-comment-junk.json", "not json"),
        ]
        self.write("agent", "140-reply-r1.json", {"event": "reply", "id": "r1", "respondsTo": "e1", "actor": "agent"})
        fields = ("evidence:ANN-9 criterion a", "evidence", "Run 3 log", FROM, "two lines")
        self.assertEqual(spool.host_rows(str(self.review), names), [
            ("100-comment-c1.json", *fields), ("120-edit-e1.json", *fields), ("150-reply-u2.json", *fields)])

    def test_a_spec_pages_spool_has_no_host_rows(self):
        names = [self.write("human", "100-comment-p1.json", comment("p1", anchorId="spec-block", quote="q")),
                 self.write("human", "110-handoff-h1.json", {"event": "handoff", "id": "h1", "events": ["p1"]})]
        self.assertEqual(spool.host_rows(str(self.review), names), [])

    def test_the_owner_and_the_service_read_a_spool_with_one_reader(self):
        """spool.read_events is the owner's read and the service's, which gives its collection root: a dot file is no
        event, and a FIFO or a link where an event should be refuses the read, never blocking or leaking."""
        self.write("human", "100-comment-c1.json", comment("c1", subject=TURN))
        self.write("human", ".100-comment-c0.json", comment("c0", subject=TURN))
        review, root = str(self.review), str(self.review.parent)
        names = [("human", "100-comment-c1.json")]
        self.assertEqual([(e["actor"], e["name"]) for e in spool.read_events(review)], names)
        self.assertEqual([(e["actor"], e["name"]) for e in serve._read_spool_events(review, root)], names)
        self.assertIsNone(serve._read_spool_events(review, review + "/human"), "a spool outside its root")
        planted = self.review / "agent" / "200-reply-r1.json"
        os.mkfifo(planted)
        with self.assertRaises(spool.UnsafeSpool):
            spool.read_events(review)
        self.assertIsNone(serve._read_spool_events(review, root))
        planted.unlink()
        planted.symlink_to(self.review / "human" / "100-comment-c1.json")
        with self.assertRaises(OSError):
            spool.read_events(review)
        self.assertIsNone(serve._read_spool_events(review, root))

    def test_the_cli_prints_host_lines_for_the_names_it_reads(self):
        name = self.write("human", "100-comment-c1.json", comment("c1", subject=TURN, kind="agent turn", label=None))
        done = subprocess.run([sys.executable, str(ASSETS / "spool.py"), "host", str(self.review)],
                              input=name + "\n", text=True, capture_output=True, check=True)
        self.assertEqual(done.stdout, "host\t%s\t%s\tagent turn\t\t\t\n" % (name, TURN))


class HostedThreadsScanTest(unittest.TestCase):
    """The scan takes a scope's hosted threads, a spool with no spec, as it takes a spec's spool."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="spec-chat-hosted-scan-")
        self.addCleanup(temp.cleanup)
        self.hosted = Path(temp.name) / "comments" / "ops"
        self.hosted.mkdir(parents=True)

    def test_no_hand_off_is_an_empty_backlog(self):
        self.assertEqual(scan(self.hosted), (3, [], []))
        (self.hosted / "human").mkdir()
        (self.hosted / "human" / "100-comment-c1.json").write_text(json.dumps(comment("c1", subject=TURN)))
        self.assertEqual(scan(self.hosted), (3, [], []))
        self.assertFalse((self.hosted / ".cursor-owner").exists(), "the scan never writes the cursor")

    def test_each_cursor_is_its_own(self):
        (self.hosted / "human").mkdir()
        for name, body in (("100-comment-c1.json", comment("c1", subject=TURN, kind="agent turn")),
                           ("110-handoff-h1.json", {"event": "handoff", "id": "h1", "events": ["c1"]})):
            (self.hosted / "human" / name).write_text(json.dumps(body))
        (self.hosted / ".cursor-owner").write_text("100-comment-c1.json\n110-handoff-h1.json\n")
        self.assertEqual(scan(self.hosted)[0], 3)
        code, rows, _ = scan(self.hosted, ".cursor-other")
        self.assertEqual((code, [row[1] for row in rows]), (0, ["100-comment-c1.json", "110-handoff-h1.json"]))


if __name__ == "__main__":
    unittest.main()
