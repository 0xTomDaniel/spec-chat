"""Review server start prints its URL and answers without reading the Jev record store; compaction reads it after,
in the background (review-service #acceptance-restart, jev-suggestions #record-held, #record-compact); each line it
writes to its log starts with its time (review-service #service-log). Its stop ends Jev's claude runs."""
import contextlib
import importlib.util
import io
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "skill" / "review-spec" / "assets"
sys.path.insert(0, str(ASSETS))
spec = importlib.util.spec_from_file_location("review_serve_startup_test", ASSETS / "review-serve.py")
serve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(serve)
jev = sys.modules["jev"]


class StoreUnreadAtStartTest(unittest.TestCase):
    """Start reads no record before it answers, however large the store; the first lookup reads only its key's file."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name) / "spec-chat"
        (self.state / "jev").mkdir(parents=True)
        contradicts = jev.load_question_sets()["contradicts"]
        self.version = {"id": contradicts.id, "version": contradicts.version}
        store = jev.JudgmentStore(self.state / "jev" / "records.jsonl")
        store.append({"cache_key": "sha256:held", "outcome": "ok", "record_id": "judgment-held",
                      "question_set": self.version})
        # A retired version's answer the ledger cites: compaction reads its file after start.
        store.append({"cache_key": "sha256:retired", "outcome": "ok", "record_id": "judgment-retired",
                      "question_set": {"id": contradicts.id, "version": contradicts.version - 1}})
        jev.WarningLedger(self.state / "jev" / "ledger.jsonl").append(
            {"project": "p", "spec": "a.spec.html", "anchor": "a", "kind": "contradicts", "outcome": "dismissed",
             "judgment": "judgment-retired", "by": "owner", "reason": "r"})
        self.reads = []
        self.compacting = threading.Event()
        lines = jev._lines

        def counted(path):
            if threading.current_thread().name == "spec-chat-jev-compact":
                self.compacting.wait(10)  # held until the server answers, to show nothing waits for it
            else:
                self.reads.append(path.name)
            return lines(path)

        patcher = patch.object(jev, "_lines", counted)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict("os.environ", {"XDG_STATE_HOME": str(self.state.parent)})
        env.start()
        self.addCleanup(env.stop)

    def test_service_builds_without_reading_store_and_store_answers_after(self):
        service = jev.JevService()
        self.addCleanup(service.stop)
        self.assertEqual(self.reads, [], "JevService read the record store at construction")
        self.assertEqual(service.seam.store.get("sha256:held")["record_id"], "judgment-held")
        self.assertEqual(set(self.reads), {service.seam.store._shard("sha256:held") + ".jsonl"},
                         "the first use read more than the key's own file")

    def test_server_prints_url_without_reading_store(self):
        out = io.StringIO()
        servers = []

        def serving(server, *args):
            servers.append((server, list(self.reads), server.jev._compaction.is_alive()))
            self.compacting.set()

        with patch.object(serve.ReviewThreadingHTTPServer, "serve_forever", serving), contextlib.redirect_stdout(out):
            serve.main([str(ROOT / "docs" / "specs"), "--port", "0"])
        self.assertIn("spec-chat review-serve on http://", out.getvalue())
        self.assertEqual(len(servers), 1)
        server, reads, compacting = servers[0]
        self.assertEqual(reads, [], "server start read the record store")
        self.assertTrue(compacting, "compaction did not start in the background")
        self.assertEqual(self.reads, [], "server start read the record store")


class LogLineTimeTest(unittest.TestCase):
    """review-service #service-log: each line the service writes to its log, a traceback's lines included, starts
    with the UTC instant the line began, to the millisecond, ending in Z, as a Jev record's time (jev-contract
    #core-time). The service's own start is tests/review-host.py's (a start appends to the log)."""

    def test_each_line_starts_with_the_instant_it_began(self):
        out = io.StringIO()
        stamped = serve._Stamped(out)
        times = iter("2026-10-30T08:00:00.00%dZ" % at for at in range(1, 9))
        with patch.object(serve, "_utc_now", lambda: next(times)):
            print("review-serve: one", file=stamped, flush=True)
            stamped.write("review-serve: begun, ")
            stamped.write('then ended\nTraceback (most recent call last):\n  File "x", line 1\n')
        self.assertEqual(out.getvalue(), "2026-10-30T08:00:00.001Z review-serve: one\n"
                                         "2026-10-30T08:00:00.002Z review-serve: begun, then ended\n"
                                         "2026-10-30T08:00:00.003Z Traceback (most recent call last):\n"
                                         '2026-10-30T08:00:00.004Z   File "x", line 1\n')

    def test_the_instant_is_utc_to_the_millisecond(self):
        out = io.StringIO()
        print("review-serve: now", file=serve._Stamped(out))
        self.assertRegex(out.getvalue(), r"\A\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z review-serve: now\n\Z")


