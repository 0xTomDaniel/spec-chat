"""Steady review service (review-service #steady, #proof-steady-seam): status route, connections, reads that
reparse nothing unchanged, a board refresh that stands while its inputs do, and an idle service that reads nothing."""

import builtins
import http.client
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "skill" / "review-spec" / "assets"
sys.path.insert(0, str(ASSETS))
jev = importlib.import_module("jev")
place = importlib.import_module("place")
_serve_spec = importlib.util.spec_from_file_location("review_serve_steady_test", ASSETS / "review-serve.py")
serve = importlib.util.module_from_spec(_serve_spec)
_serve_spec.loader.exec_module(serve)

SPEC = "docs/specs/board.spec.html"
QUESTIONS = ROOT / "skill" / "review-spec" / "assets" / "jev"
DRAFT = {"contradicts", "oversteps", "overlaps"}


def settled_answer(kind, labels):
    """A confident answer each question set knows: no change of behavior, else no, else its first label."""
    for label in ("no-behavior-change", "no"):
        if label in labels:
            return label, 0.9
    return labels[0], 0.9


class FakeProvider:
    """Answers every question at once; counts calls."""

    def __init__(self, answer=None):
        self.answer = answer
        self.calls = []
        self.lock = threading.Lock()

    def decide(self, payload):
        with self.lock:
            self.calls.append(payload)
        kind = next(iter(payload["questions"]))
        labels = list(payload["questions"][kind]["criteria"])
        choice, confidence = self.answer(kind, labels) if self.answer else settled_answer(kind, labels)
        return {"answers": {kind: {"choice": choice, "confidence": confidence}}}


def git(root, *args):
    return subprocess.check_output(("git", "-C", str(root), *args), text=True).strip()


def section(*clauses):
    leaves = "".join(f'<p data-anchor="{anchor}">{text}</p>' for anchor, text in clauses)
    return f'<html><head><title>Board</title></head><body><section data-anchor="root"><h2>Board</h2>{leaves}</section></body></html>\n'


