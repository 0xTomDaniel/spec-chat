"""Mark resolver (review-state #anchoring, #model-place): pure functions over fixture spec versions and spools."""

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "skill" / "review-spec" / "assets"
WATCH = ROOT / "skill" / "review-spec" / "scripts" / "watch-specs.sh"
sys.path.insert(0, str(ASSETS))
_spec = importlib.util.spec_from_file_location("review_place_test", ASSETS / "place.py")
place = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(place)

SENTENCE = "Pins stay on the text a reviewer marked."
V1 = (
    '<article class="spec">\n'
    '<section data-anchor="marks">\n'
    '<h2 data-anchor="marks-title">Marks</h2>\n'
    '<p data-anchor="marks-intro">Every mark has a place.\n'
    + SENTENCE + '\n'
    'Threads follow &amp; notify.</p>\n'
    '<p data-anchor="marks-after">Later text.</p>\n'
    '</section>\n'
    '</article>\n'
)
INSERTED = V1.replace('<p data-anchor="marks-intro">', '<p data-anchor="marks-new">A brand new paragraph.</p>\n<p data-anchor="marks-intro">')
REWRITTEN = V1.replace("on the text a reviewer marked", "on the words someone picked")
DELETED = V1.replace(SENTENCE + "\n", "")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def comment(quote=SENTENCE, anchor="marks-intro", version=V1, target=None):
    body = {"id": "u1", "event": "comment", "anchorId": anchor,
            "target": target or {"type": "text", "key": quote}, "quote": quote,
            "text": "keep this", "actor": "human", "schemaVersion": 1}
    if version is not None:
        body["version"] = sha(version)
    return body


class ResolveTest(unittest.TestCase):
    def test_paragraph_inserted_above_keeps_mark_at_shifted_range(self):
        result = place.resolve(comment(), INSERTED, V1)
        start = INSERTED.index(SENTENCE)
        self.assertEqual(result, {"anchorId": "marks-intro", "start": start, "end": start + len(SENTENCE),
                                  "quote": SENTENCE, "state": "kept"})
        self.assertNotEqual(start, V1.index(SENTENCE))

    def test_rewritten_part_is_changed_on_surviving_text(self):
        result = place.resolve(comment(), REWRITTEN, V1)
        self.assertEqual(result["state"], "changed")
        self.assertEqual(result["anchorId"], "marks-intro")
        self.assertTrue(result["quote"].startswith("Pins stay on the "))
        self.assertTrue(result["quote"].endswith("."))
        self.assertEqual(REWRITTEN[result["start"]:result["end"]], result["quote"])

    def test_deleted_sentence_is_gone_on_nearest_surviving_block(self):
        result = place.resolve(comment(), DELETED, V1)
        self.assertEqual(result["state"], "gone")
        self.assertEqual(result["anchorId"], "marks-intro")
        self.assertIsNone(result["quote"])

    def test_gone_block_falls_back_to_surviving_ancestor(self):
        current = V1.replace('<p data-anchor="marks-after">Later text.</p>\n', "")
        result = place.resolve(comment("Later text.", anchor="marks-after"), current, V1)
        self.assertEqual((result["anchorId"], result["state"]), ("marks", "gone"))

    def test_stale_page_comment_lands_on_same_sentence_in_current_spec(self):
        result = place.resolve(comment(version=V1), INSERTED, V1)
        self.assertEqual(INSERTED[result["start"]:result["end"]], SENTENCE)
        self.assertEqual(result["state"], "kept")

    def test_quote_across_entity_and_whitespace_maps_to_source(self):
        quote = "Threads follow & notify."
        result = place.resolve(comment(quote), INSERTED, V1)
        self.assertEqual(result["state"], "kept")
        self.assertEqual(result["quote"], quote)
        self.assertEqual(INSERTED[result["start"]:result["end"]], "Threads follow &amp; notify.")

    def test_legacy_event_without_version_resolves_in_current_spec(self):
        result = place.resolve(comment(version=None), INSERTED, None)
        self.assertEqual((result["state"], result["quote"]), ("kept", SENTENCE))
        missing = place.resolve(comment("No such words", version=None), INSERTED, None)
        self.assertEqual((missing["anchorId"], missing["state"]), ("marks-intro", "gone"))

    def test_element_and_block_targets(self):
        element = place.resolve(comment("", anchor="marks", target={"type": "element", "key": "p[2]"}), INSERTED, V1)
        self.assertEqual((element["anchorId"], element["state"]), ("marks-after", "kept"))
        self.assertTrue(INSERTED[element["start"]:element["end"]].startswith('<p data-anchor="marks-after">'))
        block = comment("", anchor="marks-intro")
        block["target"] = None
        self.assertEqual(place.resolve(block, REWRITTEN, V1)["state"], "changed")
        self.assertEqual(place.resolve(block, INSERTED, V1)["state"], "kept")

    def test_event_without_anchor_has_no_place(self):
        self.assertIsNone(place.resolve({"event": "handoff", "anchorId": "", "target": None}, V1, None))

    def test_same_inputs_same_answer(self):
        self.assertEqual(place.resolve(comment(), REWRITTEN, V1), place.resolve(comment(), REWRITTEN, V1))


def spool(root, current, versions, events):
    spec = root / "docs" / "specs" / "demo.spec.html"
    review = Path(str(spec) + ".review")
    (review / "human").mkdir(parents=True)
    (review / "versions").mkdir()
    spec.write_text(current, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for text in versions:
        (review / "versions" / (sha(text) + ".html")).write_text(text, encoding="utf-8")
    for name, body in events:
        (review / "human" / name).write_text(json.dumps(body), encoding="utf-8")
    return spec, review


class SpoolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        handoff = {"id": "h1", "event": "handoff", "anchorId": "", "target": None, "actor": "human"}
        self.spec, self.review = spool(self.root, REWRITTEN, [V1], [
            ("100-comment-u1.json", comment()), ("200-handoff-h1.json", handoff)])

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_version_is_hash_named_file_or_none(self):
        self.assertEqual(place.read_version(self.review, sha(V1)), V1)
        self.assertIsNone(place.read_version(self.review, sha("other")))
        self.assertIsNone(place.read_version(self.review, "../../etc/passwd"))

    def test_scan_rows_unchanged_and_place_matches_service(self):
        scan = subprocess.run(["sh", str(WATCH), str(self.root), ".cursor-owner", "0", "1"],
                              text=True, capture_output=True, check=True)
        self.assertEqual(scan.stdout, "%s\t100-comment-u1.json\n%s\t200-handoff-h1.json\n" % (self.spec, self.spec))
        expected = place.resolve(comment(), REWRITTEN, V1)
        self.assertEqual(scan.stderr, "place\t100-comment-u1.json\t%s\t#%s\t%d-%d\t%s\n" % (
            expected["state"], expected["anchorId"], expected["start"], expected["end"], expected["quote"]))

        _serve = importlib.util.spec_from_file_location("review_place_serve", ASSETS / "review-serve.py")
        serve = importlib.util.module_from_spec(_serve)
        _serve.loader.exec_module(serve)
        server = serve.ReviewThreadingHTTPServer(("127.0.0.1", 0), serve.MountHandler)
        server.mount_state = serve.MountState([serve._single_mount(self.root / "docs")])
        server.state_dir = str(self.root / "state")
        server.wake_controller = serve.WakeController(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = "http://127.0.0.1:%d/api/events?dir=specs/demo.spec.html.review" % server.server_port
            with urllib.request.urlopen(url, timeout=10) as response:
                events = json.loads(response.read())
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual([event.get("place") for event in events], [expected, None])


if __name__ == "__main__":
    unittest.main()
