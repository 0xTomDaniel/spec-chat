import importlib.util
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SET = ROOT / "skill/review-spec/assets/jev/audience.json"
SPEC = ROOT / "docs/specs/jev-suggestions.spec.html"
spec = importlib.util.spec_from_file_location("jev_audience_test", ROOT / "tools" / "jev.py")
jev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jev)


class AlwaysInternals:
    def __init__(self):
        self.calls = []

    def decide(self, payload):
        self.calls.append(payload)
        return {"answers": {"audience": {"choice": "internals", "confidence": 0.9}}}


def _section_anchors(source, section):
    match = re.search(r'<section data-spec-section="%s".*?</section>' % section, source, re.S)
    return set(re.findall(r'data-anchor="([^"]+)"', match.group(0))) if match else set()


class AudienceSetTest(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(SET.read_text(encoding="utf-8"))
        self.labels = {item["name"]: item for item in self.data["labels"]}

    def test_set_parses_with_reader_labels(self):
        self.assertEqual(self.data["id"], "audience")
        self.assertGreaterEqual(self.data["version"], 4)
        self.assertEqual(set(self.labels), {"for you", "internals"})
        for label in self.labels.values():
            for example in label["examples"]:
                self.assertEqual(set(example["input"]), {"clause"})
                self.assertEqual(example["label"], label["name"])

    def test_labels_are_described_per_definition(self):
        mine = self.labels["for you"]["description"].lower()
        for term in ("user-facing", "behavioral", "architecture", "boundary", "dependency direction", "interface"):
            self.assertIn(term, mine)
        other = self.labels["internals"]["description"].lower()
        for term in ("everything else", "implementation detail", "mechanics", "proofs", "fixture or test detail",
                     "minor structure"):
            self.assertIn(term, other)
        instructions = self.data["instructions"].lower()
        for term in ("user-facing", "behavioral", "architecture", "implementation detail", "acceptance criterion"):
            self.assertIn(term, instructions)
        self.assertNotIn("someone who uses the result", json.dumps(self.data))

    def test_examples_cover_each_kind(self):
        clauses = {label: [example["input"]["clause"] for example in item["examples"]]
                   for label, item in self.labels.items()}
        kinds = {
            "for you": {
                "user-facing": "never covers the spec or review controls",
                "behavioral": "handed-off events remain durably queued",
                "key architecture": "Spec Chat depends on no tracker or orchestrator",
                "typical criterion": "When the reviewer pastes a valid commit id",
            },
            "internals": {
                "implementation detail": "user-owned absolute runtime namespace",
                "proof or test detail": "Smallest first failing test",
                "proof mechanics criterion": "html checks pass unchanged",
                "minor structure": "headDate, baseDate",
            },
        }
        for label, wanted in kinds.items():
            for kind, fragment in wanted.items():
                with self.subTest(label=label, kind=kind):
                    self.assertTrue(any(fragment in clause for clause in clauses[label]))

    def test_examples_reuse_no_text_of_any_qa_fixture_revision(self):
        # The QA fixture is built from this spec's history; no example may appear in any revision of it.
        revisions = subprocess.check_output(
            ("git", "-C", str(ROOT), "log", "--format=%H", "--", str(SPEC.relative_to(ROOT))), text=True).split()
        blobs = [SPEC.read_text(encoding="utf-8")]
        for revision in revisions:
            blobs.append(subprocess.check_output(
                ("git", "-C", str(ROOT), "show", revision + ":" + str(SPEC.relative_to(ROOT))),
                text=True, stderr=subprocess.DEVNULL))
        corpus = "\n".join(" ".join(re.sub(r"<[^>]+>", " ", blob).split()) for blob in blobs)
        for label in self.labels.values():
            for example in label["examples"]:
                clause = " ".join(example["input"]["clause"].split())
                with self.subTest(clause=clause[:60]):
                    self.assertNotIn(clause, corpus)


class AudienceBuilderTest(unittest.TestCase):
    def test_builder_only_emits_leaf_anchors(self):
        source = """
        <section data-anchor="section">
          <p data-anchor="reader">A clause for the person using the result.</p>
          <div data-anchor="wrapper"><p data-anchor="nested">An inner clause.</p></div>
        </section>
        """
        questions = jev.build_audience_questions(source, "spec.html", "base", "head")
        self.assertEqual([item["id"] for item in questions], ["reader", "nested"])
        self.assertEqual(questions[0]["state"], {"clause": "A clause for the person using the result."})
        self.assertEqual(questions[0]["target"], "reader")

    def test_stories_and_boundaries_are_never_asked_criteria_are(self):
        source = """
        <section data-spec-section="user-stories" data-anchor="user-stories">
          <h2 data-anchor="stories-title">User stories</h2>
          <p data-user-story data-anchor="story-one">As a reader, I see the flag.</p>
        </section>
        <section data-spec-section="acceptance" data-anchor="acceptance">
          <p data-acceptance-criterion data-anchor="criterion-one">When the page loads, the flag shows.</p>
        </section>
        <section data-spec-section="modular-boundaries" data-anchor="modular-boundaries">
          <table><tbody><tr data-anchor="boundary-one"><td>Server</td><td>Owns the route.</td></tr></tbody></table>
        </section>
        <section data-anchor="behavior"><p data-anchor="rule-one">The flag is red.</p></section>
        """
        ids = [item["id"] for item in jev.build_audience_questions(source, "spec.html", "base", "head")]
        self.assertEqual(ids, ["criterion-one", "rule-one"])

    def test_real_spec_stories_and_boundaries_render_undimmed(self):
        # Runtime dims only clauses the route labels internals; structural clauses get no item at all.
        source = SPEC.read_text(encoding="utf-8")
        structural = _section_anchors(source, "user-stories") | _section_anchors(source, "modular-boundaries")
        criteria = set(re.findall(r'data-acceptance-criterion data-anchor="([^"]+)"', source))
        self.assertTrue(structural and criteria)
        provider = AlwaysInternals()
        with tempfile.TemporaryDirectory() as directory:
            service = jev.JevService(state_dir=directory, provider=provider, api_key="fake")
            questions = jev.build_audience_questions(source, "spec.html", "base", "head")
            service.questions = lambda *args, **kwargs: questions
            result = service.response({}, "", "", "base", [], "reading")
        items = {item["id"]: item for item in result["items"] if item["kind"] == "audience"}
        self.assertFalse(structural & set(items))
        self.assertTrue(criteria <= set(items))
        self.assertTrue(all(items[anchor]["label"] == "internals" for anchor in criteria))
        self.assertEqual(len(provider.calls), len(items))

    def test_service_only_adds_audience_in_reading_view(self):
        service = object.__new__(jev.JevService)
        service.question_sets = jev.load_question_sets(ROOT / "skill/review-spec/assets/jev")
        mount = {"root": str(ROOT)}
        target = str(SPEC)
        ordinary = service.questions(mount, target, "docs/specs/jev-suggestions.spec.html", "fb976cd", [])
        reading = service.questions(mount, target, "docs/specs/jev-suggestions.spec.html", "fb976cd", [], "reading")
        self.assertNotIn("audience", {item["kind"] for item in ordinary})
        self.assertIn("audience", {item["kind"] for item in reading})


if __name__ == "__main__":
    unittest.main()
