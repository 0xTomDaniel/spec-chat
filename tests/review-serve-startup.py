"""Review server start prints its URL without waiting on the Jev record store (review-service #service-restart)."""
import contextlib
import importlib.util
import io
import json
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


class SlowStoreStartTest(unittest.TestCase):
    """A store read held open stands in for a large records.jsonl: start must not wait on it."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name) / "spec-chat"
        (self.state / "jev").mkdir(parents=True)
        record = {"cache_key": "sha256:held", "outcome": "ok", "record_id": "judgment-held"}
        (self.state / "jev" / "records.jsonl").write_text(json.dumps(record) + "\n")
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        read_text = Path.read_text

        def held(path, *args, **kwargs):
            if path.name == "records.jsonl":
                self.release.wait(10)
            return read_text(path, *args, **kwargs)

        patcher = patch.object(Path, "read_text", held)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict("os.environ", {"XDG_STATE_HOME": str(self.state.parent)})
        env.start()
        self.addCleanup(env.stop)

    def test_service_returns_before_store_loads_and_store_answers_after(self):
        built = []
        thread = threading.Thread(target=lambda: built.append(jev.JevService()), daemon=True)
        thread.start()
        thread.join(2)
        self.assertTrue(built, "JevService waited on the record store")
        self.release.set()
        self.assertEqual(built[0].seam.store.get("sha256:held")["record_id"], "judgment-held")
        built[0].stop()

    def test_server_prints_url_while_store_is_still_loading(self):
        out = io.StringIO()
        servers = []

        def no_serve(server, *args):
            servers.append(server)

        def run():
            with patch.object(serve.ReviewThreadingHTTPServer, "serve_forever", no_serve):
                serve.main([str(ROOT / "docs" / "specs"), "--port", "0"])

        with contextlib.redirect_stdout(out):
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            thread.join(2)
        self.assertIn("spec-chat review-serve on http://", out.getvalue())
        self.release.set()
        thread.join(5)
        self.assertEqual(len(servers), 1)


if __name__ == "__main__":
    unittest.main()