def repo(directory, before, after):
    """A worktree whose spec is `before` at base and origin/HEAD, `after` on disk."""
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    path = root / SPEC
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(before, encoding="utf-8")
    git(root, "add", SPEC)
    git(root, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD")
    git(root, "update-ref", "refs/remotes/origin/main", base)
    git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    path.write_text(after, encoding="utf-8")
    return root, base


def row(root, slug, base):
    return {"id": f"spec:{slug}:aa::{SPEC}", "slug": slug, "project": "p", "root": str(root),
            "narrow_root": str(Path(root) / "docs"), "spec": SPEC, "spec_file": str(Path(root) / SPEC),
            "path": f"{slug}/{SPEC}", "base": base, "owner": "w1:p1", "cursor_name": ".cursor-owner"}


def history(spec_file, comments):
    """A spool with review history: comments, each handed off, all consumed by the owner's cursor."""
    review = Path(str(spec_file) + ".review")
    (review / "human").mkdir(parents=True, exist_ok=True)
    names = []
    for index in range(comments):
        stamp = 1000 + 2 * index
        comment = {"id": f"c{index}", "event": "comment", "actor": "human", "anchorId": "a", "text": f"Note {index}.",
                   "target": {"type": "block"}}
        handoff = {"id": f"h{index}", "event": "handoff", "actor": "human", "events": [f"c{index}"]}
        for name, body in ((f"{stamp}-comment-c{index}.json", comment), (f"{stamp + 1}-handoff-h{index}.json", handoff)):
            (review / "human" / name).write_text(json.dumps(body), encoding="utf-8")
            names.append(name)
    (review / ".cursor-owner").write_text("".join(name + "\n" for name in names), encoding="utf-8")
    return review


def aged(*paths):
    """File times well in the past, as on a box where nothing was written for a while."""
    past = time.time() - 60
    for path in paths:
        os.utime(path, (past, past))


class Steady(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name).resolve()
        self.services, self.servers = [], []

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for service in self.services:
            service.stop()
        self.tmp.cleanup()

    def jev_service(self, provider=None):
        service = jev.JevService(state_dir=self.dir / "state", provider=provider or FakeProvider(), api_key="fake",
                                 question_dirs=[QUESTIONS])
        self.services.append(service)
        return service

    def lanes(self):
        """Two lanes of one project, each changing two clauses against target main."""
        keep = section(("a", "Keep."), ("b", "Keep too."))
        x, base_x = repo(self.dir / "x", keep, section(("a", "Cards sort by age."), ("b", "The board edits specs.")))
        y, base_y = repo(self.dir / "y", keep, section(("a", "Cards sort by title."), ("b", "Only owners edit specs.")))
        return [row(x, "ann1", base_x), row(y, "ann2", base_y)]

    def server(self, rows, service):
        server = serve.ReviewThreadingHTTPServer(("127.0.0.1", 0), serve.MountHandler)
        server.mount_state = serve.MountState(rows)
        server.state_dir = str(self.dir / "providers")
        server.jev = service
        server.wake_controller = serve.WakeController(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        return server

    def status(self, server):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:%d/api/status" % server.server_port, timeout=10) as response:
            return json.loads(response.read())

    def board(self, service, rows):
        answer = service.board(rows)
        self.assertTrue(service._board_idle.wait(20))
        return answer

    def settle_asks(self, service):
        deadline = time.monotonic() + 20
        while service.queue_counts() != (0, 0) or service._asking:
            self.assertLess(time.monotonic(), deadline, "asks never settled")
            time.sleep(0.02)


class StatusRouteTest(Steady):
    def test_status_reports_memory_connections_and_jev_queue(self):
        """#proof-steady-first-test, #acceptance-status."""
        service = self.jev_service()
        server = self.server([], service)
        status = self.status(server)
        self.assertEqual(set(status), {"rss_bytes", "connections", "jev"})
        self.assertGreater(status["rss_bytes"], 1 << 20)
        self.assertEqual(status["connections"], 1)  # the one asking
        self.assertEqual(status["jev"], {"waiting": 0, "running": 0})
        with patch.object(service, "queue_counts", lambda: (7, 3)):
            self.assertEqual(self.status(server)["jev"], {"waiting": 7, "running": 3})

    def test_status_reads_no_store(self):
        service = self.jev_service()
        server = self.server(self.lanes(), service)
        opened = []
        real_open = builtins.open

        def counted(file, *args, **kwargs):
            if str(file).startswith(str(self.dir)):
                opened.append(str(file))
            return real_open(file, *args, **kwargs)

        with patch.object(builtins, "open", counted), patch.object(io, "open", counted):
            self.status(server)
        self.assertEqual(opened, [])


class ConnectionsTest(Steady):
    def test_closed_pages_leave_only_the_asking_connection(self):
        """#acceptance-steady-closed: pages whose tabs closed without closing their keep-alive connections; after
        the handler's idle timeout (shortened here) the status route shows one connection, its own."""
        service = self.jev_service()
        server = self.server([], service)
        pages = []
        with patch.object(serve.MountHandler, "timeout", 1.0):
            for _ in range(3):
                page = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
                page.request("GET", "/api/status")
                page.getresponse().read()
                pages.append(page)  # left open: the page never closes it
            self.assertEqual(self.status(server)["connections"], 4)
            deadline = time.monotonic() + 10
            while self.status(server)["connections"] != 1:
                self.assertLess(time.monotonic(), deadline, "idle connections stayed open")
                time.sleep(0.1)
        for page in pages:
            page.close()


class ReadsTest(Steady):
    def read(self, service, rows, index):
        mount = rows[index]
        return service.response(mount, mount["spec_file"], mount["spec"], mount["base"], [], "", rows)

    def test_repeat_read_reparses_and_rekeys_nothing(self):
        """#steady-bound: a read of unchanged specs parses no spec and computes no question key again: each row's
        changed clauses, their context, and every question's key are built once per content."""
        rows = self.lanes()
        service = self.jev_service()
        for _ in range(2):
            first = [self.read(service, rows, index) for index in (0, 1)]
            self.settle_asks(service)
        self.assertTrue(service._lane_questions(rows))
        parses, keys = [], []
        feed, canonical = jev._AnchorParser.feed, jev._canonical
        with patch.object(jev._AnchorParser, "feed", lambda parser, data: (parses.append(1), feed(parser, data))[1]), \
                patch.object(jev, "_canonical", lambda value: (keys.append(1), canonical(value))[1]):
            again = [self.read(service, rows, index) for index in (0, 1)]
        self.assertEqual(again, first)
        self.assertEqual(parses, [], "a read parsed an unchanged spec")
        self.assertEqual(keys, [], "a read computed a question key again")

    def test_kept_key_is_the_computed_key(self):
        rows = self.lanes()
        service = self.jev_service()
        chains = service._lane_questions(rows)
        self.assertTrue(chains)
        for step in (step for chain in chains for step in chain["chain"]):
            fresh = {name: value for name, value in step.items() if name != jev.QUESTION_KEY}
            self.assertEqual(service.seam.key(step), service.seam.key(fresh))


class BoardTest(Steady):
    def test_unchanged_inputs_skip_the_board_pass(self):
        """#cross-lane-async with #steady-bound: a settled board recomputes nothing until a row's spec text or
        target main changes."""
        rows = self.lanes()
        service = self.jev_service()
        self.board(service, rows)
        settled = self.board(service, rows)
        self.assertEqual(len(settled["rows"]), 2)
        passes = []
        real = service._board_from_records
        with patch.object(service, "_board_from_records", lambda *args: (passes.append(1), real(*args))[1]):
            for _ in range(5):
                self.assertEqual(self.board(service, rows), settled)
            self.assertEqual(passes, [])
            Path(rows[0]["spec_file"]).write_text(section(("a", "Cards sort by size."), ("b", "The board edits specs.")),
                                                  encoding="utf-8")
            self.board(service, rows)
            self.assertTrue(passes)
            passes.clear()
            git(rows[1]["root"], "update-ref", "refs/remotes/origin/main",
                git(rows[1]["root"], "-c", "user.name=T", "-c", "user.email=t@example.com",
                    "commit-tree", "-m", "main moved", "-p", rows[1]["base"], rows[1]["base"] + "^{tree}"))
            self.board(service, rows)
            self.assertTrue(passes, "a moved target main did not refresh the board")

    def test_board_reads_each_base_once_through_kept(self):
        """The material questions come from the kept builder and the base text from _at: an edit reads no base again."""
        rows = self.lanes()[:1]
        service = self.jev_service()
        shows = []
        run = subprocess.run

        def counted(argv, *args, **kwargs):
            if argv and argv[0] == "git" and "show" in argv:
                shows.append(argv)
            return run(argv, *args, **kwargs)

        with patch.object(jev.subprocess, "run", counted):
            self.board(service, rows)
            Path(rows[0]["spec_file"]).write_text(section(("a", "Cards sort by size."), ("b", "Edited.")), encoding="utf-8")
            self.board(service, rows)
        self.assertEqual(len(shows), 1, shows)

    def test_unfinished_answer_is_asked_again_after_its_pause(self):
        """A pass with an answer still unavailable does not stand: once its pause ends the board asks again."""
        failing = [True]

        def answer(kind, labels):
            if failing[0]:
                raise OSError("jev down")
            return settled_answer(kind, labels)

        rows = self.lanes()[:1]
        provider = FakeProvider(answer)
        service = self.jev_service(provider)
        with patch.object(jev, "RETRY_PAUSE", 0):
            self.board(service, rows)
            self.assertEqual(self.board(service, rows)["rows"][0]["material"], "unknown")
            failing[0] = False
            self.board(service, rows)
        self.assertEqual(self.board(service, rows)["rows"][0]["material"], "no")


class IdleTest(Steady):
    def test_an_idle_hour_reads_no_spec_spool_or_record(self):
        """#acceptance-steady-idle: registered specs with review history and Jev answers, no page open, no hand-off
        waiting: an hour of the service's own work reads no spec, spool event, or Jev record, and asks Jev nothing."""
        rows = self.lanes()
        provider = FakeProvider()
        service = self.jev_service(provider)
        reviews = [history(item["spec_file"], 40) for item in rows]
        self.board(service, rows)  # Jev answers on record
        self.settle_asks(service)
        server = self.server(rows, service)
        aged(*(path for review in reviews for path in (review / "human", review / ".cursor-owner")))
        asked = len(provider.calls)
        opened, records, spools, launched = [], [], [], []
        real_open, real_os_open, lines, spool_read = builtins.open, os.open, jev._lines, serve._read_spool_events
        popen = subprocess.Popen

        def counted(file, *args, **kwargs):
            if str(file).startswith(str(self.dir)):
                opened.append(str(file))
            return real_open(file, *args, **kwargs)

        def counted_os(file, *args, **kwargs):
            if isinstance(file, str) and not os.path.isabs(file):  # relative to a spool folder descriptor
                opened.append(file)
            elif str(file).startswith(str(self.dir)):
                opened.append(str(file))
            return real_os_open(file, *args, **kwargs)

        class Counted(popen):
            def __init__(self, argv, *args, **kwargs):
                launched.append(argv)
                super().__init__(argv, *args, **kwargs)

        with patch.object(builtins, "open", counted), patch.object(io, "open", counted), \
                patch.object(os, "open", counted_os), patch.object(subprocess, "Popen", Counted), \
                patch.object(jev, "_lines", lambda path: (records.append(path), lines(path))[1]), \
                patch.object(serve, "_read_spool_events", lambda *a: (spools.append(a), spool_read(*a))[1]):
            for _ in range(3600 // serve.WAKE_POLL_SECONDS):
                server.wake_controller.poll()
        cursors = [path for path in opened if path.endswith(".cursor-owner")]
        self.assertLessEqual(len(cursors), len(rows), "the cursor was read again with nothing changed")
        events = [path for path in opened if path.endswith(".json")]
        specs = [path for path in opened if path.endswith(".spec.html")]
        self.assertEqual((events, specs, records, spools, launched), ([], [], [], [], []))
        self.assertEqual(len(provider.calls), asked)
        self.assertEqual(server.wake_controller.states, {})

    def test_a_new_hand_off_is_still_noticed(self):
        rows = self.lanes()[:1]
        review = history(rows[0]["spec_file"], 2)
        service = self.jev_service()
        server = self.server(rows, service)
        aged(review / "human", review / ".cursor-owner")
        controller = server.wake_controller
        controller.poll()
        self.assertEqual(controller.batches[rows[0]["id"]][1], ())
        (review / "human" / "2000-comment-n.json").write_text(json.dumps(
            {"id": "n", "event": "comment", "actor": "human", "anchorId": "a", "text": "New."}), encoding="utf-8")
        (review / "human" / "2001-handoff-hn.json").write_text(json.dumps(
            {"id": "hn", "event": "handoff", "actor": "human", "events": ["n"]}), encoding="utf-8")
        controller.poll()
        self.assertEqual(controller.batches[rows[0]["id"]][1], ("2000-comment-n.json", "2001-handoff-hn.json"))


class MemoryTest(Steady):
    ROUNDS = 8

    def test_rounds_of_pages_hold_memory_within_a_fixed_bound(self):
        """#acceptance-steady-memory: pages open, poll, read Jev, and close, round after round; the service's
        memory after the last round stays within a fixed bound of its memory after the first, and what it holds
        per spec, page, and connection is the same after every round."""
        rows = self.lanes()
        for item in rows:
            history(item["spec_file"], 20)
        service = self.jev_service()
        server = self.server(rows, service)
        port = server.server_port

        def round_():
            pages = []
            for item in rows:
                page = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
                for method, path in (("GET", "/" + item["path"]), ("HEAD", "/" + item["path"]),
                                     ("GET", "/api/events?dir=" + item["path"] + ".review"),
                                     ("GET", "/api/jev?path=%s&base=%s" % (item["path"], item["base"])),
                                     ("GET", "/api/jev?path=%s&base=%s&view=reading" % (item["path"], item["base"])),
                                     ("GET", "/api/jev/board"), ("GET", "/")):
                    page.request(method, path)
                    response = page.getresponse()
                    response.read()
                    self.assertEqual(response.status, 200, path)
                pages.append(page)
            for page in pages:
                page.close()
            self.settle_asks(service)
            deadline = time.monotonic() + 10
            while self.status(server)["connections"] != 1:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.05)

        def held():
            return (len(service._kept.values), service._kept.used, len(place._RESOLVERS), len(serve._ROW_CACHE),
                    len(serve._THREAD_CACHE), service.queue_counts(), len(service._marks))

        round_()
        round_()
        first, shape = self.status(server)["rss_bytes"], held()
        for _ in range(self.ROUNDS):
            round_()
        last = self.status(server)["rss_bytes"]
        self.assertEqual(held(), shape)
        self.assertLess(last - first, 16 << 20, (first, last))


if __name__ == "__main__":
    unittest.main()
