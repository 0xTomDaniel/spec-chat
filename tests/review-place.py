"""Mark resolver (review-state #anchoring, #model-place): pure functions over fixture spec versions and spools."""

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import time
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
                                  "quote": SENTENCE, "state": "kept", "key": None})
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

    def test_element_key_follows_the_element_when_a_peer_is_inserted_before_it(self):
        v1 = '<section data-anchor="b">\n<p>one</p>\n<p>two</p>\n</section>\n'
        current = v1.replace("<p>one</p>", "<p>new</p>\n<p>one</p>")
        result = place.resolve(comment("", anchor="b", target={"type": "element", "key": "p[1]"}, version=v1), current, v1)
        self.assertEqual((result["anchorId"], result["state"], result["key"]), ("b", "kept", "p[2]"))
        self.assertEqual(current[result["start"]:result["end"]], "<p>one</p>")
        ident = '<section data-anchor="b">\n<p id="x">one</p>\n</section>\n'
        moved = ident.replace('<p id="x">', '<p>new</p>\n<p id="x">')
        kept = place.resolve(comment("", anchor="b", target={"type": "element", "key": "p#x"}, version=ident), moved, ident)
        self.assertEqual(kept["key"], "p#x")
        svg = '<figure data-anchor="f">\n<svg><g><path d="1"/></g></svg>\n</figure>\n'
        grown = svg.replace("<g>", "<g><path d=\"0\"/>")
        path = place.resolve(comment("", anchor="f", target={"type": "element", "key": "svg[1]/g[1]/path[1]"}, version=svg), grown, svg)
        self.assertEqual(path["key"], "svg[1]/g[1]/path[2]")
        self.assertIsNone(place.resolve(comment(), INSERTED, V1)["key"], "a text mark carries its quote, not a key")

    def test_event_without_anchor_has_no_place(self):
        self.assertIsNone(place.resolve({"event": "handoff", "anchorId": "", "target": None}, V1, None))

    def test_replies_and_edits_have_no_place(self):
        """A thread is placed by its root comment; an edit copying a stale quote can never show gone."""
        edit = dict(comment(version=REWRITTEN), id="e1", event="edit", supersedes="u1", threadId="u1")
        reply = dict(comment(version=REWRITTEN), id="u2", event="reply", respondsTo="a1", threadId="u1", quote=None)
        self.assertIsNone(place.resolve(edit, REWRITTEN, REWRITTEN))
        self.assertIsNone(place.resolve(reply, REWRITTEN, REWRITTEN))
        self.assertEqual(place.resolve(comment(), REWRITTEN, V1)["state"], "changed")

    def test_large_unequal_line_count_rewrite_places_in_bounded_time(self):
        """#model-place: cost stays near linear in spec size; a 600 to 550 line rewrite once took 34s."""
        def spec(lines):
            return ('<article class="spec">\n<section data-anchor="big">\n' + "".join(
                '<p data-anchor="p%d">%s</p>\n' % (i, line) for i, line in enumerate(lines)) + "</section>\n</article>\n")
        old_lines = ["Line %d says the review keeps marks where a reviewer put them, item %d." % (i, i * 7) for i in range(600)]
        new_lines = ["Row %d now reads quite differently: the agent rewrote part %d here." % (i, i * 3) for i in range(550)]
        old, new = spec(old_lines), spec(new_lines)
        self.assertGreater(len(old), 30000)
        started = time.monotonic()
        result = place.resolve(comment(old_lines[300], anchor="p300", version=old), new, old)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 3.0, "placement took %.1fs" % elapsed)
        self.assertIn(result["state"], ("changed", "gone"))
        half = spec(old_lines[:300] + ["Inserted %d." % i for i in range(40)] + [line + " Edited." for line in old_lines[300:]])
        started = time.monotonic()
        moved = place.resolve(comment(old_lines[450], anchor="p450", version=old), half, old)
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertEqual((moved["anchorId"], moved["state"], moved["quote"]), ("p490", "kept", old_lines[450]),
                         "every quoted character survives the edited, shifted line")

    def test_edited_line_beside_inserted_line_is_changed_not_gone(self):
        old = '<section data-anchor="s">\n<p data-anchor="a">Alpha beta gamma delta.</p>\n<p data-anchor="z">End.</p>\n</section>\n'
        new = old.replace("Alpha beta gamma delta.</p>", "Alpha beta gamma epsilon.</p>\n<p data-anchor=\"n\">New paragraph.</p>")
        result = place.resolve(comment("Alpha beta gamma delta.", anchor="a", version=old), new, old)
        self.assertEqual((result["anchorId"], result["state"]), ("a", "changed"))

    def test_untouched_sentence_of_common_words_in_rewritten_line_is_kept(self):
        """Popular-token junking once dropped every token of an untouched sentence built of common words."""
        s1 = "A reviewer marks the text of the spec, and the page keeps the mark on the text of the spec for the reviewer."
        s2 = "The mark stays on the text of the page."
        rest = ("The page shows the mark on the text, and the mark of the page stays on the text of the spec the page shows. "
                "The text of the mark is the text of the page, and the page of the mark is the page of the text the mark stays on.")
        for repeat in (1, 12):
            old = ('<section data-anchor="s">\n<p data-anchor="a">%s %s %s</p>\n<p data-anchor="z">End.</p>\n</section>\n'
                   % (s1, s2, " ".join([rest] * repeat)))
            new = old.replace(s1, "An agent rewrote the first sentence of the page so the text of the mark on the page reads new text.").replace(
                '</p>\n<p data-anchor="z">', '</p>\n<p data-anchor="n">A new paragraph the agent added.</p>\n<p data-anchor="z">')
            result = place.resolve(comment(s2, anchor="a", version=old), new, old)
            self.assertEqual((result["anchorId"], result["state"], result["quote"]), ("a", "kept", s2), repeat)

    def test_text_mark_is_located_by_its_quote_not_its_40_char_key(self):
        """The page's text key is the selection's first 40 characters; the quote is the whole selection."""
        first = "The export button downloads a CSV file of the rows in the current table, with one header row."
        second = "The export button downloads a CSV file of the totals only."
        self.assertEqual(first[:40], second[:40])
        old = '<section data-anchor="s">\n<p data-anchor="a">%s %s</p>\n</section>\n' % (first, second)
        mark = comment(second, anchor="a", version=old, target={"type": "text", "key": second[:40]})
        same = place.resolve(mark, old, old)
        self.assertEqual((same["state"], same["quote"]), ("kept", second), "the second sentence, not the first")
        rewritten = old.replace("with one header row.", "and a footer with grand totals.")
        mark = comment(first, anchor="a", version=old, target={"type": "text", "key": first[:40]})
        changed = place.resolve(mark, rewritten, old)
        self.assertEqual(changed["state"], "changed", "a rewrite past the key's 40 characters changes the mark")
        legacy = comment(first, anchor="a", version=old, target={"type": "text", "key": first[:40]})
        legacy["quote"] = None
        self.assertEqual(place.resolve(legacy, old, old)["quote"], first[:40], "no quote: the key locates")

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

    def test_repeat_poll_of_many_text_marks_is_cheap(self):
        """#model-place: a page polls often; place is resolved once per event and spec text."""
        blocks = "".join('<p data-anchor="b%d">Block %d says %s</p>\n' % (i, i, "word " * 30) for i in range(600))
        old = '<article>\n' + blocks + '</article>\n'
        current = old.replace('<p data-anchor="b0">', '<p data-anchor="new">Inserted.</p>\n<p data-anchor="b0">')
        self.assertGreater(len(current), 99000)
        names = []
        for i in range(100):
            body = comment("Block %d says word" % (i * 6), anchor="b%d" % (i * 6), version=old)
            body["id"] = "u%d" % i
            names.append(("%03d-comment-u%d.json" % (i, i), body))
        spec, review = spool(self.root / "perf", current, [old], names)
        reads = []
        real = place.read_version
        place.read_version = lambda *args: reads.append(args) or real(*args)
        try:
            events = [{"name": name, "body": body} for name, body in names]
            began = time.process_time()
            place.resolve_events(str(spec), events)
            first = time.process_time() - began
            began = time.process_time()
            place.resolve_events(str(spec), [{"name": name, "body": body} for name, body in names])
            again = time.process_time() - began
        finally:
            place.read_version = real
        self.assertTrue(all(event["place"]["state"] == "kept" for event in events))
        self.assertLessEqual(len(reads), 1, "each version file is read once")
        self.assertLess(first, 1.5)
        self.assertLess(again, 0.05)


if __name__ == "__main__":
    unittest.main()
