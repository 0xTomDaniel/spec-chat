"""Review server start prints its URL and answers without reading the Jev record store; compaction reads it after,
in the background (review-service #acceptance-restart, jev-suggestions #record-held, #record-compact)."""
import contextlib
import importlib.util
import io
import sys
import tempfile
import threading
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


if __name__ == "__main__":
    unittest.main()
