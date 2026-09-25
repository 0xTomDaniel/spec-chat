import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("jev_audience_test", ROOT / "tools" / "jev.py")
jev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jev)


class AudienceBuilderTest(unittest.TestCase):
    def test_question_set_has_reader_labels(self):
        data = json.loads((ROOT / "skill/review-spec/assets/jev/audience.json").read_text())
        self.assertEqual(data["id"], "audience")
        self.assertEqual({item["name"] for item in data["labels"]}, {"for you", "internals"})
        self.assertTrue(all(item["examples"] for item in data["labels"]))

    def test_builder_only_emits_leaf_anchors(self):
        source = """
        <section data-anchor="section">
          <p data-anchor="reader">A clause for the person using the result.</p>
          <div data-anchor="wrapper"><p data-anchor="nested">An inner clause.</p></div>
        </section>
        """
        questions = jev.build_audience_questions(source, "spec.html", "base", "head")
        self.assertEqual([item["id"] for item in questions], ["reader", "nested"])
        self.assertEqual(questions[0]["state"]["reader"], "someone who uses the result, not builds it")
        self.assertEqual(questions[0]["target"], "reader")

    def test_service_only_adds_audience_in_reading_view(self):
        service = object.__new__(jev.JevService)
        service.question_sets = jev.load_question_sets(ROOT / "skill/review-spec/assets/jev")
        mount = {"root": str(ROOT)}
        target = str(ROOT / "docs/specs/jev-suggestions.spec.html")
        ordinary = service.questions(mount, target, "docs/specs/jev-suggestions.spec.html", "fb976cd", [])
        reading = service.questions(mount, target, "docs/specs/jev-suggestions.spec.html", "fb976cd", [], "reading")
        self.assertNotIn("audience", {item["kind"] for item in ordinary})
        self.assertIn("audience", {item["kind"] for item in reading})


if __name__ == "__main__":
    unittest.main()
