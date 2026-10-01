"""Review server start prints its URL without reading the Jev record store (review-service #acceptance-restart)."""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
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
    """Start never reads records.jsonl, however large; the first store use reads it once."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name) / "spec-chat"
        (self.state / "jev").mkdir(parents=True)
        record = {"cache_key": "sha256:held", "outcome": "ok", "record_id": "judgment-held"}
        (self.state / "jev" / "records.jsonl").write_text(json.dumps(record) + "\n")
        self.reads = []
        read_text = Path.read_text

        def counted(path, *args, **kwargs):
            if path.name == "records.jsonl":
                self.reads.append(path)
            return read_text(path, *args, **kwargs)

        patcher = patch.object(Path, "read_text", counted)
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
        self.assertEqual(len(self.reads), 1)

    def test_server_prints_url_without_reading_store(self):
        out = io.StringIO()
        servers = []
        with patch.object(serve.ReviewThreadingHTTPServer, "serve_forever", lambda server, *args: servers.append(server)), \
                contextlib.redirect_stdout(out):
            serve.main([str(ROOT / "docs" / "specs"), "--port", "0"])
        self.assertIn("spec-chat review-serve on http://", out.getvalue())
        self.assertEqual(len(servers), 1)
        self.assertEqual(self.reads, [], "server start read the record store")


if __name__ == "__main__":
    unittest.main()