# A review service whose Jev asks the general LLM as it starts: the ask holds its claude run (review-serve.py, then
# its arguments).
HELD_ASK = r"""
import importlib.util, sys, threading
spec = importlib.util.spec_from_file_location("review_serve_held_ask", sys.argv[1])
serve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(serve)


class Held(serve.JevService):
    def start(self):
        super().start()
        seam = self.seam
        threading.Thread(target=seam._complete, daemon=True, args=(
            seam._fallback_provider(seam.provider()), {"messages": [{"role": "user", "content": "q"}]})).start()


serve.JevService = Held
sys.exit(serve.main(sys.argv[2:]))
"""


class JevRunsEndWithServiceTest(unittest.TestCase):
    """A stop, a SIGTERM's or main's own, ends each Jev claude run in flight, its whole process group, and starts none
    after: no run spends model tokens on an answer nothing records."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        (self.work / "bin").mkdir()
        claude = self.work / "bin" / "claude"  # a stand-in that starts a child, writes both pids, and waits
        claude.write_text('#!/bin/sh\nsleep 30 &\necho "$$ $!" > %s/pids.tmp && mv %s/pids.tmp %s/pids\nwait\n'
                          % ((self.work,) * 3))
        claude.chmod(0o755)
        self.env = {"PATH": "%s%s%s" % (self.work / "bin", os.pathsep, os.environ["PATH"]),
                    "XDG_STATE_HOME": str(self.work / "state")}
        env = patch.dict("os.environ", self.env)
        env.start()
        self.addCleanup(env.stop)

    def pids(self, timeout=10):
        """The held run's claude and its child, once it started: each is killed at cleanup, should it run on."""
        deadline = time.monotonic() + timeout
        path = self.work / "pids"
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        pids = [int(pid) for pid in path.read_text().split()]
        for pid in pids:
            self.addCleanup(self.kill, pid)
        path.unlink()
        return pids

    @staticmethod
    def kill(pid):
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)

    @staticmethod
    def gone(pid, timeout=5):
        """The process ended (or is only a zombie its parent has not yet reaped)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with open("/proc/%d/stat" % pid) as stat:
                    if stat.read().rsplit(")", 1)[1].split()[0] == "Z":
                        return True
            except OSError:
                return True
            time.sleep(0.02)
        return False

    def test_a_stop_ends_the_claude_run_in_flight_and_starts_none_after(self):
        asks, failures = [], []

        def ask(seam):
            try:
                seam._complete(seam._fallback_provider(seam.provider()), {"messages": [{"role": "user", "content": "q"}]})
            except RuntimeError as exc:
                failures.append(exc)

        def serving(server, *args):  # serves until its run is held, then main stops
            asks.append(threading.Thread(target=ask, args=(server.jev.seam,)))
            asks[-1].start()
            self.held = self.pids()
            self.jev = server.jev

        started = time.monotonic()
        with patch.object(serve.ReviewThreadingHTTPServer, "serve_forever", serving), \
                contextlib.redirect_stdout(io.StringIO()):
            serve.main([str(ROOT / "docs" / "specs"), "--port", "0"])
        asks[0].join(5)
        self.assertLess(time.monotonic() - started, 10, "the stop waited for the claude run")
        for pid in self.held:
            self.assertTrue(self.gone(pid), "a claude run, or what it started, ran on after the stop")
        self.assertEqual(len(failures), 1, "the ended run is one failed call")
        ask(self.jev.seam)
        self.assertEqual(len(failures), 2, "an ask after the stop fails")
        self.assertFalse((self.work / "pids").exists(), "a claude run started after the stop")

    def test_a_sigterm_ends_the_claude_run_in_flight(self):
        """review-host stop and the service manager stop the service with a SIGTERM, which never reaches main's end."""
        with (self.work / "log").open("w") as log:
            process = subprocess.Popen(
                (sys.executable, "-c", HELD_ASK, str(ASSETS / "review-serve.py"), str(ROOT / "docs" / "specs"),
                 "--port", "0"), env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), stdout=log, stderr=log)
        self.addCleanup(lambda: process.poll() is None and (process.kill(), process.wait()))
        held = self.pids()
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(10), -signal.SIGTERM, (self.work / "log").read_text())
        for pid in held:
            self.assertTrue(self.gone(pid), "a claude run, or what it started, ran on after the SIGTERM")


if __name__ == "__main__":
    unittest.main()
