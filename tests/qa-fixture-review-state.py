"""QA review-state collections (tests/fixtures/qa/spools.py): each seeded spool resolves
through place.py and spool.py to the state its review-state criterion's Given names."""

import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "qa"
ASSETS = ROOT / "skill" / "review-spec" / "assets"
sys.path.insert(0, str(ASSETS))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


place = load("qa_place", ASSETS / "place.py")
spool = load("qa_spool", ASSETS / "spool.py")
spools = load("qa_spools", FIXTURE / "spools.py")
HEAD = (FIXTURE / "head" / "docs" / "specs" / "report.spec.html").read_text(encoding="utf-8")


class Site:
    """One run of the real reset.sh into a temporary QA_ROOT."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        env = {**os.environ, "QA_ROOT": str(self.root), "QA_FIXTURE": str(FIXTURE), "QA_REPO_SPEC_CHAT": str(ROOT)}
        subprocess.run(["sh", str(FIXTURE / "reset.sh")], check=True, env=env)

    def spec(self, collection):
        return str(self.root / "site" / collection / spools.SPEC)

    def events(self, collection):
        human = Path(self.spec(collection) + ".review") / "human"
        return [{"name": name, "body": json.loads((human / name).read_text())} for name in sorted(os.listdir(human))]

    def places(self, collection):
        return {e["body"]["id"]: e["place"] for e in place.resolve_events(self.spec(collection), self.events(collection))}


class ReviewStateCollections(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = Site()

    @classmethod
    def tearDownClass(cls):
        cls.site.tmp.cleanup()

    def current(self, collection):
        return Path(self.site.spec(collection)).read_text(encoding="utf-8")

    def test_every_collection_is_reset_registered_and_navigated(self):
        reset = (FIXTURE / "reset.sh").read_text()
        registry = (self.site.root / "serve" / "registry.toml").read_text()
        nav = tomllib.loads((ROOT / "qa.toml").read_text())["nav"]
        hinted = {hint["path"].split("/")[1] for hint in nav.values()}
        for collection in spools.COLLECTIONS:
            self.assertIn(collection, reset)
            self.assertIn('"spec:%s::docs/specs/report.spec.html"' % collection, registry)
            self.assertIn(collection, hinted)

    def test_seeded_events_name_their_version_and_file(self):
        version = hashlib.sha256(HEAD.encode("utf-8")).hexdigest()
        for collection in spools.COLLECTIONS:
            review = self.site.spec(collection) + ".review"
            for event in self.site.events(collection) if os.path.isdir(review) else ():
                body = event["body"]
                self.assertEqual(event["name"].split("-", 1)[1], "%s-%s.json" % (body["event"], body["id"]))
                self.assertEqual(body["version"], version)
                self.assertEqual(place.read_version(review, version), HEAD)
                self.assertEqual(body["actor"], "human")
                self.assertTrue(body["browser"] and body["author"])

    def test_anchor_moved_keeps_the_same_sentence(self):
        self.assertIn(spools.INSERTED, self.current("anchor-moved"))
        mark = self.site.places("anchor-moved")["qa-sentence"]
        self.assertEqual((mark["state"], mark["anchorId"], mark["quote"]), ("kept", "report-export", spools.SENTENCE))
        self.assertEqual(self.current("anchor-moved")[mark["start"]:mark["end"]], spools.SENTENCE)

    def test_anchor_changed_sits_on_remaining_text(self):
        mark = self.site.places("anchor-changed")["qa-sentence"]
        self.assertEqual((mark["state"], mark["anchorId"]), ("changed", "report-export"))
        self.assertTrue(mark["quote"].startswith("The export button downloads a CSV of"))
        self.assertNotEqual(mark["quote"], spools.SENTENCE)

    def test_anchor_gone_falls_to_nearest_surviving_block(self):
        self.assertNotIn(spools.SENTENCE, self.current("anchor-gone"))
        mark = self.site.places("anchor-gone")["qa-sentence"]
        self.assertEqual((mark["state"], mark["anchorId"], mark["start"]), ("gone", "acceptance", None))

    def test_agent_scan_batch_holds_the_rewritten_handed_off_comment(self):
        review = self.site.spec("agent-scan") + ".review"
        batch = spool.handoff_batch(review, set())
        self.assertEqual([name.split("-", 2)[1] for name in batch], ["comment", "handoff"])
        self.assertEqual(self.site.places("agent-scan")["qa-sentence"]["state"], "changed")

    def test_stale_page_edit_lands_on_save_and_the_mark_follows(self):
        spec = self.site.spec("stale-page")
        self.assertEqual(self.current("stale-page"), HEAD)
        self.assertFalse(os.path.exists(spec + ".review"))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env = {**os.environ, "HOME": str(self.site.root / "home"), "XDG_STATE_HOME": str(self.site.root / "state"),
               "XDG_CONFIG_HOME": str(self.site.root / "config")}
        env.pop("HERDR_SOCKET_PATH", None)
        server = subprocess.Popen([sys.executable, str(FIXTURE / "serve.py"), str(self.site.root / "serve" / "registry.toml"), str(port)],
                                  cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = "http://127.0.0.1:%d" % port
        try:
            for _ in range(100):
                try:
                    with urllib.request.urlopen(base + "/stale-page/docs/specs/report.spec.html") as response:
                        etag = response.headers["ETag"].strip('"')
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                self.fail("serve.py did not start")
            self.assertEqual(etag, hashlib.sha256(HEAD.encode("utf-8")).hexdigest())
            body = {"id": "qa-stale", "event": "comment", "actor": "human", "createdAt": "2026-10-01T00:00:00Z",
                    "schemaVersion": 1, "browser": "qa-capture", "author": "Lychee", "version": etag,
                    "anchorId": "report-export", "target": {"type": "text", "key": spools.SENTENCE},
                    "quote": spools.SENTENCE, "text": "Name the columns."}
            name = "%d-comment-qa-stale.json" % time.time_ns()
            query = "dir=/stale-page/docs/specs/report.spec.html.review&actor=human&name=" + name
            request = urllib.request.Request(base + "/api/events?" + query, data=json.dumps(body).encode(), method="POST")
            with urllib.request.urlopen(request) as response:
                self.assertEqual(json.load(response)["name"], name)
            self.assertIn(spools.INSERTED, self.current("stale-page"))
            with urllib.request.urlopen(base + "/api/events?dir=/stale-page/docs/specs/report.spec.html.review") as response:
                events = json.load(response)
            mark = next(e["place"] for e in events if e["body"]["id"] == "qa-stale")
            self.assertEqual((mark["state"], mark["anchorId"], mark["quote"]), ("kept", "report-export", spools.SENTENCE))
        finally:
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
